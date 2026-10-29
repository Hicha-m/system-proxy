#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
python3 -m system_proxy.target_cli --config config/examples/public-targets.json profiles >/tmp/system-proxy-public-profiles.json
python3 -m system_proxy.target_cli --config config/examples/public-targets.json demo >/tmp/system-proxy-public-demo.json
python3 - <<'PY'
import json
from pathlib import Path
profiles = json.loads(Path("/tmp/system-proxy-public-profiles.json").read_text())
demo = json.loads(Path("/tmp/system-proxy-public-demo.json").read_text())
assert profiles["count"] == 4
assert demo["synthetic"] and demo["test_net_only"]
assert demo["network_requests"] == 0
assert len(demo["targets"]) == 4
for record in demo["targets"].values():
    assert record["ranking"]["observed_proxies"] == 3
assert len({value["ranking"]["ranking"][0]["ip"] for value in demo["targets"].values()}) >= 3
print("Public demonstration OK: 4 isolated target rankings with distinct winners, reserved IPs, no network calls")
PY
