"""Append-only local upstream CONNECT attempts, scoped to their destination.

Gateway events are NOT HTTPS application response codes and MUST NOT change
the global checker score or passive per-domain HTTP outcome score.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .collect import normalize_endpoint
from .destination import domain_key
from .history import History

OUTCOMES = frozenset({"connected", "connect_error", "denied"})
REASONS = frozenset({"ok", "timeout", "network", "handshake", "policy", "unknown"})
PROTOCOLS = frozenset({"http", "socks4", "socks5"})


class GatewayAttempts:
    """Use a caller-owned History connection; safe to open from separate threads."""

    def __init__(self, history: History):
        self.db = history.conn
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS gateway_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL,
                port INTEGER NOT NULL,
                protocol TEXT NOT NULL,
                domain TEXT NOT NULL,
                outcome TEXT NOT NULL
                    CHECK(outcome IN ('connected','connect_error','denied')),
                reason TEXT NOT NULL,
                latency_ms REAL,
                attempted_at TEXT NOT NULL,
                FOREIGN KEY(ip, port, protocol)
                    REFERENCES endpoints(ip, port, protocol)
            );
            CREATE INDEX IF NOT EXISTS idx_gateway_attempt_scope
                ON gateway_attempts(domain, protocol, attempted_at DESC, id DESC);
        """)

    def record(self, ip: str, port: int, protocol: str, domain: str,
               *, outcome: str, reason: str = "ok",
               latency_ms: float | None = None,
               at: datetime | None = None) -> None:
        ip, port, specified = normalize_endpoint(f"{ip}:{port}")
        domain = domain_key(domain)
        if specified or protocol not in PROTOCOLS or outcome not in OUTCOMES or reason not in REASONS:
            raise ValueError("Invalid upstream result")
        if (latency_ms is not None and
                (not isinstance(latency_ms, (int, float))
                 or not math.isfinite(latency_ms)
                 or not 0 <= latency_ms <= 120_000)):
            raise ValueError("Invalid duration")
        stamp = at or datetime.now(timezone.utc)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Timezone-aware timestamp required")
        stamp = stamp.astimezone(timezone.utc)
        if stamp > datetime.now(timezone.utc):
            raise ValueError("Future timestamp")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO endpoints VALUES (?,?,?)",
                            (ip, port, protocol))
            self.db.execute("""
                INSERT INTO gateway_attempts
                    (ip,port,protocol,domain,outcome,reason,latency_ms,attempted_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (ip, port, protocol, domain, outcome, reason,
                  latency_ms, stamp.isoformat()))

    def cooling_down(self, domain: str, protocol: str, *,
                     cooldown_seconds: int = 90,
                     now: datetime | None = None) -> set[tuple[str, int]]:
        hostname = domain_key(domain)
        if protocol not in PROTOCOLS or not 1 <= cooldown_seconds <= 3600:
            raise ValueError("Invalid cooldown")
        current = now or datetime.now(timezone.utc)
        threshold = (current - timedelta(seconds=cooldown_seconds)).astimezone(timezone.utc).isoformat()
        rows = self.db.execute("""
            SELECT ip,port,outcome FROM gateway_attempts
            WHERE domain=? AND protocol=? AND attempted_at>=?
            ORDER BY id DESC
        """, (hostname, protocol, threshold))
        # Only the latest attempt for each endpoint is decisive:
        # a subsequent successful tunnel removes an earlier cooldown.
        seen: set[tuple[str, int]] = set()
        blocked: set[tuple[str, int]] = set()
        for ip, port, outcome in rows:
            key = (ip, port)
            if key in seen:
                continue
            seen.add(key)
            if outcome != "connected":
                blocked.add(key)
        return blocked

    def summary(self, domain: str, *, protocol: str = "http",
                limit: int = 25) -> dict:
        hostname = domain_key(domain)
        if protocol not in PROTOCOLS or not 1 <= limit <= 100:
            raise ValueError("Invalid summary parameters")
        grouped = {
            row[0]: row[1] for row in self.db.execute("""
                SELECT outcome, COUNT(*) FROM gateway_attempts
                WHERE domain=? AND protocol=? GROUP BY outcome
            """, (hostname, protocol))
        }
        recent = [
            {"ip":ip,"port":port,"outcome":outcome,"reason":reason,
             "latency_ms":latency,"attempted_at":at}
            for ip,port,outcome,reason,latency,at in self.db.execute("""
                SELECT ip,port,outcome,reason,latency_ms,attempted_at
                FROM gateway_attempts
                WHERE domain=? AND protocol=?
                ORDER BY id DESC LIMIT ?
            """, (hostname, protocol, limit))
        ]
        return {
            "domain":hostname, "protocol":protocol,
            "total":sum(grouped.values()),
            "connected":grouped.get("connected", 0),
            "connect_error":grouped.get("connect_error", 0),
            "denied":grouped.get("denied", 0),
            "recent_attempts":recent,
            "scope":"upstream_tunnel_establishment_only",
        }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="system-proxy-gateway-stats",
        description="Read per-domain upstream CONNECT history (no network access).",
    )
    p.add_argument("--db", type=Path, default=Path("proxy-history.sqlite3"))
    p.add_argument("--domain", required=True)
    p.add_argument("--protocol", choices=sorted(PROTOCOLS), default="http")
    p.add_argument("--limit", type=int, default=25)
    args = p.parse_args(argv)
    try:
        with History(args.db) as h:
            stats = GatewayAttempts(h).summary(args.domain, protocol=args.protocol, limit=args.limit)
    except ValueError as exc:
        p.error(str(exc))
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
