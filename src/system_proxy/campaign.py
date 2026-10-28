"""Opt-in, resumable proxy verification against a fixed approved HTTPS target.

Only example.com is enabled by default. Additional target hosts require a
separate explicit authorization and target-specific response verification.
A candidate is not a working proxy until a complete HTTPS check passes.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .collect import Collector, http_fetch, normalize_endpoint
from .destination import DestinationHealth, Observation
from .history import History
from .verification import verify_candidates

PUBLIC_LIST = "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/all/data.txt"
TARGET = "https://example.com/"
DOMAIN = "example.com"


def collect_inventory(h: History, *, max_candidates: int = 25000,
                      fetch=None) -> dict:
    """Collect multiple pages; persist deduplicated public IP endpoints."""
    if not 1 <= max_candidates <= 50000:
        raise ValueError("Invalid inventory limit")
    inventory: dict[tuple[str, int], dict] = {}
    sources = {}
    for source_id in ("geonode", "hproxy"):
        # GeoNode may cap page size. HProxy exposes a single list.
        collector = Collector(**({"fetch": fetch} if fetch else {}),
                              timeout=8, max_pages=20, page_size=500,
                              max_bytes=8_000_000, max_rows=50000)
        report = collector.collect([source_id])
        sources[source_id] = {
            "candidate_count": len(report.proxies),
            "errors": len(report.sources[source_id]["errors"]),
        }
        for item in report.to_dict()["proxies"]:
            key = item["ip"], item["port"]
            existing = inventory.get(key)
            if existing is None:
                inventory[key] = item
            else:
                existing["protocols"] = sorted(set(existing["protocols"]) |
                                               set(item["protocols"]))
                existing["sources"] = sorted(set(existing["sources"]) |
                                             set(item["sources"]))
            if len(inventory) >= max_candidates:
                break
        if len(inventory) >= max_candidates:
            break
    if len(inventory) < max_candidates:
        # This is a fixed, public HTTPS dataset, NOT a dynamically supplied URL.
        # Treat every line as untrusted; never accept private IPs or credentials.
        try:
            raw = (fetch or http_fetch)(PUBLIC_LIST, 8.0, 8_000_000)
            accepted = 0
            for line in raw.decode("utf-8-sig").splitlines()[:150000]:
                try:
                    ip,port,proto = normalize_endpoint(line)
                    if proto not in {"http","socks4","socks5"}:
                        continue
                    key = ip,port
                    item = inventory.get(key)
                    if item is None:
                        inventory[key] = {"ip":ip,"port":port,
                                          "protocols":[proto],"sources":["proxifly"]}
                        accepted += 1
                    else:
                        item["protocols"] = sorted(set(item["protocols"]) | {proto})
                        item["sources"] = sorted(set(item["sources"]) | {"proxifly"})
                    if len(inventory) >= max_candidates:
                        break
                except (ValueError,TypeError,OverflowError):
                    continue
            sources["proxifly"] = {"candidate_count":accepted,"errors":0}
        except (OSError,ValueError,UnicodeError):
            sources["proxifly"] = {"candidate_count":0,"errors":1}
    h.import_candidates(list(inventory.values()))
    return {"unique_endpoints": len(inventory), "sources": sources,
            "target": max_candidates, "target_reached": len(inventory) >= max_candidates}


def run(*, db: Path, max_checks: int = 10000, timeout: float = 2.0,
        workers: int = 12, batch_size: int = 120,
        runtime_seconds: int = 780, refresh: bool = False,
        fetch=None, verifier=verify_candidates) -> dict:
    if not (1 <= max_checks <= 10000 and 0 < timeout <= 5 and
            1 <= workers <= 16 and 1 <= batch_size <= 500 and
            1 <= runtime_seconds <= 840):
        raise ValueError("Campaign exceeds approved limits")
    started = time.monotonic()
    db.parent.mkdir(parents=True, exist_ok=True)
    with History(db) as history:
        health = DestinationHealth(history)
        inventory = collect_inventory(history, max_candidates=25000, fetch=fetch)
        checked = Counter()
        attempts = 0
        # Iterate over all known candidate protocols, not just the first page.
        for protocol in ("http", "socks4", "socks5"):
            rows = history.conn.execute(
                "SELECT ip,port,protocol FROM endpoints WHERE protocol=? "
                "ORDER BY ip,port", (protocol,)
            )
            pending = []
            for ip, port, proto in rows:
                if attempts >= max_checks or time.monotonic()-started >= runtime_seconds:
                    break
                if not refresh:
                    recent = history.conn.execute(
                        "SELECT 1 FROM destination_observations "
                        "WHERE ip=? AND port=? AND protocol=? AND domain=? LIMIT 1",
                        (ip,port,proto,DOMAIN)
                    ).fetchone()
                    if recent:
                        checked["skipped_previously_checked"] += 1
                        continue
                pending.append((ip, port, proto))
                if len(pending) >= min(batch_size, max_checks-attempts):
                    _execute_batch(history, health, pending, timeout, workers,
                                   checked, verifier)
                    attempts += len(pending)
                    pending = []
            if pending and attempts < max_checks and time.monotonic()-started < runtime_seconds:
                _execute_batch(history,health,pending[:max_checks-attempts],
                               timeout,workers,checked,verifier)
                attempts += min(len(pending),max_checks-attempts)
            if attempts >= max_checks or time.monotonic()-started >= runtime_seconds:
                break
        # Efficient SQL ranking for a large inventory. Don't call the
        # per-record report which is intended only for small interactive lists.
        rankings = {}
        for proto in ("http", "socks4", "socks5"):
            rankings[proto] = [
                {"ip": ip, "port": port, "successes": good, "failures": bad,
                 "last_latency_ms": latency}
                for ip,port,good,bad,latency in history.conn.execute("""
                  SELECT ip,port,
                    SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) AS good,
                    SUM(CASE WHEN outcome='proxy_error' THEN 1 ELSE 0 END) AS bad,
                    MIN(CASE WHEN outcome='success' THEN latency_ms ELSE NULL END)
                  FROM destination_observations
                  WHERE domain=? AND protocol=?
                  GROUP BY ip,port
                  HAVING good > 0
                  ORDER BY good DESC, bad ASC, 5 ASC LIMIT 20
                """,(DOMAIN,proto))
            ]
        return {
            "destination": TARGET,
            "inventory": inventory,
            "attempts_this_run": attempts,
            "checks": dict(checked),
            "duration_seconds": round(time.monotonic()-started,2),
            "max_checks":max_checks,
            "db":str(db),
            "ranking":rankings,
            "complete": attempts >= max_checks,
        }


def _execute_batch(history, health, entries, timeout, workers, totals, verifier):
    checks = verifier(entries, timeout=timeout, workers=workers,
                      max_candidates=len(entries), baseline=False)
    # Run only for example.com; all existing probes verify TLS and known content.
    history.add_checks(checks)
    for check in checks:
        totals[check.status] += 1
        health.record(Observation(
            ip=check.ip,port=check.port,protocol=check.protocol,domain=DOMAIN,
            outcome=("success" if check.status=="passed" else
                     "unknown" if check.status=="unavailable" else "proxy_error"),
            latency_ms=check.latency_ms,
            observed_at=check.checked_at,
        ))


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description="Opt-in, bounded Codespace proxy campaign")
    p.add_argument("--db",type=Path,default=Path("proxy-campaign.sqlite3"))
    p.add_argument("--target-url",default=TARGET)
    p.add_argument("--max-checks",type=int,default=10000)
    p.add_argument("--timeout",type=float,default=2)
    p.add_argument("--workers",type=int,default=12)
    p.add_argument("--batch-size",type=int,default=120)
    p.add_argument("--runtime-seconds",type=int,default=780)
    p.add_argument("--refresh",action="store_true")
    args=p.parse_args(argv)
    if args.target_url != TARGET:
        p.error("This campaign supports only its approved reference target; add a reviewed target profile for other URLs")
    print(json.dumps(run(db=args.db,max_checks=args.max_checks,
                         timeout=args.timeout,workers=args.workers,
                         batch_size=args.batch_size,
                         runtime_seconds=args.runtime_seconds,
                         refresh=args.refresh),sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
