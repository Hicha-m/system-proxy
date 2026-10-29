#!/usr/bin/env bash
# Read-only D1 synchronization preview against Codespace's persisted reference DB.
# NEVER sends data over the network or requires private Cloudflare credentials.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
DATA_DIR="${SYSTEM_PROXY_DATA_DIR:-${HOME}/.local/share/system-proxy}"
exec python3 -m system_proxy.d1_storage \
  --db "$DATA_DIR/campaign.sqlite3" \
  --max-endpoints 5000 \
  --max-events 2000 \
  --max-sources 10000 \
  --since-hours 72
