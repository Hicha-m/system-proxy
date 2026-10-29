#!/usr/bin/env bash
# Read-only D1 synchronization preview against Codespace's persisted reference DB.
# NEVER sends data over the network or requires private Cloudflare credentials.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
source "$ROOT/ci/storage.sh"
storage_prepare
DATA_DIR="$SYSTEM_PROXY_DATA_DIR"
if [[ ! -f "$DATA_DIR/campaign.sqlite3" ]]; then
  printf '{"status":"skipped","reason":"no_local_campaign_database","next":"run_d1-bootstrap"}\n'
  exit 0
fi
python3 -m system_proxy.d1_storage \
  --db "$DATA_DIR/campaign.sqlite3" \
  --max-endpoints 5000 \
  --max-events 2000 \
  --max-sources 10000 \
  --since-hours 72

# Bounded public reference-only sample for manual verification via Cloudflare MCP.
bash "$ROOT/ci/d1-reference-seed.sh"
