"""Fail-closed proxy selection from the local history, without network requests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .destination import DestinationHealth, domain_key
from .history import History


def select(h: History, *, protocol: str = "http", domain: str | None = None,
           freshness_seconds: int = 3600, min_score: float = 0) -> dict:
    if protocol not in {"http","socks4","socks5"}:
        raise ValueError("Unsupported protocol")
    if not 0 <= min_score <= 100:
        raise ValueError("Invalid score")
    global_health = h.summary(stale_after_seconds=freshness_seconds)["endpoints"]
    valid = {
        (p["ip"],p["port"],p["protocol"]):p for p in global_health
        if p["protocol"] == protocol
        and p["usable_by_recent_check"]
        and p["reliability_score"] is not None
        and p["reliability_score"] >= min_score
    }
    if domain is not None:
        domain = domain_key(domain)
        report = DestinationHealth(h).report(
            domain,protocol=protocol,freshness_seconds=freshness_seconds,limit=1000)
        # Require observed domain success AND fresh passing global check.
        matches = [
            {**valid[(p["ip"],p["port"],p["protocol"])],
             "destination_score":p["destination_score"],"domain":domain}
            for p in report["ranked"] if p["eligible"] and
            (p["ip"],p["port"],p["protocol"]) in valid
        ]
        matches.sort(key=lambda x:(-(x["destination_score"] or 0),
                                   -(x["reliability_score"] or 0),x["ip"],x["port"]))
    else:
        matches = sorted(valid.values(),key=lambda p:(
            -(p["reliability_score"] or 0),
            p["last_latency_ms"] if p["last_latency_ms"] is not None else float("inf"),
            p["ip"],p["port"]))
    # No address inferred as working from source metadata alone.
    return {"protocol":protocol,"domain":domain,"selected":matches[0] if matches else None,
            "eligible_count":len(matches),
            "policy":"fresh_local_check_required; domain_success_required_if_specified"}


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
