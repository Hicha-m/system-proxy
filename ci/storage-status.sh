#!/usr/bin/env bash
# Inspect live persistent state (no Cloudflare credentials, no collection).
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
source "$ROOT/ci/storage.sh"
storage_prepare
python3 -m system_proxy.persistence status --data-dir "$SYSTEM_PROXY_DATA_DIR"
