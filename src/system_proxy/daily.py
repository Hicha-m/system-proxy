"""Daily proxy discovery and reliability checks with durable SQLite history.

The reference target is deliberately fixed. A result on example.com does not
prove a proxy will work for an unrelated destination.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .persistence import data_directory

from .campaign import (
    DOMAIN, TARGET, _execute_batch, collect_inventory,
)
from .destination import DestinationHealth
from .history import History
from .verification import verify_candidates
from .project_export import export_projects

PROTOCOLS = ("http", "socks4", "socks5")


def plan_checks(conn, *, previous_keys: set[tuple[str, int, str]],
                now: datetime, max_checks: int) -> tuple[list[tuple[str, int, str]], dict]:
    """Prioritize newly discovered proxies and due successful rechecks.

    A positive result is rechecked after four hours, unverified old candidates
    are tested next, and failed proxies get a 72-hour cooldown. No endpoint is
    checked more than once in this cycle. Protocols are independent.
    """
    latest = {}
    for ip, port, protocol, outcome, stamp in conn.execute(
        "SELECT ip,port,protocol,outcome,checked_at FROM url_checks "
        "WHERE target_url=? ORDER BY rowid", (TARGET,)
    ):
        latest[(ip, port, protocol)] = (outcome, stamp)

    new, due_success, unseen, due_failed, due_unknown = [], [], [], [], []
    for key in conn.execute(
        "SELECT ip,port,protocol FROM endpoints "
        "WHERE protocol IN ('http','socks4','socks5') ORDER BY protocol,ip,port"
    ):
        record = latest.get(key)
        if record is None:
            (new if key not in previous_keys else unseen).append(key)
            continue
        outcome, stamp = record
        try:
            age = now - datetime.fromisoformat(stamp)
        except (ValueError, TypeError):
            continue
        if age < timedelta(0):
            continue
        if outcome == "success" and age >= timedelta(hours=4):
            due_success.append(key)
        elif outcome == "proxy_error" and age >= timedelta(hours=72):
            due_failed.append(key)
        elif outcome == "unknown" and age >= timedelta(hours=24):
            due_unknown.append(key)

    # Preserve a minimum recheck budget even when sources provide many new IPs.
    reserve = min(len(due_success), max(1, max_checks // 5))
    selected = (
        due_success[:reserve] + new + unseen +
        due_success[reserve:] + due_unknown + due_failed
    )[:max_checks]
    return selected, {
        "new_candidates": len(new),
        "due_success": len(due_success),
        "previously_unchecked": len(unseen),
        "due_failed": len(due_failed),
        "due_unknown": len(due_unknown),
        "selected": len(selected),
    }


def run_daily(*, db: Path, max_checks: int = 10000,
              runtime_seconds: int = 20400, timeout: float = 2.0,
              workers: int = 12, batch_size: int = 120,
              profiles: Path | None = None, force: bool = False,
              now: datetime | None = None,
              fetch=None, verifier=verify_candidates) -> dict:
    if not (1 <= max_checks <= 10000 and 1 <= runtime_seconds <= 21000
            and 0 < timeout <= 5 and 1 <= workers <= 16
            and 1 <= batch_size <= 500):
        raise ValueError("Daily campaign exceeds approved limits")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("An aware datetime is required")
    today = now.astimezone(timezone.utc).date().isoformat()
    db.parent.mkdir(parents=True, exist_ok=True)
    lock_path = db.with_suffix(".lock")
    # Single writer across CI / scheduled jobs on the same filesystem.
    with lock_path.open("a+") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "already_running", "date": today, "db": str(db)}
        started = time.monotonic()
        with History(db) as history:
            conn = history.conn
            health = DestinationHealth(history)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS url_checks (
                    ip TEXT NOT NULL, port INTEGER NOT NULL, protocol TEXT NOT NULL,
                    target_url TEXT NOT NULL, outcome TEXT NOT NULL,
                    latency_ms REAL, checked_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_url_checks
                    ON url_checks(target_url,protocol,ip,port);
                CREATE TABLE IF NOT EXISTS daily_runs (
                    day TEXT NOT NULL, started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL, collected INTEGER NOT NULL,
                    new_candidates INTEGER NOT NULL, attempted INTEGER NOT NULL,
                    passed INTEGER NOT NULL, failed INTEGER NOT NULL,
                    PRIMARY KEY(day)
                );
            """)
            finished = conn.execute(
                "SELECT 1 FROM daily_runs WHERE day=?", (today,)
            ).fetchone()
            if finished and not force:
                return {"status": "already_completed_today", "date": today,
                        "db": str(db)}

            previous_keys = set(conn.execute(
                "SELECT ip,port,protocol FROM endpoints "
                "WHERE protocol IN ('http','socks4','socks5')"
            ))
            inventory = collect_inventory(history, max_candidates=25000, fetch=fetch)
            print("MCP_PROGRESS " + json.dumps({
                "event": "daily_inventory", "date": today,
                "endpoints_seen": inventory["unique_endpoints"],
                "sources": inventory["sources"],
            }, sort_keys=True), flush=True)
            selected, queue = plan_checks(conn, previous_keys=previous_keys,
                                          now=now, max_checks=max_checks)
            print("MCP_PROGRESS " + json.dumps({
                "event": "daily_plan", **queue,
            }, sort_keys=True), flush=True)
            totals = Counter()
            attempts = 0
            for offset in range(0, len(selected), batch_size):
                if time.monotonic() - started >= runtime_seconds:
                    break
                batch = selected[offset: offset + batch_size]
                _execute_batch(history, health, batch, timeout, workers, totals, verifier)
                attempts += len(batch)
            exports = export_projects(conn, db.parent / "exports",
                                      profiles=profiles, now=datetime.now(timezone.utc))
            with conn:
                conn.execute("""
                    INSERT INTO daily_runs
                      (day,started_at,ended_at,collected,new_candidates,
                       attempted,passed,failed)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(day) DO UPDATE SET
                      ended_at=excluded.ended_at,
                      collected=excluded.collected,
                      new_candidates=excluded.new_candidates,
                      attempted=daily_runs.attempted+excluded.attempted,
                      passed=daily_runs.passed+excluded.passed,
                      failed=daily_runs.failed+excluded.failed
                """, (today, now.isoformat(), datetime.now(timezone.utc).isoformat(),
                      inventory["unique_endpoints"], queue["new_candidates"],
                      attempts, totals["passed"], totals["failed"]))
            return {
                "status": "ok", "date": today,
                "inventory": inventory["unique_endpoints"],
                "new_candidates": queue["new_candidates"],
                "planned": len(selected),
                "attempted": attempts,
                "passed": totals["passed"],
                "failed": totals["failed"],
                "unavailable": totals["unavailable"],
                "exports": exports,
                "duration_seconds": round(time.monotonic() - started, 2),
                "db": str(db),
                "complete": attempts == len(selected),
            }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Daily proxy discovery and freshness rechecks")
    p.add_argument("--db", type=Path, default=data_directory() / "campaign.sqlite3")
    p.add_argument("--max-checks", type=int, default=10000)
    p.add_argument("--runtime-seconds", type=int, default=20400)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--batch-size", type=int, default=120)
    p.add_argument("--profiles", type=Path)
    p.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    result = run_daily(db=a.db, max_checks=a.max_checks,
                       runtime_seconds=a.runtime_seconds, timeout=a.timeout,
                       workers=a.workers, batch_size=a.batch_size,
                       profiles=a.profiles, force=a.force)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] in {"ok", "already_completed_today"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
