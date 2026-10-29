"""Per-destination passive outcome observations and conservative ranking.

This module NEVER makes HTTP requests. Callers report outcomes of their own
authorized requests. HTTP 403/429/challenge cannot establish a proxy failure.
"""
from __future__ import annotations

import ipaddress
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .collect import normalize_endpoint
from .history import History

OUTCOMES = frozenset({
    "success", "target_denied", "rate_limited", "challenge",
    "proxy_error", "network_error", "unknown",
})
PROTOCOLS = frozenset({"http", "https", "socks4", "socks5"})


def domain_key(domain: str) -> str:
    """Canonical hostname only: no URL, credentials, port, or network request."""
    if not isinstance(domain, str):
        raise ValueError("Hostname must be text")
    value = domain.strip().rstrip(".").lower()
    if len(value) > 253 or not value or "://" in value or any(c in value for c in "/:@?#\\ "):
        raise ValueError("Provide only a domain hostname")
    try:
        ascii_name = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Invalid domain") from exc
    if not (1 <= len(ascii_name) <= 253):
        raise ValueError("Invalid domain length")
    parts = ascii_name.split(".")
    if len(parts) < 2 or any(
        not 1 <= len(p) <= 63
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", p)
        for p in parts
    ):
        raise ValueError("Invalid domain")
    try:
        ipaddress.ip_address(ascii_name)
    except ValueError:
        pass
    else:
        raise ValueError("Use a hostname, not an IP address")
    # Local and internal hostnames are never valid as a destination key.
    if parts[-1] in {"local", "localhost", "internal", "test", "invalid"}:
        raise ValueError("Internal/reserved host")
    return ascii_name


def classify_status(status: int | None) -> str:
    """HTTP status alone does not distinguish target blocks from proxy failure."""
    if status is None:
        return "unknown"
    if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        raise ValueError("Invalid HTTP status")
    if 200 <= status <= 399:
        return "success"
    if status == 403:
        return "target_denied"
    if status == 429:
        return "rate_limited"
    return "unknown"


def wilson_lower(successes: int, failures: int) -> float | None:
    n = successes + failures
    if not n:
        return None
    z = 1.96
    p = successes / n
    delta = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return round(100 * (p + z*z / (2*n) - delta) / (1 + z*z/n), 1)


@dataclass(frozen=True)
class Observation:
    ip: str
    port: int
    protocol: str
    domain: str
    outcome: str
    observed_at: str
    http_status: int | None = None
    latency_ms: float | None = None


class DestinationHealth:
    def __init__(self, history: History):
        self.history = history
        self.db = history.conn
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS destination_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT NOT NULL, port INTEGER NOT NULL, protocol TEXT NOT NULL,
            domain TEXT NOT NULL, outcome TEXT NOT NULL,
            http_status INTEGER, latency_ms REAL, observed_at TEXT NOT NULL,
            FOREIGN KEY(ip,port,protocol) REFERENCES endpoints(ip,port,protocol)
          );
          CREATE INDEX IF NOT EXISTS idx_destination_domain
            ON destination_observations(domain,protocol,ip,port,id DESC);
        """)

    def record(self, item: Observation) -> None:
        ip, port, specified = normalize_endpoint(f"{item.ip}:{item.port}")
        if item.protocol not in PROTOCOLS or specified:
            raise ValueError("Unsupported protocol")
        domain = domain_key(item.domain)
        if item.outcome not in OUTCOMES:
            raise ValueError("Invalid outcome")
        if item.http_status is not None:
            classify_status(item.http_status)
        if item.latency_ms is not None and not (0 <= item.latency_ms <= 600_000 and math.isfinite(item.latency_ms)):
            raise ValueError("Invalid latency")
        stamp = datetime.fromisoformat(item.observed_at)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Timezone-aware timestamp required")
        if stamp > datetime.now(timezone.utc):
            raise ValueError("Future timestamp")
        with self.db:
            # Allow passive observations for candidates not yet imported.
            self.db.execute("INSERT OR IGNORE INTO endpoints VALUES (?,?,?)",
                            (ip, port, item.protocol))
            self.db.execute("""
              INSERT INTO destination_observations
              (ip,port,protocol,domain,outcome,http_status,latency_ms,observed_at)
              VALUES (?,?,?,?,?,?,?,?)
            """, (ip, port, item.protocol, domain, item.outcome,
                  item.http_status, item.latency_ms, stamp.isoformat()))

    def report(self, domain: str, *, protocol: str = "http",
               freshness_seconds: int = 3600, limit: int = 100) -> dict:
        hostname = domain_key(domain)
        if protocol not in PROTOCOLS or not (1 <= freshness_seconds <= 2_592_000 and 1 <= limit <= 1000):
            raise ValueError("Invalid report parameters")
        now = datetime.now(timezone.utc)
        candidates = self.db.execute("""
          SELECT ip,port,protocol FROM endpoints WHERE protocol=?
          ORDER BY ip,port
        """, (protocol,)).fetchall()
        output = []
        for ip, port, proto in candidates:
            history = self.db.execute("""
              SELECT outcome,http_status,latency_ms,observed_at
              FROM destination_observations
              WHERE ip=? AND port=? AND protocol=? AND domain=?
              ORDER BY observed_at DESC,id DESC
            """, (ip,port,proto,hostname)).fetchall()
            success = sum(o[0]=="success" for o in history)
            # Restrictions describe the target, NOT a broken proxy. Keep them
            # visible and ineligible, without depressing transport reliability.
            restrictions = sum(o[0] in {"target_denied","rate_limited","challenge"}
                               for o in history)
            fail = sum(o[0] in {"proxy_error","network_error"} for o in history)
            # App success scoring: target denial is a destination outcome;
            # it does NOT change the proxy's global health score.
            latest = history[0] if history else None
            fresh = False
            if latest:
                age = (now - datetime.fromisoformat(latest[3])).total_seconds()
                fresh = 0 <= age <= freshness_seconds
            global_status = self.db.execute("""
              SELECT status,checked_at FROM checks
              WHERE ip=? AND port=? AND protocol=?
              ORDER BY checked_at DESC,id DESC LIMIT 1
            """, (ip,port,proto)).fetchone()
            global_ok = None
            if global_status:
                global_age = (now-datetime.fromisoformat(global_status[1])).total_seconds()
                global_ok = (global_status[0]=="passed" and 0 <= global_age <= freshness_seconds)
            output.append({
                "ip":ip,"port":port,"protocol":proto,"domain":hostname,
                "observations":len(history),"success":success,
                "denied_or_failed":fail+restrictions,
                "transport_failures":fail,"restrictions":restrictions,
                "unknown":len(history)-success-fail-restrictions,
                "destination_score":wilson_lower(success,fail),
                "destination_latest_outcome":latest[0] if latest else None,
                "destination_fresh":fresh,
                "global_recently_passing":global_ok,
                "eligible":bool(fresh and latest[0]=="success" and global_ok is not False),
                "last_http_status":latest[1] if latest else None,
            })
        # Untested endpoints must never outrank recently successful ones.
        output.sort(key=lambda r: (
            not r["eligible"],
            -(r["destination_score"] if r["destination_score"] is not None else -1),
            -r["success"],
            r["ip"],r["port"],
        ))
        return {
            "domain":hostname, "protocol":protocol,
            "policy":"passive_observations_only",
            "freshness_seconds":freshness_seconds,
            "total_candidates":len(output),
            "ranked":output[:limit],
            "score_definition":"Wilson 95% lower bound of application successes versus transport failures; target restrictions excluded",
        }
