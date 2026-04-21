#!/usr/bin/env bash
# Launch the local viewer for this SevDesk archive.
# Linux / macOS. Requires python3 (standard library only — no dependencies).
set -euo pipefail
cd "$(dirname "$0")"
exec python3 serve.py "$@"
