"""Fail-closed proxy selection using local checks; no network requests."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .destination import DestinationHealth, domain_key
from .history import History

PROTOCOLS = frozenset(("http", "socks4", "socks5"))


def ranked_candidates(h: History, *, protocol: str = "http",
                      domain: str | None = None, freshness_seconds: int = 3600,
                      min_score: float = 0, limit: int = 1000) -> list[dict]:
    """Return ranked, DISTINCT, eligible endpoints (not unverified raw lists).

    Domain-specific success is required only when domain is supplied.
    Gateway callers may inspect several candidates for bounded pre-CONNECT
    failover; no blind fallback to unverified or stale endpoints is possible.
    """
    if protocol not in PROTOCOLS:
        raise ValueError("Unsupported protocol")
    if not isinstance(min_score, (int, float)) or not math.isfinite(min_score) or not 0 <= min_score <= 100:
        raise ValueError("Invalid score")
    if not 1 <= freshness_seconds <= 2_592_000 or not 1 <= limit <= 1000:
        raise ValueError("Invalid ranking limits")

    valid = {
        (p["ip"], p["port"], p["protocol"]): p
        for p in h.summary(stale_after_seconds=freshness_seconds)["endpoints"]
        if p["protocol"] == protocol
        and p["usable_by_recent_check"]
        and p["reliability_score"] is not None
        and p["reliability_score"] >= min_score
    }
    if domain is not None:
        domain = domain_key(domain)
        report = DestinationHealth(h).report(
            domain, protocol=protocol,
            freshness_seconds=freshness_seconds, limit=1000,
        )
        matches = [
            {**valid[key], "destination_score":p["destination_score"],
             "domain":domain}
            for p in report["ranked"]
            if p["eligible"]
            for key in [(p["ip"], p["port"], p["protocol"])]
            if key in valid
        ]
        matches.sort(key=lambda x: (
            -(x["destination_score"] or 0),
            -(x["reliability_score"] or 0),
            x["ip"], x["port"],
        ))
    else:
        matches = sorted(valid.values(), key=lambda p: (
            -(p["reliability_score"] or 0),
            p["last_latency_ms"] if p["last_latency_ms"] is not None else float("inf"),
            p["ip"], p["port"],
        ))
    return matches[:limit]


def select(h: History, *, protocol: str = "http", domain: str | None = None,
           freshness_seconds: int = 3600, min_score: float = 0) -> dict:
    matches = ranked_candidates(
        h, protocol=protocol, domain=domain,
        freshness_seconds=freshness_seconds, min_score=min_score,
    )
    return {
        "protocol":protocol, "domain":domain,
        "selected":matches[0] if matches else None,
        "eligible_count":len(matches),
        "policy":"fresh_local_check_required; domain_success_required_if_specified",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="system-proxy-select")
    p.add_argument("--db", type=Path, default=Path("proxy-history.sqlite3"))
    p.add_argument("--protocol", choices=["http","socks4","socks5"],default="http")
    p.add_argument("--domain", help="If set, require recent domain success")
    p.add_argument("--freshness-seconds",type=int,default=3600)
    p.add_argument("--min-score",type=float,default=0)
    args = p.parse_args(argv)
    try:
        with History(args.db) as h:
            result = select(h,protocol=args.protocol,domain=args.domain,
                            freshness_seconds=args.freshness_seconds,min_score=args.min_score)
    except ValueError as exc:
        p.error(str(exc))
    print(json.dumps(result,indent=2))
    return 0 if result["selected"] is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
