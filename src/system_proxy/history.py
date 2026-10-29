"""SQLite-backed observations, provenance and conservative source reporting."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .collect import normalize_endpoint
from .verification import Check
from .reliability import temporal_wilson


class History:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path), timeout=15)
        self.conn.execute("PRAGMA busy_timeout=15000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS endpoints (
            ip TEXT NOT NULL, port INTEGER NOT NULL, protocol TEXT NOT NULL,
            PRIMARY KEY(ip, port, protocol)
        );
        CREATE TABLE IF NOT EXISTS provenance (
            ip TEXT NOT NULL, port INTEGER NOT NULL, protocol TEXT NOT NULL,
            source TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            PRIMARY KEY(ip, port, protocol, source),
            FOREIGN KEY(ip, port, protocol) REFERENCES endpoints(ip, port, protocol)
        );
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL, port INTEGER NOT NULL, protocol TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('passed','failed','unavailable')),
            latency_ms REAL, reason TEXT NOT NULL, checked_at TEXT NOT NULL,
            FOREIGN KEY(ip, port, protocol) REFERENCES endpoints(ip, port, protocol)
        );
        CREATE INDEX IF NOT EXISTS idx_checks_endpoint
            ON checks(ip, port, protocol, id DESC);
        """)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def import_candidates(self, proxies: list[dict]) -> int:
        """Persist validated public proxy candidates and source associations."""
        now = datetime.now(timezone.utc).isoformat()
        count = 0
        with self.conn:
            for item in proxies:
                ip, port, _ = normalize_endpoint(f"{item['ip']}:{item['port']}")
                protocols = set(item.get("protocols", []))
                sources = set(item.get("sources", []))
                # Never import arbitrary source names/oversized metadata.
                for protocol in protocols & {"http", "https", "socks4", "socks5"}:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO endpoints VALUES (?,?,?)",
                        (ip, port, protocol))
                    count += 1
                    for source in sources:
                        if not isinstance(source, str) or not (1 <= len(source) <= 80):
                            continue
                        self.conn.execute("""
                        INSERT INTO provenance VALUES (?,?,?,?,?,?)
                        ON CONFLICT(ip,port,protocol,source)
                        DO UPDATE SET last_seen=excluded.last_seen
                        """, (ip, port, protocol, source, now, now))
        return count

    def add_checks(self, checks: list[Check]) -> None:
        with self.conn:
            for c in checks:
                ip, port, _ = normalize_endpoint(f"{c.ip}:{c.port}")
                if c.protocol not in {"http", "https", "socks4", "socks5"}:
                    raise ValueError("Invalid protocol")
                self.conn.execute("INSERT OR IGNORE INTO endpoints VALUES (?,?,?)",
                                  (ip, port, c.protocol))
                self.conn.execute("""
                    INSERT INTO checks(ip,port,protocol,status,latency_ms,reason,checked_at)
                    VALUES (?,?,?,?,?,?,?)
                """, (ip, port, c.protocol, c.status, c.latency_ms,
                      c.reason[:100], c.checked_at))

    def candidates(self, protocol: str = "http", limit: int = 100) -> list[tuple[str, int, str]]:
        if protocol not in {"http", "https", "socks4", "socks5"} or not 1 <= limit <= 1000:
            raise ValueError("Invalid query")
        return [(ip, port, proto) for ip, port, proto in self.conn.execute("""
          SELECT ip, port, protocol FROM endpoints WHERE protocol = ?
          ORDER BY (
             SELECT MAX(id) FROM checks c
             WHERE c.ip=endpoints.ip AND c.port=endpoints.port
               AND c.protocol=endpoints.protocol
          ) ASC NULLS FIRST, ip, port LIMIT ?
        """, (protocol, limit))]

    def summary(self, *, stale_after_seconds: int = 3600) -> dict:
        if stale_after_seconds <= 0:
            raise ValueError("Invalid freshness window")
        now = datetime.now(timezone.utc)
        output: list[dict] = []
        # Keep the checks with status=unavailable out of the reliability
        # denominator: they do not establish a proxy failure.
        for ip, port, proto in self.conn.execute(
            "SELECT ip, port, protocol FROM endpoints ORDER BY protocol, ip, port"
        ).fetchall():
            rows = self.conn.execute("""
            SELECT status, latency_ms, checked_at, reason
            FROM checks WHERE ip=? AND port=? AND protocol=? ORDER BY id DESC
            """, (ip, port, proto)).fetchall()
            passed = sum(row[0] == "passed" for row in rows)
            failed = sum(row[0] == "failed" for row in rows)
            n = passed + failed
            # Wilson 95% lower confidence bound avoids rating 1/1 as 100%.
            if n:
                z = 1.96
                p = passed / n
                base = p + z*z/(2*n)
                delta = z*math.sqrt((p*(1-p) + z*z/(4*n))/n)
                lower = (base - delta)/(1+z*z/n)
                score = round(100 * max(0.0, lower), 1)
            else:
                score = None
            temporal_score = temporal_wilson(
                ((row[0], row[2]) for row in rows), now=now,
            )
            last = rows[0] if rows else None
            age = None
            if last:
                try:
                    age = max(0, (now - datetime.fromisoformat(last[2])).total_seconds())
                except ValueError:
                    age = None
            fresh = age is not None and age <= stale_after_seconds
            sources = [r[0] for r in self.conn.execute("""
              SELECT source FROM provenance
              WHERE ip=? AND port=? AND protocol=? ORDER BY source
            """, (ip,port,proto))]
            # Fail closed when most recent check failed or is unavailable.
            usable = bool(fresh and last and last[0] == "passed")
            output.append({
                "ip": ip, "port": port, "protocol": proto, "sources": sources,
                "passed": passed, "failed": failed,
                "unavailable": len(rows)-n,
                "reliability_score": score,
                "temporal_score": temporal_score,
                "latest_status": last[0] if last else "untested",
                "last_checked_at": last[2] if last else None,
                "fresh": fresh,
                "usable_by_recent_check": usable,
                "last_latency_ms": last[1] if last else None,
            })
        source_report: dict[str, dict] = {}
        for endpoint in output:
            for source in endpoint["sources"]:
                stat = source_report.setdefault(source, {
                    "endpoints": 0, "recently_passing": 0,
                    "exclusive_recently_passing": 0})
                stat["endpoints"] += 1
                if endpoint["usable_by_recent_check"]:
                    stat["recently_passing"] += 1
                    if len(endpoint["sources"]) == 1:
                        stat["exclusive_recently_passing"] += 1
        return {"checked_endpoints": sum(e["latest_status"] != "untested" for e in output),
                "total_endpoints": len(output), "freshness_seconds": stale_after_seconds,
                "endpoints": output, "source_stats": source_report,
                "score_definition": "Legacy all-time Wilson score; temporal_score is 30-day, 7-day-half-life Wilson, excluding unavailable"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
