#!/usr/bin/env bash
# Read-only health, backups and bounded historical retention preview.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m system_proxy.ops report
