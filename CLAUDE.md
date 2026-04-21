# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repository. For end-user documentation, see [README.md](README.md).

## What this is

`sevdesk-archiver` is a standalone Python CLI that builds an idempotent local archive of SevDesk documents (invoices, credit notes, vouchers) as PDFs plus full-metadata JSON sidecars, with a self-contained HTML viewer and deep integrity checking. Published on PyPI, Apache-2.0 licensed.

## Commands

All project operations go through `uv`. Never `pip install` into this project.

```bash
uv sync                              # install deps into .venv (matches uv.lock)
uv sync --locked                     # CI mode: fail if pyproject drifted from lock
uv run pytest -q                     # run tests (hermetic — no SevDesk API calls)
uv run ruff check src tests          # lint
uv run mypy src                      # type check
uv build                             # build sdist + wheel into dist/

uv run sevdesk-archiver --help       # CLI entry point
uv run sevdesk-archiver archive      # archive invoices/credit notes into $ARCHIVE_TARGET
uv run sevdesk-archiver verify       # deep integrity report
uv run sevdesk-archiver serve        # http://127.0.0.1:8765 viewer
```

Environment: `SEVDESK_API_TOKEN` and `ARCHIVE_TARGET` come from `.env` (gitignored) or the shell. See `.env.example`.

## Layout

```
src/sevdesk_archiver/
├── archive.py          # engine: month-chunked fetch, idempotent writes, hash + shape + cross-check logic, verify_archive, backfill_sidecar_hashes
├── cli.py              # click group: archive, serve, verify
├── sevdesk.py          # SevDeskClient: requests-based, retry/backoff, rate-limit aware
├── utils.py            # logging, session factory, filename sanitizer
├── exceptions.py       # typed errors (DocumentNotFoundError, RateLimitExceededError, …)
└── templates/          # bundled with the wheel, copied into each archive
    ├── archive_index.html
    ├── logo.png
    ├── serve.py
    └── serve-archive.sh

tests/                  # pytest; mocks SevDeskClient, writes to tests/_tmp_*/ dirs (gitignored)
.github/workflows/
├── ci.yaml             # push to main + PR: pytest, ruff, mypy on 3.13
└── release.yaml        # v*.*.* tag: verify + build + publish to PyPI + GitHub Release
```

## Conventions

- **Python ≥3.11**. Code uses `tomllib`, `X | None`, etc.
- **`archive` submodule vs function**: `from sevdesk_archiver import archive` resolves to the **submodule**, not the function. For the function use `from sevdesk_archiver.archive import archive`.
- **Sidecar format** is append-compatible: new optional fields (like `pdf_hash`) must not break older archives. `ARCHIVE_VERSION` stays at 1 until a schema break.
- **Hash format**: `"sha256:<hex>"` (lowercase, colon-separated, self-describing — future-proof for other algorithms).
- **Tests must be hermetic**: no network, no `.env`, use `MagicMock` for `SevDeskClient`.
- Keep `uv.lock` committed and in sync with `pyproject.toml` — CI runs `uv sync --locked`.

## CHANGELOG

`CHANGELOG.md` is required for every release — no version bump without a corresponding entry. Follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/): group changes under `### Added`, `### Changed`, `### Fixed`, `### Removed`, `### Security`.

## Release Process (PyPI)

Releases are triggered by pushing a `v*` tag. GitHub Actions builds and publishes via OIDC trusted publishing — no API token needed.

### One-time PyPI setup (already done after first release)
1. pypi.org → Account Settings → Publishing → "Add a new pending publisher"
   - Project: `sevdesk-archiver`, Owner: `arjoma`, Repo: `sevdesk-archiver`
   - Workflow: `release.yaml`, Environment: `pypi`
2. GitHub repo → Settings → Environments → create `pypi` (empty is fine)

### Per-release steps
```bash
# 1. Update CHANGELOG.md — move items from [Unreleased] into a new [X.Y.Z]
#    section with today's date, and add new compare links at the bottom.
#    Follows Keep a Changelog conventions.
# 2. Bump version in pyproject.toml (e.g. 0.1.0 → 0.2.0)
# 3. MANDATORY: sync lockfile — uv.lock must match pyproject.toml version.
#    PyPI rejects re-uploads of deleted versions, so forgetting this
#    forces a patch bump (e.g. v0.5.0 → v0.5.1).
uv sync

# 4. Commit — uv.lock MUST be included in the same commit as the version bump
git add CHANGELOG.md pyproject.toml uv.lock
git commit -m "Release v0.2.0"

# 5. Tag and push — this triggers the GitHub Actions release workflow
git tag v0.2.0
git push origin main v0.2.0
```

**CHANGELOG.md is required for every release** — no version bump without a corresponding entry. Group changes under `### Added`, `### Changed`, `### Fixed`, `### Removed` as appropriate.

The workflow (`.github/workflows/release.yaml`) runs on any `v*.*.*` tag, verifies the tag matches `pyproject.toml`, runs tests + ruff + mypy, builds with `uv build`, publishes to PyPI via trusted publishing, and creates a GitHub Release with the dist files attached.
