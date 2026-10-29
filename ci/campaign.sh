#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
# Use the shared durable state directory, never the checkout itself.
source "$ROOT/ci/storage.sh"
storage_prepare
DATA_DIR="$SYSTEM_PROXY_DATA_DIR"
trap storage_snapshot_on_exit EXIT
python3 -m system_proxy.campaign --db "$DATA_DIR/campaign.sqlite3" --max-checks 10000 --timeout 2 --workers 12 --batch-size 120 --runtime-seconds 20400
