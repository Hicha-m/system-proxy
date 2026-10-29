"""Conservative, opt-in pruning of old raw observations.

Preview is read-only. Apply requires an integrity-checked SQLite snapshot.
Keep the last observation PER proxy and destination to preserve cooldown and
eligibility decisions. Never delete endpoint, provenance or daily statistics.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .persistence import data_directory, snapshot

# These are fixed SQL identifiers; none is sourced from user input.
TABLES = (
    ("checks", "checked_at", ("ip", "port", "protocol")),
    ("url_checks", "checked_at", ("ip", "port", "protocol", "target_url")),
    ("destination_observations", "observed_at",
     ("ip", "port", "protocol", "domain")),
    ("gateway_attempts", "attempted_at",
     ("ip", "port", "protocol", "domain")),
)


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _sql(table: str, stamp: str, group: tuple[str, ...]) -> str:
    cols = ",".join(group)
    return (
        f"SELECT rid FROM ("
        f"SELECT rowid AS rid, {stamp} AS observed, "
        f"ROW_NUMBER() OVER (PARTITION BY {cols} "
        f"ORDER BY {stamp} DESC,rowid DESC) AS rn FROM {table}) "
        f"WHERE observed<? AND rn>1 ORDER BY observed,rid LIMIT ?"
    )


def prune_history(db: Path, *, days: int = 90, max_delete: int = 5000,
                  apply: bool = False, now: datetime | None = None) -> dict:
    if type(days) is not int or not 7 <= days <= 3650:
        raise ValueError("Retention days outside safe bounds")
    if type(max_delete) is not int or not 1 <= max_delete <= 10000:
        raise ValueError("Deletion limit outside safe bounds")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Timezone-aware clock required")
    if not db.is_file():
        return {"status": "no_database", "deleted": 0}
    cutoff = (now - timedelta(days=days)).astimezone(timezone.utc).isoformat()
    # Snapshot before the first write and before any destructive transaction.
    # Caller must not use a different persistence directory for this DB.
    if apply:
        result = snapshot(db.parent)
        if result != "saved":
            raise RuntimeError("Cannot apply retention without a verified snapshot")
    with sqlite3.connect(db, timeout=30) as conn:
        conn.execute("PRAGMA busy_timeout=30000")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        remaining = max_delete
        counts = {}
        # For apply, lock once; if interrupted SQLite rolls back the transaction.
        if apply:
            conn.execute("BEGIN IMMEDIATE")
        try:
            for table, stamp, group in TABLES:
                if not _has_table(conn, table):
                    counts[table] = 0
                    continue
                rows = [r[0] for r in conn.execute(
                    _sql(table, stamp, group), (cutoff, remaining)
                )]
                counts[table] = len(rows)
                remaining -= len(rows)
                if apply and rows:
                    # Chunking respects SQLite's parameter cap on older builds.
                    for i in range(0, len(rows), 250):
                        chunk = rows[i:i + 250]
                        marks = ",".join("?" for _ in chunk)
                        conn.execute(f"DELETE FROM {table} WHERE rowid IN ({marks})", chunk)
            if apply:
                conn.commit()
        except Exception:
            if apply:
                conn.rollback()
            raise
    return {"status": "applied" if apply else "dry_run",
            "days": days, "cutoff": cutoff, "limit": max_delete,
            "deleted": sum(counts.values()) if apply else 0,
            "eligible": counts, "protected_last_observation": True,
            "snapshot_before_apply": apply}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bounded SQLite historical retention")
    parser.add_argument("--db", type=Path, default=data_directory() / "campaign.sqlite3")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--max-delete", type=int, default=5000)
    parser.add_argument("--apply", action="store_true")
    a = parser.parse_args(argv)
    try:
        result = prune_history(a.db, days=a.days, max_delete=a.max_delete,
                               apply=a.apply)
    except (ValueError, sqlite3.Error, OSError, RuntimeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
