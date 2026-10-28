#!/usr/bin/env bash
# Explicit opt-in bounded real-network diagnostic for Remote Dev MCP.
# The MCP administrator must independently allow the "network" profile.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
exec python3 - <<'PY'
import json
import tempfile
from collections import Counter
from pathlib import Path

from system_proxy.collect import Collector
from system_proxy.history import History
from system_proxy.verification import direct_baseline, verify_candidates

def main():
    timeout = 3.0
    if not direct_baseline(timeout):
        print(json.dumps({"network_profile": "blocked", "reason": "example_com_baseline_unavailable", "tests_completed": False}))
        return 2
    report = Collector(timeout=5.0, max_pages=1, page_size=50,
                       max_bytes=1_000_000, max_rows=100).collect(["geonode"])
    source = report.sources.get("geonode", {})
    proxies = report.to_dict()["proxies"]
    if source.get("errors") or not proxies:
        print(json.dumps({"network_profile": "blocked",
                          "reason": "geonode_collection_failed",
                          "source_errors": source.get("errors", []),
                          "tests_completed": False}))
        return 2
    with tempfile.TemporaryDirectory(prefix="system-proxy-network-") as dirname:
        with History(Path(dirname) / "checks.sqlite3") as history:
            history.import_candidates(proxies)
            outcomes = {}
            for protocol, count in (("http", 4), ("socks5", 2), ("socks4", 2)):
                entries = history.candidates(protocol, count)
                if not entries:
                    outcomes[protocol] = {"tested": 0, "passed": 0, "failed": 0, "unavailable": 0}
                    continue
                checks = verify_candidates(entries, timeout=timeout, workers=2,
                                           max_candidates=count, baseline=False)
                history.add_checks(checks)
                counter = Counter(c.status for c in checks)
                outcomes[protocol] = {"tested": len(checks), "passed": counter["passed"],
                                       "failed": counter["failed"], "unavailable": counter["unavailable"]}
            summary = history.summary()
            print(json.dumps({
                "network_profile": "completed", "source": "geonode",
                "collected_unique": len(proxies),
                "checked_endpoints": summary["checked_endpoints"],
                "protocol_results": outcomes,
                "live_proxy_available": any(p["passed"] for p in outcomes.values()),
                "scope": "fixed_https_example_com_only", "tests_completed": True,
            }, sort_keys=True))
            return 0  # Diagnostics completed, NOT necessarily a live proxy.
raise SystemExit(main())
PY
