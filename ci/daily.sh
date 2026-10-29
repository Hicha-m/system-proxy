#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
source "$ROOT/ci/storage.sh"
storage_prepare
DATA_DIR="$SYSTEM_PROXY_DATA_DIR"
trap storage_snapshot_on_exit EXIT
python3 -m system_proxy.daily \
  --db "$DATA_DIR/campaign.sqlite3" \
  --max-checks "${PROXY_DAILY_MAX_CHECKS:-10000}" \
  --workers "${PROXY_DAILY_WORKERS:-12}" \
  --timeout "${PROXY_DAILY_TIMEOUT:-2}" \
  --runtime-seconds "${PROXY_DAILY_RUNTIME:-20400}" \
  --profiles "$ROOT/config/proxy-projects.json"

# Codespace remains responsible for verification. Synchronize only after
# a successful daily run, and only when its private Cloudflare secret exists.
if [[ "${PROXY_D1_SYNC_ENABLED:-1}" == "1" ]]; then
  bash "$ROOT/ci/codespace-d1-sync.sh"
else
  python3 -m system_proxy.ops record-sync --data-dir "$DATA_DIR" \
    --status disabled --reason owner_disabled
fi

# Maintenance defaults to a read-only 90-day preview. Explicit opt-in
# is required to remove up to 1,000 old duplicate observations. The
# retention module creates and checks a SQLite snapshot before changes.
if [[ "${PROXY_RETENTION_AUTO_APPLY:-0}" == "1" ]]; then
  python3 -m system_proxy.retention \
    --db "$DATA_DIR/campaign.sqlite3" --days 90 --max-delete 1000 --apply
else
  python3 -m system_proxy.retention \
    --db "$DATA_DIR/campaign.sqlite3" --days 90 --max-delete 1000
fi
python3 -m system_proxy.ops report --db "$DATA_DIR/campaign.sqlite3"
