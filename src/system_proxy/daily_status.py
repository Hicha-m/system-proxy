"""Read-only overview of persistent daily discovery and reliability observations."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def load_status(db: Path, *, days: int = 14) -> dict:
    if not 1 <= days <= 90:
        raise ValueError("days must be between 1 and 90")
    if not db.is_file():
        return {"status": "no_database", "db": str(db)}
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "daily_runs" not in tables:
            return {"status": "no_daily_runs", "db": str(db)}
        days_rows = [{
            "day": day, "endpoints_seen": total,
            "new_candidates": new, "attempted": attempted,
            "passed": good, "failed": bad,
        } for day, total, new, attempted, good, bad in conn.execute("""
            SELECT day,collected,new_candidates,attempted,passed,failed
            FROM daily_runs ORDER BY day DESC LIMIT ?
        """, (days,))]
        total_checks = conn.execute(
            "SELECT COUNT(*) FROM url_checks WHERE target_url='https://example.com/'"
        ).fetchone()[0]
        protocol_counts = dict(conn.execute(
            "SELECT protocol,COUNT(*) FROM endpoints GROUP BY protocol"
        ))
        return {
            "status": "ok", "db": str(db),
            "total_reference_checks": total_checks,
            "inventory_by_protocol": protocol_counts,
            "recent_days": days_rows,
        }


def main(argv=None):
    p = argparse.ArgumentParser(description="Read-only daily proxy monitoring")
    p.add_argument("--db", type=Path,
                   default=Path.home() / ".local/share/system-proxy/campaign.sqlite3")
    p.add_argument("--days", type=int, default=14)
    args = p.parse_args(argv)
    print(json.dumps(load_status(args.db, days=args.days), sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
