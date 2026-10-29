#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
DATA_DIR="${SYSTEM_PROXY_DATA_DIR:-${HOME}/.local/share/system-proxy}"
mkdir -p "$DATA_DIR"
exec python3 -m system_proxy.daily \
  --db "$DATA_DIR/campaign.sqlite3" \
  --max-checks "${PROXY_DAILY_MAX_CHECKS:-10000}" \
  --workers "${PROXY_DAILY_WORKERS:-12}" \
  --timeout "${PROXY_DAILY_TIMEOUT:-2}" \
  --runtime-seconds "${PROXY_DAILY_RUNTIME:-20400}" \
  --profiles "$ROOT/config/proxy-projects.json"
