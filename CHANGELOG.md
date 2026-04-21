# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/arjoma/sevdesk-archiver/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/arjoma/sevdesk-archiver/releases/tag/v0.1.0
