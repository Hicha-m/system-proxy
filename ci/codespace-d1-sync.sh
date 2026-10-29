#!/usr/bin/env bash
# Codespace-owned SQLite -> Cloudflare D1 synchronization.
# The project repository contains no credentials.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
source "$ROOT/ci/storage.sh"
storage_prepare
DATA_DIR="$SYSTEM_PROXY_DATA_DIR"
DB="$DATA_DIR/campaign.sqlite3"
record_d1() {
  python3 -m system_proxy.ops record-sync --data-dir "$DATA_DIR" \
    --status "$1" --reason "$2"
}
if [[ ! -f "$DB" ]]; then
  record_d1 skipped no_local_campaign_database
  exit 0
fi
if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  record_d1 skipped codespace_cloudflare_secret_missing
  exit 0
fi
if [[ -z "${CLOUDFLARE_ACCOUNT_ID:-}" || -z "${CLOUDFLARE_D1_DATABASE_ID:-}" ]]; then
  record_d1 skipped cloudflare_d1_identifiers_missing
  exit 0
fi
if python3 -m system_proxy.d1_storage \
  --db "$DB" \
  --max-endpoints "${PROXY_D1_MAX_ENDPOINTS:-5000}" \
  --max-events "${PROXY_D1_MAX_EVENTS:-2000}" \
  --max-sources "${PROXY_D1_MAX_SOURCES:-10000}" \
  --since-hours "${PROXY_D1_SINCE_HOURS:-72}" \
  --apply; then
  record_d1 synced ok
else
  record_d1 failed transport_or_schema_error
  exit 1
fi
