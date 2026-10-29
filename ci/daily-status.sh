#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
source "$ROOT/ci/storage.sh"
storage_prepare
DATA_DIR="$SYSTEM_PROXY_DATA_DIR"
exec python3 -m system_proxy.daily_status --db "$DATA_DIR/campaign.sqlite3" --days 14
