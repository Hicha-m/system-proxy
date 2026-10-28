#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
DATA_DIR="${SYSTEM_PROXY_DATA_DIR:-${HOME}/.local/share/system-proxy}"
exec python3 -m system_proxy.campaign_report --db "$DATA_DIR/campaign.sqlite3" --limit 20
