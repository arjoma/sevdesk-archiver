import json
import logging
import os
import sys

import click
from dotenv import find_dotenv, load_dotenv

from . import archive as archive_mod
from .sevdesk import SevDeskClient
from .utils import setup_logging


def _resolve_target(target: str | None) -> str:
    target_dir = target or os.getenv("ARCHIVE_TARGET")
    if not target_dir:
        click.echo(
            click.style(
                "Error: No target directory. Use --target or set ARCHIVE_TARGET in .env / env.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(1)
    return os.path.expanduser(os.path.expandvars(target_dir))


def _require_token(override: str | None = None) -> str:
    token = override or os.getenv("SEVDESK_API_TOKEN")
    if not token:
        click.echo(
            click.style(
                "Error: SEVDESK_API_TOKEN is not set. Pass --api-token, export it, "
                "or put it in a .env file.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(1)
    return token


@click.group()
@click.option("--log-file", help="Path to log file for output capture.")
@click.option("--verbose", is_flag=True, help="Enable verbose (debug) logging.")
@click.version_option(package_name="sevdesk-archiver", prog_name="sevdesk-archiver")
def cli(log_file, verbose):
    """SevDesk Archiver — build and serve a local archive of SevDesk documents."""
    load_dotenv(find_dotenv(usecwd=True))
    level = logging.DEBUG if verbose else logging.INFO
    final_log_file = log_file or os.getenv("SEVDESK_ARCHIVER_LOG_FILE")
    setup_logging(final_log_file, level=level)


@cli.command()
@click.option("--target", help="Archive directory (default: $ARCHIVE_TARGET, or '.' falls back to the current directory)")
@click.option(
    "--api-token",
    "api_token",
    envvar="SEVDESK_API_TOKEN",
    help="SevDesk API token (default: $SEVDESK_API_TOKEN)",
)
@click.option("--after", help="Start date YYYY-MM-DD (default: 1st of previous month)")
@click.option("--end", "end_date", help="End date YYYY-MM-DD (default: today)")
@click.option(
    "--status",
    default=None,
    help="SevDesk status filter (default: all non-drafts; drafts are never archived)",
)
@click.option(
    "--credit-notes/--no-credit-notes",
    "credit_notes",
    default=True,
    help="Include Credit Notes (default: yes)",
)
@click.option(
    "--vouchers/--no-vouchers",
    "vouchers",
    default=False,
    help="Include Vouchers / incoming invoices (default: no)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be downloaded without writing files",
)
def archive(target, api_token, after, end_date, status, credit_notes, vouchers, dry_run):
    """Build an idempotent local archive of SevDesk documents.

    Each document gets a PDF plus a JSON sidecar with full metadata. Re-running
    only fetches what is missing or changed. A manifest.json is written for the
    HTML viewer. Drafts (non-sent documents) are never archived.
    """
    target_dir = _resolve_target(target)
    token = _require_token(api_token)

    client = SevDeskClient(api_token=token)

    had_error = False
    for evt in archive_mod.archive(
        client=client,
        target_dir=target_dir,
        after_date=after,
        end_date=end_date,
        status=status,
        include_credit_notes=credit_notes,
        include_vouchers=vouchers,
        dry_run=dry_run,
    ):
        msg = evt["message"]
        t = evt["type"]
        if t == "error":
            had_error = True
            click.echo(click.style(msg, fg="red"), err=True)
        elif t == "success":
            click.echo(click.style(msg, fg="green"))
        elif t == "dry_run":
            click.echo(click.style(msg, fg="blue"))
        elif t == "warning":
            click.echo(click.style(msg, fg="yellow"))
        elif t == "info":
            click.echo(click.style(msg, fg="cyan"))
        else:
            click.echo(msg)

    if had_error:
        sys.exit(1)


@cli.command()
@click.option("--target", help="Archive directory (default: $ARCHIVE_TARGET)")
@click.option("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
@click.option("--port", default=8765, type=int, help="Bind port (default: 8765)")
@click.option("--no-browser", is_flag=True, help="Do not auto-open the browser")
def serve(target, host, port, no_browser):
    """Serve the archive over HTTP so the index.html viewer works.

    index.html loads manifest.json via fetch(), which requires http:// (not file://).
    """
    import http.server
    import socketserver
    import webbrowser
    from functools import partial

    target_dir = _resolve_target(target)

    if not os.path.isdir(target_dir):
        click.echo(
            click.style(f"Error: {target_dir} is not a directory.", fg="red"),
            err=True,
        )
        sys.exit(1)

    archive_mod.install_index_html(target_dir)
    archive_mod.install_logo(target_dir)
    archive_mod.install_serve_scripts(target_dir)

    handler = partial(http.server.SimpleHTTPRequestHandler, directory=target_dir)
    try:
        httpd = socketserver.ThreadingTCPServer((host, port), handler)
    except OSError as e:
        click.echo(click.style(f"Error binding {host}:{port}: {e}", fg="red"), err=True)
        sys.exit(1)

    url = f"http://{host}:{port}/index.html"
    click.echo(click.style(f"Serving {target_dir}", fg="cyan"))
    click.echo(click.style(f"Open: {url}", fg="green", bold=True))
    click.echo("Press Ctrl+C to stop.")

    if not no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        click.echo("\nStopped.")
    finally:
        httpd.server_close()


@cli.command()
@click.option("--target", help="Archive directory (default: $ARCHIVE_TARGET)")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format",
)
@click.option(
    "--delete-orphans",
    is_flag=True,
    help="Delete orphan PDF/JSON files in files/ (needs explicit confirmation)",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip interactive confirmation for destructive operations",
)
@click.option(
    "--no-hashes",
    is_flag=True,
    help="Skip SHA-256 hash verification (faster on large archives)",
)
@click.option(
    "--backfill-hashes",
    is_flag=True,
    help="Hash PDFs on disk and add pdf_hash to sidecars that lack it",
)
def verify(target, output_format, delete_orphans, yes, no_hashes, backfill_hashes):
    """Deep integrity check of the archive.

    Reports missing / orphan files, unpaired PDF↔JSON siblings, malformed
    sidecars, manifest↔sidecar mismatches, duplicate sevdesk_ids, and
    SHA-256 hash mismatches. Exits non-zero when any issue is found.

    With --delete-orphans, orphan files can be removed after confirmation.
    With --backfill-hashes, sidecars lacking pdf_hash get one written from
    the PDF on disk (needs explicit confirmation).
    """
    target_dir = _resolve_target(target)

    if backfill_hashes:
        if not yes:
            click.echo(
                click.style(
                    f"About to add pdf_hash to sidecars in {target_dir}/files/ that lack it.",
                    fg="yellow",
                    bold=True,
                )
            )
            try:
                resp = click.prompt("Type 'yes' to proceed", default="no")
            except click.Abort:
                resp = "no"
            if resp.strip().lower() != "yes":
                click.echo("Cancelled.")
                sys.exit(1)
        br = archive_mod.backfill_sidecar_hashes(target_dir)
        click.echo(
            click.style(
                f"Backfill: updated={br['updated']} skipped={br['skipped']} "
                f"missing_pdf={br['missing_pdf']} errors={br['errors']}",
                fg="green",
                bold=True,
            )
        )

    report = archive_mod.verify_archive(target_dir, check_hashes=not no_hashes)

    if output_format == "json":
        serializable = {
            k: (sorted(v) if isinstance(v, set) else v) for k, v in report.items()
        }
        click.echo(json.dumps(serializable, indent=2, default=str))
        return

    click.echo(f"[*] Archive: {target_dir}")
    if report["errors"]:
        for err in report["errors"]:
            click.echo(click.style(f"  ✗ {err}", fg="red"), err=True)
        sys.exit(1)

    total_files = len(report["files_on_disk"])
    click.echo(
        f"[*] Manifest entries: {report['manifest_count']} "
        f"(no_pdf flagged: {report['no_pdf_count']})"
    )
    click.echo(f"[*] Files on disk:   {total_files}")
    if not no_hashes:
        click.echo(
            f"[*] Hashes: {report['hash_verified']} verified, "
            f"{report['hash_unverified']} without recorded hash, "
            f"{len(report['hash_mismatches'])} mismatched"
        )

    def _list(label, items, color):
        if not items:
            return
        click.echo(click.style(f"\n{label} ({len(items)}):", fg=color, bold=True))
        shown = items[:20] if isinstance(items, list) else sorted(items)[:20]
        for item in shown:
            click.echo(click.style(f"  - {item}", fg=color))
        if len(items) > 20:
            click.echo(click.style(f"  … {len(items) - 20} more", fg=color))

    _list("Missing PDF files (referenced by manifest)", report["missing_pdf"], "yellow")
    _list(
        "Missing JSON sidecars (referenced by manifest)",
        report["missing_json"],
        "yellow",
    )
    _list("Orphan PDF files (on disk, not in manifest)", report["orphan_pdf"], "yellow")
    _list(
        "Orphan JSON sidecars (on disk, not in manifest)",
        report["orphan_json"],
        "yellow",
    )
    _list(
        "Unpaired PDFs (no sibling sidecar)", report["unpaired_pdf"], "yellow"
    )
    _list(
        "Unpaired sidecars (no sibling PDF, not flagged no_pdf)",
        report["unpaired_json"],
        "yellow",
    )
    _list("Malformed sidecars", report["sidecar_errors"], "red")
    if report["manifest_sidecar_mismatches"]:
        items = [
            f"{m['file']}: {m['field']} manifest={m['manifest']!r} sidecar={m['sidecar']!r}"
            for m in report["manifest_sidecar_mismatches"]
        ]
        _list("Manifest↔sidecar mismatches", items, "red")
    if report["duplicate_sevdesk_ids"]:
        items = [f"id={d['id']}: {', '.join(d['files'])}" for d in report["duplicate_sevdesk_ids"]]
        _list("Duplicate sevdesk_ids across sidecars", items, "red")
    if report["hash_mismatches"]:
        items = [
            f"{m['file']}: recorded={m['recorded']} actual={m['actual']}"
            for m in report["hash_mismatches"]
        ]
        _list("PDF hash mismatches (bit rot / tampering)", items, "red")

    issues = (
        len(report["missing_pdf"])
        + len(report["missing_json"])
        + len(report["orphan_pdf"])
        + len(report["orphan_json"])
        + len(report["unpaired_pdf"])
        + len(report["unpaired_json"])
        + len(report["sidecar_errors"])
        + len(report["manifest_sidecar_mismatches"])
        + len(report["duplicate_sevdesk_ids"])
        + len(report["hash_mismatches"])
    )
    if issues == 0:
        click.echo(click.style("\n✓ Archive is consistent.", fg="green", bold=True))
        return

    click.echo(
        click.style(f"\n⚠ {issues} inconsistencies found.", fg="yellow", bold=True)
    )

    if delete_orphans and (report["orphan_pdf"] or report["orphan_json"]):
        to_delete = report["orphan_pdf"] + report["orphan_json"]
        click.echo(
            click.style(
                f"\nAbout to delete {len(to_delete)} orphan file(s) from {target_dir}/files/",
                fg="red",
                bold=True,
            )
        )
        if not yes:
            try:
                resp = click.prompt("Type 'yes' to proceed", default="no")
            except click.Abort:
                resp = "no"
            if resp.strip().lower() != "yes":
                click.echo("Cancelled.")
                sys.exit(1)
        files_dir = os.path.join(target_dir, archive_mod.FILES_SUBDIR)
        deleted = 0
        for name in to_delete:
            path = os.path.join(files_dir, name)
            try:
                os.remove(path)
                deleted += 1
            except OSError as e:
                click.echo(click.style(f"  ! failed to delete {name}: {e}", fg="red"))
        click.echo(click.style(f"Deleted {deleted} file(s).", fg="green"))

    sys.exit(1)


if __name__ == "__main__":
    cli()
