#!/usr/bin/env bash
# Read-only operational snapshot of the owner-controlled Codespace state.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
python3 -m system_proxy.observability --days 14 --format json
# Lot 3 operational status is also read-only. One document per JSON line.
python3 -m system_proxy.ops report
