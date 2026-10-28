#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m system_proxy.campaign --db "${HOME}/.local/share/system-proxy/campaign.sqlite3" --max-checks 10000 --timeout 2 --workers 12 --batch-size 120 --runtime-seconds 780
