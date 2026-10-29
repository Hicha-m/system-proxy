#!/usr/bin/env bash
# Read-only D1 synchronization preview against Codespace's persisted reference DB.
# NEVER sends data over the network or requires private Cloudflare credentials.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
DATA_DIR="${SYSTEM_PROXY_DATA_DIR:-${HOME}/.local/share/system-proxy}"
python3 -m system_proxy.d1_storage \
  --db "$DATA_DIR/campaign.sqlite3" \
  --max-endpoints 5000 \
  --max-events 2000 \
  --max-sources 10000 \
  --since-hours 72

# Bounded public reference-only sample for manual verification via Cloudflare MCP.
bash "$ROOT/ci/d1-reference-seed.sh"

# Secret-boundary diagnostic: NEVER print values; only presence flags.
# This reports what is visible to the Codespace CI child process.
python3 - <<'PY'
import json
import os
names = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_D1_DATABASE_ID", "CLOUDFLARE_API_TOKEN")
print(json.dumps({
    "scope": "codespace_process_only",
    "d1_configuration": {
        name: ("present" if os.environ.get(name) else "absent")
        for name in names
    },
    "values_disclosed": False,
}, sort_keys=True))
PY
