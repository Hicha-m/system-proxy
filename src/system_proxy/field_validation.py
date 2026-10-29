"""Small, fixed-destination, read-only field validation on a persistent inventory.

No new candidates, DB writes, raw addresses, free-form errors or secrets in output.
This deliberately checks only proxy ports permitted by the MCP owner profile.
"""
from __future__ import annotations

import concurrent.futures
import json
import random
import sqlite3
import statistics
from collections import Counter
from pathlib import Path

from .persistence import data_directory
from .verification import check_one, direct_baseline_details

PROTOCOLS = ("http", "socks4", "socks5")
# Current MCP owner's approved egress ports; this is NOT a proxy discovery policy.
PERMITTED_PORTS = (80, 443, 1080, 3128, 8000, 8080, 8081, 8888)
SAMPLE_PER_PROTOCOL = 12
WORKERS = 4
TIMEOUT_SECONDS = 3.0
RETRY_TIMEOUT_SECONDS = 5.0
MAX_TIMEOUT_RETRIES_PER_PROTOCOL = 3
SEED = 20261029
REASON_CODES = frozenset({
    "verified_https_content", "unexpected_response", "timeout", "tls_error",
    "dns_resolution_failed", "network_policy_denied", "proxy_or_target_denied",
    "upstream_or_target_error", "invalid_http_response", "url_error_unknown",
    "proxy_connection_error", "network_or_protocol_error", "invalid_probe_request",
    "unexpected_checker_exception", "protocol_not_implemented", "check_target_unavailable",
})


def select_sample(conn: sqlite3.Connection, *, per_protocol: int = SAMPLE_PER_PROTOCOL,
                  seed: int = SEED) -> tuple[dict, dict]:
    """Independent deterministic uniform samples; avoid first-IP and first-protocol bias."""
    if not 1 <= per_protocol <= 12:
        raise ValueError("Sampling exceeds approved limit")
    rng = random.Random(seed)
    samples = {}
    coverage = {}
    for protocol in PROTOCOLS:
        # All inventory entries are already public IP literals validated at import.
        rows = conn.execute(
            "SELECT ip,port,protocol FROM endpoints WHERE protocol=? ORDER BY ip,port",
            (protocol,)
        ).fetchall()
        eligible = [r for r in rows if r[1] in PERMITTED_PORTS]
        # Exclude endpoints recently tested by the persistent daily scheduler,
        # so this probe does not repeatedly contact the same public proxies.
        recently_checked = set(conn.execute("""
            SELECT ip,port,protocol FROM checks
            WHERE protocol=? AND checked_at>=datetime('now','-1 day')
        """, (protocol,)).fetchall())
        fresh = [r for r in eligible if r not in recently_checked]
        count = min(per_protocol, len(fresh))
        samples[protocol] = rng.sample(fresh, count)
        coverage[protocol] = {
            "inventory": len(rows),
            "approved_port_candidates": len(eligible),
            "eligible_not_recently_checked": len(fresh),
            "selected": count,
        }
    return samples, coverage


def safe_summary(checks: list) -> dict:
    status = Counter(c.status if c.status in {"passed", "failed", "unavailable"}
                     else "unavailable" for c in checks)
    reasons = Counter(c.reason if c.reason in REASON_CODES else "other"
                      for c in checks)
    latency = [c.latency_ms for c in checks if c.status == "passed"
               and c.latency_ms is not None]
    return {
        "checked": len(checks),
        "passed": status["passed"], "failed": status["failed"],
        "unavailable": status["unavailable"],
        "success_rate_percent": round(100 * status["passed"] / len(checks), 1)
                                if checks else None,
        "reasons": dict(sorted(reasons.items())),
        "passed_median_latency_ms": round(statistics.median(latency), 2)
                                    if latency else None,
    }


def run(conn: sqlite3.Connection, *, check=check_one,
        direct=direct_baseline_details, per_protocol: int = SAMPLE_PER_PROTOCOL,
        seed: int = SEED) -> dict:
    samples, coverage = select_sample(conn, per_protocol=per_protocol, seed=seed)
    # A failed direct baseline must not suppress independent proxy checks.
    baseline = direct(5.0)
    safe_direct = {
        "status": baseline.get("status") if baseline.get("status") in
                  {"ok", "failed", "blocked", "inconclusive"} else "inconclusive",
        "failed_stage": baseline.get("failed_stage") if baseline.get("failed_stage") in
                        {"dns", "tcp", "tls", "http", None} else None,
    }
    out = {}
    for protocol, candidates in samples.items():
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            checks = list(pool.map(lambda item: check(*item, TIMEOUT_SECONDS), candidates))
        # Targeted timeout experiment. Retry only 3 timed-out proxies per protocol.
        timed_out = [c for c in checks if c.reason == "timeout"][:MAX_TIMEOUT_RETRIES_PER_PROTOCOL]
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            retries = list(pool.map(lambda c: check(c.ip, c.port, c.protocol,
                                                     RETRY_TIMEOUT_SECONDS), timed_out))
        out[protocol] = {
            **safe_summary(checks),
            "timeout_retry": {
                "attempted": len(retries),
                "passed_at_5_seconds": sum(c.status == "passed" for c in retries),
                "reasons": safe_summary(retries)["reasons"],
            },
        }
    return {
        "status": "ok",
        "scope": "fixed_https_example_com_public_candidates_only",
        "persistence": "read_only_no_history_changes",
        "network_profile": "owner_approved_public_tcp_ports_only",
        "baseline_direct": safe_direct,
        "per_protocol": out,
        "sampling": coverage,
        "timeout_seconds": TIMEOUT_SECONDS,
        "retry_timeout_seconds": RETRY_TIMEOUT_SECONDS,
        "maximum_initial_checks": per_protocol * len(PROTOCOLS),
        "maximum_timeout_retries": MAX_TIMEOUT_RETRIES_PER_PROTOCOL * len(PROTOCOLS),
    }


def main() -> int:
    root = data_directory()
    db = root / "campaign.sqlite3"
    if root.is_symlink() or db.is_symlink() or not db.is_file():
        print(json.dumps({"status": "error", "reason": "database_missing_or_unsafe"}))
        return 2
    try:
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True,
                             timeout=10) as conn:
            conn.execute("PRAGMA query_only=ON")
            report = run(conn)
    except Exception:
        # Don't print untrusted network exception details, file paths, IPs or URLs.
        print(json.dumps({"status": "error", "reason": "validation_unavailable"}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
