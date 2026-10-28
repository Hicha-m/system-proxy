#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
# In persistent execution HOME is the Codespace user's home, not a
# disposable job directory. Override SYSTEM_PROXY_DATA_DIR if desired.
DATA_DIR="${SYSTEM_PROXY_DATA_DIR:-${HOME}/.local/share/system-proxy}"
mkdir -p "$DATA_DIR"
exec python3 -m system_proxy.campaign --db "$DATA_DIR/campaign.sqlite3" --max-checks 10000 --timeout 2 --workers 12 --batch-size 120 --runtime-seconds 20400
