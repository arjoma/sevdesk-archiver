# SevDesk Archiver

Standalone Python tool that builds a **self-contained local archive** of your SevDesk documents (invoices, credit notes, vouchers). Each document is stored as a PDF plus a JSON sidecar with the full SevDesk metadata, and the archive ships its own standalone HTML viewer — no database, no server required.

- **Idempotent** — re-running only fetches what is missing or changed
- **Self-serving** — the archive directory contains a static `index.html` viewer and a standalone `serve.py` (Python stdlib only)
- **Type-aware** — invoices, credit notes, and vouchers with correct status/type labels (German)
- **Resilient** — exponential backoff on rate limits and transient network errors
- **No DB** — plain files on disk, easy to back up, inspect, or diff

## Install

```bash
uv tool install sevdesk-archiver
# or from source:
uv pip install -e .
```

## Configure

Copy `.env.example` to `.env` and fill in your values, or export the variables in your shell:

```bash
SEVDESK_API_TOKEN=your_sevdesk_token
ARCHIVE_TARGET=$HOME/Documents/sevdesk-archive
```

Get your SevDesk API token from <https://my.sevdesk.de/admin/userManagement> (Benutzer → API-Token).

## Usage

```bash
# Build / refresh the archive (default range: 1st of previous month … today)
sevdesk-archiver archive

# Limit the date range
sevdesk-archiver archive --after 2026-01-01 --end 2026-03-31

# Include vouchers (incoming invoices)
sevdesk-archiver archive --vouchers

# See what would happen — no files written
sevdesk-archiver archive --dry-run

# Serve the archive over HTTP and open the browser (index.html needs http://)
sevdesk-archiver serve

# Verify manifest <-> files consistency
sevdesk-archiver verify
```

`--target <dir>` overrides `ARCHIVE_TARGET` on any command.

## Archive layout

```
$ARCHIVE_TARGET/
├── index.html          # viewer (loads manifest.json via fetch)
├── manifest.json       # summary of all entries
├── serve.py            # standalone HTTP server (stdlib only)
├── serve-archive.sh    # wrapper: ./serve-archive.sh
└── files/
    ├── inv-20260115-RE-2026_0001-Mustermann_GmbH.pdf
    ├── inv-20260115-RE-2026_0001-Mustermann_GmbH.json
    └── …
```

The `files/` subdirectory is the authoritative store. `manifest.json` is regenerated on every run and is safe to delete — it'll be rebuilt from the sidecars. The `index.html` / `serve.py` helpers are copies of the shipped templates; you can re-run `sevdesk-archiver archive` at any time to refresh them.

Once archived, the folder is self-contained. You can copy it anywhere (USB stick, S3, attached storage) and open it with:

```bash
cd /path/to/archive
./serve-archive.sh            # or: python3 serve.py
```

No `pip install`, no `sevdesk-archiver`, no SevDesk API access required to browse — just Python 3's standard library.

## Library use

```python
from sevdesk_archiver import SevDeskClient, archive, verify_archive

client = SevDeskClient(api_token=...)
for event in archive(client, target_dir="/path/to/archive"):
    print(event["message"])

report = verify_archive("/path/to/archive")
```

## Development

```bash
uv sync
uv run pytest
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
