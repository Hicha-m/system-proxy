#!/usr/bin/env bash
# Read-only seed extraction for manually verified Cloudflare MCP ingestion.
# Only the approved fixed reference target is exported.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
source "$ROOT/ci/storage.sh"
storage_prepare
python3 - <<'PY'
import json
import os
from pathlib import Path
from system_proxy.d1_storage import plan_reference_sync

db = Path(os.environ["SYSTEM_PROXY_DATA_DIR"]) / "campaign.sqlite3"
plan = plan_reference_sync(db, max_endpoints=5000, max_events=2000,
                           max_sources=10000, since_hours=72)
best = sorted(
    (r for r in plan["scores"] if r[6] > 0 and r[11] == "success"),
    key=lambda r: (-(r[10] if r[10] is not None else -1), -r[6], r[3], r[4])
)
keys = {(r[3],r[4],r[5]) for r in best[:8]}
sample = {
    "scope": "reference-only",
    "source_url": "https://example.com/",
    "endpoints": [r for r in plan["endpoints"] if (r[0],r[1],r[2]) in keys],
    "sources": [r for r in plan["sources"] if (r[0],r[1],r[2]) in keys][:16],
    "events": [r for r in plan["events"] if (r[1],r[2],r[3]) in keys][:32],
    "scores": [r for r in best[:8] if (r[3],r[4],r[5]) in keys],
}
assert len(sample["endpoints"]) <= 8 and len(sample["events"]) <= 32
print("D1_REFERENCE_SEED_JSON=" + json.dumps(sample, separators=(',',':')))
PY
