"""Bounded, opt-in network health campaign for the Remote Dev MCP.

Direct HTTPS and public proxy checks are INDEPENDENT; no global early exit.
Never print URLs with credentials, raw exceptions, individual IPs, or headers.
"""
from __future__ import annotations

import json
import re
import tempfile
from collections import Counter
from pathlib import Path

from .collect import Collector
from .history import History
from .source_diagnostics import diagnose_source
from .verification import direct_baseline_details, verify_candidates

LIMITS = (("http", 4), ("socks5", 2), ("socks4", 2))


def _safe_source_reason(errors: list[str]) -> str:
    """Classify provider errors without ever emitting raw URLs or secrets."""
    if not errors:
        return "no_candidates"
    error = errors[0]
    match = re.match(r"^HTTPError:\s*HTTP Error (\d{3})\b", error)
    if match:
        code = int(match.group(1))
        return ("source_http_forbidden" if code in (401, 403)
                else "source_rate_limited" if code == 429
                else "source_http_error")
    if error.startswith(("TimeoutError:", "socket.timeout:")):
        return "source_timeout"
    if error.startswith(("URLError:", "ConnectionError:", "gaierror:")):
        return "source_network_error"
    if error.startswith(("SSLError:", "SSLCertVerificationError:")):
        return "source_tls_error"
    if error.startswith("ValueError:"):
        return "source_invalid_or_oversized_response"
    return "source_unavailable"


def run_diagnostic(*, direct_check=direct_baseline_details,
                   collect=None, verify=verify_candidates,
                   source_check=diagnose_source) -> dict:
    # The direct reference check is diagnostic, NOT a prerequisite to the
    # independent proxy checks, which still verify example.com over TLS.
    try:
        direct = direct_check(3.0)
    except Exception:
        direct = {"status": "inconclusive", "failed_stage": None,
                  "reason": "diagnostic_exception", "steps": []}

    sources = []
    candidates = []
    # GeoNode normally returns a small page; HProxy is a bounded fallback
    # if GeoNode is unreachable or supplies no valid proxy candidates.
    for source_id in ("geonode", "hproxy"):
        try:
            if collect is None:
                collector = Collector(
                    timeout=5.0, max_pages=1, page_size=50,
                    max_bytes=1_000_000 if source_id == "geonode" else 8_000_000,
                    max_rows=100 if source_id == "geonode" else 30_000,
                )
                report = collector.collect([source_id])
            else:
                report = collect([source_id])
            source = report.sources.get(source_id, {})
            errors = source.get("errors", [])
            fetched = report.to_dict()["proxies"]
            usable = bool(fetched) and not errors
            sources.append({
                "source": source_id,
                "status": "ok" if usable else "inconclusive",
                "reason": "collected" if usable else _safe_source_reason(errors),
                "candidate_count": len(fetched),
            })
            if usable:
                candidates = fetched[:50]
                break
        except (OSError, ValueError, TypeError, KeyError):
            sources.append({
                "source": source_id, "status": "inconclusive",
                "reason": "source_collection_error", "candidate_count": 0,
            })

    # Probe the exact failing canonical provider URL separately, without
    # fetching its potentially large response or displaying sensitive errors.
    for source in sources:
        if source["status"] != "ok":
            try:
                source["https_diagnostic"] = source_check(source["source"], timeout=3.0)
            except Exception:
                source["https_diagnostic"] = {
                    "source": source["source"], "status": "inconclusive",
                    "failed_stage": None, "reason": "diagnostic_exception",
                    "steps": [],
                }

    collection = {
        "status": "ok" if candidates else "inconclusive",
        "reason": "collected" if candidates else "no_usable_source",
        "candidate_count": len(candidates),
        "sources": sources,
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
    if proxy_pass:
        overall, reason = "ok", "proxy_https_verified"
    elif not total_checked:
        overall, reason = "inconclusive", "no_proxy_checks_completed"
    elif direct["status"] == "ok":
        overall, reason = "ok", "direct_https_verified_proxy_sample_not_verified"
    else:
        # Direct blockage is not proof that all upstream proxy egress is blocked.
        overall, reason = "inconclusive", "no_verified_https_path_in_sample"
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
        "scope": "bounded_public_proxy_sources_fixed_https_example_com_only",
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
