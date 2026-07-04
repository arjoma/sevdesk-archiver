# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- README section on running headless (cron / systemd): periodic full-history sweep to catch backdated or late-booked documents, `flock` against overlapping runs, version pinning, and keeping the API token outside the archive directory.
- Filename collision handling: when two different documents generate the same archive filename (e.g. two vouchers with identical date, description, and supplier), the later one gets `-<sevdesk_id>` appended instead of being silently skipped and never downloaded.
- Zero-byte PDFs on disk are treated as missing and re-downloaded.
- Malformed JSON sidecars (e.g. from a crash mid-write) are rewritten on the next `archive` run instead of being left broken forever.
- Test suite for `SevDeskClient`: pagination, date-window widening and client-side filtering, all `getPdf` response formats, 404 → `DocumentNotFoundError`, and 429 / retry-exhaustion → `RateLimitExceededError` mapping.

### Fixed
- Re-downloading a missing PDF now always refreshes the sidecar, so `pdf_hash` matches the bytes on disk even when SevDesk returns a byte-different render. Previously the sidecar kept the old hash and `verify` reported a spurious mismatch.
- All archive writes (PDFs, sidecars, `manifest.json`, hash backfill) now go through a temp file + atomic rename, so an interrupted run can never leave a truncated file at the final path — which previously could get its truncated hash recorded as ground truth on the next run.

## [0.1.1] - 2026-04-21

### Added
- `archive --api-token` CLI flag as an alternative to `SEVDESK_API_TOKEN` / `.env`.

### Changed
- README: lead with `uvx sevdesk-archiver@latest`; document env vars, CLI args, and `.env` as three equivalent ways to configure. Add project badges (PyPI, CI, Python version, License).
- Release workflow: upload only `*.whl` and `*.tar.gz` to GitHub Releases (dotfiles like `.gitignore` no longer appear as release assets).

### Fixed
- `.env` lookup now starts from the current working directory, so `uvx sevdesk-archiver@latest` picks up a `.env` next to where the user runs it. Previously `load_dotenv()` searched from the installed package path, so only a `.env` alongside the source was ever found.

## [0.1.0] - 2026-04-21

First public release.

### Added
- `archive` command: idempotent, month-chunked fetch of invoices, credit notes, and (optionally) vouchers from SevDesk; writes PDFs and JSON sidecars into `$ARCHIVE_TARGET/files/`.
- `serve` command: local HTTP viewer on `127.0.0.1:8765` that loads the archive's `manifest.json`.
- `verify` command: deep integrity check covering manifest ↔ files consistency, PDF ↔ JSON pairing, sidecar shape validation, manifest ↔ sidecar cross-check, duplicate `sevdesk_id` detection, and SHA-256 hash verification.
- SHA-256 `pdf_hash` recorded in every sidecar (`"sha256:<hex>"`); automatic carry-over on metadata refresh; falls back to hashing the on-disk PDF when writing a new sidecar without a known hash.
- `verify --backfill-hashes` to add hashes to sidecars written before this feature existed.
- `verify --delete-orphans` to remove unreferenced `files/*.pdf` and `files/*.json` (with confirmation).
- `verify --format json` for machine-readable output.
- Drafts (Invoice/CreditNote status <200, Voucher status <100) are never archived.
- Self-contained HTML viewer + logo + stdlib-only `serve.py` / `serve-archive.sh` bundled and copied into each archive.
- Rate-limit + transient-error retry with exponential backoff in the SevDesk client.
- GitHub Actions CI (pytest, ruff, mypy on Python 3.13 via uv).
- GitHub Actions release workflow: PyPI publish via trusted publishing on `v*.*.*` tags, with tag-vs-pyproject version verification and automatic GitHub Release creation.

[Unreleased]: https://github.com/arjoma/sevdesk-archiver/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/arjoma/sevdesk-archiver/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/arjoma/sevdesk-archiver/releases/tag/v0.1.0
