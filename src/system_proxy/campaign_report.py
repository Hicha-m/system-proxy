"""Fast, read-only ranking of previously verified URL-specific proxies."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from .persistence import data_directory

from .campaign import TARGET
from .destination import wilson_lower

PROTOCOLS = ("http", "socks4", "socks5")


def load_report(db: Path, *, target_url: str = TARGET, limit: int = 20) -> dict:
    if target_url != TARGET or not 1 <= limit <= 100:
        raise ValueError("Unapproved target or report limit")
    if not db.is_file():
        return {"target_url": target_url, "status": "no_database",
                "ranked": {p: [] for p in PROTOCOLS}, "checked": 0}
    uri = db.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='url_checks'"
        ).fetchone()
        if not exists:
            return {"target_url": target_url, "status": "no_campaign_data",
                    "ranked": {p: [] for p in PROTOCOLS}, "checked": 0}
        total = conn.execute(
            "SELECT COUNT(*) FROM url_checks WHERE target_url=?", (target_url,)
        ).fetchone()[0]
        ranked = {}
        for proto in PROTOCOLS:
            rows = conn.execute("""
                SELECT ip,port,
                    SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END) AS good,
                    SUM(CASE WHEN outcome='proxy_error' THEN 1 ELSE 0 END) AS bad,
                    MIN(CASE WHEN outcome='success' THEN latency_ms END) AS latency
                FROM url_checks
                WHERE target_url=? AND protocol=?
                GROUP BY ip,port
                HAVING good > 0
            """, (target_url,proto))
            candidates = [
                {"ip":ip,"port":port,"successes":good,"failures":bad,
                 "score":wilson_lower(good,bad),"best_latency_ms":latency}
                for ip,port,good,bad,latency in rows
            ]
            candidates.sort(key=lambda p: (-(p["score"] or 0),
                                           -p["successes"],
                                           p["best_latency_ms"] if p["best_latency_ms"] is not None else float("inf")))
            ranked[proto] = candidates[:limit]
        return {"target_url":target_url,"status":"ok","checked":total,
                "ranked":ranked,
                "score_definition":"Wilson 95% bound of successful HTTPS requests to exactly this URL"}


def main(argv=None):
    p=argparse.ArgumentParser(description="Read-only per-URL proxy ranking")
    p.add_argument("--db",type=Path,default=data_directory() / "campaign.sqlite3")
    p.add_argument("--target-url",default=TARGET)
    p.add_argument("--limit",type=int,default=20)
    a=p.parse_args(argv)
    print(json.dumps(load_report(a.db,target_url=a.target_url,limit=a.limit),
                     sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
