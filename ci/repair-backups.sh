#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
source "$ROOT/ci/storage.sh"
storage_prepare
# This job writes only verified backup generations, not the primary DB.
python3 -m system_proxy.persistence snapshot --data-dir "$SYSTEM_PROXY_DATA_DIR"
python3 -m system_proxy.persistence snapshot --data-dir "$SYSTEM_PROXY_DATA_DIR"
python3 -m system_proxy.persistence status --data-dir "$SYSTEM_PROXY_DATA_DIR"
