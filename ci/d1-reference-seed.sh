#!/usr/bin/env bash
# Read-only diagnostic: bounded reference-only seed snapshot from persistent SQLite.
# Output contains public proxy IPs; NEVER print private target configuration.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
python3 - <<'PY'
import json
import os
from pathlib import Path
from system_proxy.d1_storage import plan_reference_sync

db = Path(os.environ.get("SYSTEM_PROXY_DATA_DIR", str(Path.home()/".local/share/system-proxy"))) / "campaign.sqlite3"
plan = plan_reference_sync(db, max_endpoints=5000, max_events=2000, max_sources=10000, since_hours=72)
# Only recently checked and positively verified reference endpoints.
best = sorted((r for r in plan["scores"] if r[6] > 0 and r[11] == "success"),
              key=lambda r: (-(r[10] if r[10] is not None else -1), -r[6], r[3], r[4]))
keys = {(r[3],r[4],r[5]) for r in best[:12]}
sample = {
    "scope": "reference-only",
    "source_url": "https://example.com/",
    "endpoints": [r for r in plan["endpoints"] if (r[0],r[1],r[2]) in keys],
    "sources": [r for r in plan["sources"] if (r[0],r[1],r[2]) in keys][:24],
    "events": [r for r in plan["events"] if (r[1],r[2],r[3]) in keys][:60],
    "scores": [r for r in best[:12] if (r[3],r[4],r[5]) in keys],
}
# Production assets and private profile URLs MUST NOT appear in this report.
assert set(sample) == {"scope","source_url","endpoints","sources","events","scores"}
assert len(sample["endpoints"]) <= 12 and len(sample["events"]) <= 60
print("D1_REFERENCE_SEED_JSON=" + json.dumps(sample, separators=(',',':')))
PY
