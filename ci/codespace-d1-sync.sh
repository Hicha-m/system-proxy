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
if [[ ! -f "$DB" ]]; then
  printf '{"status":"skipped","reason":"no_local_campaign_database"}\n'
  exit 0
fi
if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  printf '{"status":"skipped","reason":"codespace_cloudflare_secret_missing","action":"configure_secret"}\n'
  exit 0
fi
if [[ -z "${CLOUDFLARE_ACCOUNT_ID:-}" || -z "${CLOUDFLARE_D1_DATABASE_ID:-}" ]]; then
  printf '{"status":"skipped","reason":"cloudflare_d1_identifiers_missing"}\n'
  exit 0
fi
python3 -m system_proxy.d1_storage \
  --db "$DB" \
  --max-endpoints "${PROXY_D1_MAX_ENDPOINTS:-5000}" \
  --max-events "${PROXY_D1_MAX_EVENTS:-2000}" \
  --max-sources "${PROXY_D1_MAX_SOURCES:-10000}" \
  --since-hours "${PROXY_D1_SINCE_HOURS:-72}" \
  --apply
