# SevDesk Archiver

[![PyPI version](https://img.shields.io/pypi/v/sevdesk-archiver)](https://pypi.org/project/sevdesk-archiver/)
[![CI](https://github.com/arjoma/sevdesk-archiver/actions/workflows/ci.yaml/badge.svg)](https://github.com/arjoma/sevdesk-archiver/actions/workflows/ci.yaml)
[![Python](https://img.shields.io/python/required-version-toml?tomlFilePath=https://raw.githubusercontent.com/arjoma/sevdesk-archiver/main/pyproject.toml)](https://pypi.org/project/sevdesk-archiver/)
[![License](https://img.shields.io/pypi/l/sevdesk-archiver)](https://github.com/arjoma/sevdesk-archiver/blob/main/LICENSE)

Standalone Python tool that builds a **self-contained local archive** of your SevDesk documents (invoices, credit notes, vouchers). Each document is stored as a PDF plus a JSON sidecar with the full SevDesk metadata, and the archive ships its own standalone HTML viewer — no database, no server required.

- **Idempotent** — re-running only fetches what is missing or changed
- **Self-serving** — the archive directory contains a static `index.html` viewer and a standalone `serve.py` (Python stdlib only)
- **Type-aware** — invoices, credit notes, and vouchers with correct status/type labels (German)
- **Resilient** — exponential backoff on rate limits and transient network errors
- **Integrity-checked** — SHA-256 `pdf_hash` per document; `verify` cross-checks manifest, files, sidecars, and hashes
- **No DB** — plain files on disk, easy to back up, inspect, or diff

## Run it

No install needed — [`uvx`](https://docs.astral.sh/uv/guides/tools/) fetches and runs the latest release in an ephemeral environment:

```bash
uvx sevdesk-archiver@latest --help
```

Or install persistently:

```bash
uv tool install sevdesk-archiver
sevdesk-archiver --help
```

## Configure

Two things are needed for the `archive` command: a SevDesk API token and a target directory. Any of the following works:

**1. Command-line arguments** — highest precedence:

```bash
uvx sevdesk-archiver@latest archive \
  --api-token "$SEVDESK_API_TOKEN" \
  --target /path/to/archive
```

**2. Environment variables** — export once, run anywhere:

```bash
export SEVDESK_API_TOKEN=your_sevdesk_token
export ARCHIVE_TARGET=$HOME/Documents/sevdesk-archive
uvx sevdesk-archiver@latest archive
```

**3. `.env` file in the working directory** — auto-loaded on startup:

```
# .env
SEVDESK_API_TOKEN=your_sevdesk_token
ARCHIVE_TARGET=.
```

```bash
cd /path/to/archive && uvx sevdesk-archiver@latest archive
```

Get your SevDesk API token from <https://my.sevdesk.de/admin/userManagement> (Benutzer → API-Token).

### One-line in-place refresh

Drop a `.env` (with `ARCHIVE_TARGET=.` and your token) into an archive directory, then refresh it with a single line from inside that directory:

```bash
cd /path/to/archive && uvx sevdesk-archiver@latest archive
```

Or as a cron / systemd-timer job:

```bash
cd /path/to/archive && uvx sevdesk-archiver@latest archive && uvx sevdesk-archiver@latest verify
```

## Commands

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

# Deep integrity check: manifest ↔ files ↔ sidecars ↔ hashes
sevdesk-archiver verify

# Add SHA-256 pdf_hash to sidecars that lack it (existing archives)
sevdesk-archiver verify --backfill-hashes
```

`--target <dir>` overrides `ARCHIVE_TARGET` on any command.

## Archive layout

```
$ARCHIVE_TARGET/
├── index.html          # viewer (loads manifest.json via fetch)
├── manifest.json       # summary of all entries
├── logo.png
├── serve.py            # standalone HTTP server (stdlib only)
├── serve-archive.sh    # wrapper: ./serve-archive.sh
└── files/
    ├── inv-20260115-RE-2026_0001-Mustermann_GmbH.pdf
    ├── inv-20260115-RE-2026_0001-Mustermann_GmbH.json
    └── …
```

The `files/` subdirectory is the authoritative store. `manifest.json` is regenerated on every run and is safe to delete — it'll be rebuilt from the sidecars. The `index.html` / `serve.py` helpers are copies of the shipped templates; you can re-run `sevdesk-archiver archive` at any time to refresh them.

Each sidecar carries the full SevDesk document, archive metadata, and a SHA-256 `pdf_hash` (`"sha256:<hex>"`) so `verify` can catch silent corruption.

Once archived, the folder is self-contained. You can copy it anywhere (USB stick, S3, attached storage) and open it with:

```bash
cd /path/to/archive
./serve-archive.sh            # or: python3 serve.py
```

No `pip install`, no `sevdesk-archiver`, no SevDesk API access required to browse — just Python 3's standard library.

## Library use

```python
from sevdesk_archiver import SevDeskClient, verify_archive
from sevdesk_archiver.archive import archive

client = SevDeskClient(api_token="...")
for event in archive(client, target_dir="/path/to/archive"):
    print(event["message"])

report = verify_archive("/path/to/archive")
```

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
uv run mypy src
```

See [CLAUDE.md](CLAUDE.md) for the release process and project conventions, and [CHANGELOG.md](CHANGELOG.md) for version history.

## License

Apache-2.0 — see [LICENSE](LICENSE).
