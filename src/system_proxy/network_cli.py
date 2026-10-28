"""Bounded, opt-in network health campaign for the Remote Dev MCP.

Direct HTTPS and public proxy checks are INDEPENDENT; no global early exit.
Never print URLs with credentials, raw exceptions, individual IPs, or headers.
"""
from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

from .collect import Collector
from .history import History
from .verification import direct_baseline_details, verify_candidates

LIMITS = (("http", 4), ("socks5", 2), ("socks4", 2))


def run_diagnostic(*, direct_check=direct_baseline_details,
                   collect=None, verify=verify_candidates) -> dict:
    # A failed direct connection must not prevent collection or proxy testing.
    try:
        direct = direct_check(3.0)
    except Exception:
        direct = {"status": "inconclusive", "failed_stage": None,
                  "reason": "diagnostic_exception", "steps": []}

    try:
        report = (collect or Collector(timeout=5.0, max_pages=1, page_size=50,
                                       max_bytes=1_000_000, max_rows=100).collect)(["geonode"])
        candidates = report.to_dict()["proxies"]
        source = report.sources.get("geonode", {})
        collection_errors = len(source.get("errors", []))
        collection = {
            "status": "ok" if candidates and not collection_errors else "inconclusive",
            "reason": ("collected" if candidates and not collection_errors
                       else "source_response_unavailable" if collection_errors
                       else "no_candidates"),
            "candidate_count": len(candidates),
            "error_count": collection_errors,
        }
    except (OSError, ValueError, TypeError, KeyError):
        candidates = []
        collection = {
            "status": "inconclusive", "reason": "source_collection_error",
            "candidate_count": 0, "error_count": 1,
        }

    results = {}
    total_checked = 0
    with tempfile.TemporaryDirectory(prefix="system-proxy-network-") as tmp:
        with History(Path(tmp) / "checks.sqlite3") as history:
            if candidates:
                history.import_candidates(candidates)
            for protocol, limit in LIMITS:
                entries = history.candidates(protocol, limit)
                if not entries:
                    results[protocol] = {
                        "status": "inconclusive", "reason": "no_candidates",
                        "checked": 0, "passed": 0, "failed": 0, "unavailable": 0,
                    }
                    continue
                # The fixed example.com reference remains mandatory INSIDE
                # each proxy probe. Do not disable certificate verification.
                # No direct baseline gate (it may fail while proxies work).
                checks = verify(entries, max_candidates=limit,
                                timeout=3.0, workers=2, baseline=False)
                history.add_checks(checks)
                totals = Counter(c.status for c in checks)
                tested = len(checks)
                total_checked += tested
                results[protocol] = {
                    "status": ("ok" if totals["passed"] else
                               "failed" if totals["failed"] else "inconclusive"),
                    "reason": ("verified_https_proxy" if totals["passed"] else
                               "sample_failed" if totals["failed"] else "not_verified"),
                    "checked": tested,
                    "passed": totals["passed"],
                    "failed": totals["failed"],
                    "unavailable": totals["unavailable"],
                }

    proxy_pass = any(x["passed"] > 0 for x in results.values())
    if direct["status"] == "ok" or proxy_pass:
        overall = "ok"
        reason = "https_verified"
    elif direct["status"] == "blocked":
        # The direct test may be blocked by policy, but this does NOT prove
        # all proxy egress is prohibited.
        overall = "inconclusive"
        reason = "direct_blocked_proxy_not_verified"
    else:
        overall = "inconclusive"
        reason = "no_verified_https_path_in_sample"
    return {
        "schema_version": 2,
        "network_profile": overall,
        "status": overall,
        "reason": reason,
        "direct": direct,
        "collection": collection,
        "proxy_results": results,
        "live_proxy_available": proxy_pass,
        "tested_endpoints": total_checked,
        "tests_completed": total_checked > 0,
        "scope": "geonode_public_proxy_sample_fixed_https_example_com_only",
    }


def main() -> int:
    try:
        report = run_diagnostic()
    except Exception:
        # Prevent accidental traceback leakage of URLs or secrets.
        report = {
            "schema_version": 2, "network_profile": "inconclusive",
            "status": "inconclusive", "reason": "unexpected_diagnostic_error",
            "tests_completed": False,
        }
    print(json.dumps(report, sort_keys=True, ensure_ascii=True), flush=True)
    # A diagnostic is not judged by whether the sampled free proxies work.
    # Machine-readable status is authoritative; 2 means not completed.
    return 0 if report.get("tests_completed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
