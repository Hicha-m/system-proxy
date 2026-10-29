"""Networkless, per-project/per-target observation store and conservative ranking.

Never probes targets or proxies. Only records results reported by an authorized
client that already performed an HTTPS request. A 403/429/challenge is a
destination restriction, *not* evidence that the proxy is broken.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .collect import normalize_endpoint
from .destination import classify_status, wilson_lower
from .history import History
from .target_profiles import TargetProfile

OUTCOMES = frozenset({
    "success", "proxy_error", "network_error", "target_denied",
    "rate_limited", "challenge", "unknown"
})
FAILURES = frozenset({"proxy_error", "network_error"})
RESTRICTIONS = frozenset({"target_denied", "rate_limited", "challenge"})


@dataclass(frozen=True)
class TargetObservation:
    ip: str
    port: int
    protocol: str
    outcome: str
    observed_at: str
    source: str = "client"
    http_status: int | None = None
    latency_ms: float | None = None
    verified_https: bool = False


class TargetStore:
    def __init__(self, history: History):
        self.history = history
        self.db = history.conn
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS target_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                ip TEXT NOT NULL,
                port INTEGER NOT NULL,
                protocol TEXT NOT NULL,
                outcome TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                source TEXT NOT NULL,
                http_status INTEGER,
                latency_ms REAL,
                verified_https INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(ip, port, protocol)
                    REFERENCES endpoints(ip, port, protocol)
            );
            CREATE INDEX IF NOT EXISTS idx_target_observations_project
                ON target_observations(project_id,target_id,protocol,id);
        """)

    def record(self, profile: TargetProfile, item: TargetObservation) -> None:
        """Record a client-owned observation; never initiate a network request."""
        ip, port, scheme = normalize_endpoint(f"{item.ip}:{item.port}")
        if scheme is not None or item.protocol not in profile.protocols:
            raise ValueError("Invalid endpoint or protocol for target")
        if item.outcome not in OUTCOMES:
            raise ValueError("Invalid observation outcome")
        if item.source not in ("client", "approved_probe"):
            raise ValueError("Invalid observation source")
        if type(item.verified_https) is not bool:
            raise ValueError("verified_https must be boolean")
        if item.latency_ms is not None and (
            type(item.latency_ms) not in (float, int)
            or not math.isfinite(item.latency_ms)
            or not 0 <= item.latency_ms <= 600_000
        ):
            raise ValueError("Invalid latency")
        if item.http_status is not None:
            status_kind = classify_status(item.http_status)
            if (status_kind == "target_denied" and item.outcome != "target_denied"
                    or status_kind == "rate_limited" and item.outcome != "rate_limited"
                    or status_kind == "success"
                    and item.outcome not in ("success", "challenge")):
                raise ValueError("HTTP status conflicts with reported outcome")
        if item.outcome == "success" and (
            not item.verified_https or item.http_status is None
            or classify_status(item.http_status) != "success"
        ):
            raise ValueError("A success requires verified HTTPS and a successful HTTP status")
        if item.outcome == "challenge" and item.http_status is not None and not (
            200 <= item.http_status < 400
        ):
            raise ValueError("Unsupported challenge status")
        try:
            instant = datetime.fromisoformat(item.observed_at)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid timestamp") from exc
        now = datetime.now(timezone.utc)
        if (instant.tzinfo is None or instant.utcoffset() is None
                or instant > now
                or (now - instant).total_seconds() > 90 * 24 * 3600):
            raise ValueError("Observation must be timestamped within the last 90 days")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO endpoints VALUES (?,?,?)",
                (ip, port, item.protocol)
            )
            self.db.execute("""
                INSERT INTO target_observations
                (project_id,target_id,ip,port,protocol,outcome,observed_at,
                 source,http_status,latency_ms,verified_https)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (profile.project_id, profile.target_id,
                  ip, port, item.protocol, item.outcome, instant.isoformat(),
                  item.source, item.http_status, item.latency_ms,
                  int(item.verified_https)))

    def _observations(self, profile: TargetProfile, *, protocol: str):
        if protocol not in profile.protocols:
            raise ValueError("Protocol is not enabled for this target")
        return self.db.execute("""
            SELECT ip,port,protocol,outcome,observed_at,latency_ms,http_status
              FROM target_observations
             WHERE project_id=? AND target_id=? AND protocol=?
             ORDER BY id
        """, (profile.project_id, profile.target_id, protocol))

    def ranking(self, profile: TargetProfile, *, protocol: str = "http",
                limit: int = 20, now: datetime | None = None) -> dict:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid ranking limit")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Invalid now timestamp")
        # SQLite index makes this linear in the *selected target*, not in all
        # collected endpoints. Only proxies with real observations are ranked.
        grouped = {}
        for ip,port,proto,outcome,stamp,latency,status in self._observations(
            profile, protocol=protocol
        ):
            key = ip, port, proto
            item = grouped.setdefault(key, {
                "ip": ip, "port": port, "protocol": proto,
                "successes": 0, "proxy_failures": 0,
                "restrictions": 0, "unknown": 0,
                "latest_outcome": None, "last_checked_at": None,
                "last_http_status": None, "success_latencies": [],
            })
            if outcome == "success":
                item["successes"] += 1
                if latency is not None:
                    item["success_latencies"].append(latency)
            elif outcome in FAILURES:
                item["proxy_failures"] += 1
            elif outcome in RESTRICTIONS:
                item["restrictions"] += 1
            else:
                item["unknown"] += 1
            # Last ingested observation wins, even if its client timestamp
            # predates an earlier record (avoid future-data leakage).
            item["latest_outcome"] = outcome
            item["last_checked_at"] = stamp
            item["last_http_status"] = status
        rows = []
        for item in grouped.values():
            score = wilson_lower(item["successes"], item["proxy_failures"])
            last_stamp = datetime.fromisoformat(item["last_checked_at"])
            age = (now - last_stamp).total_seconds()
            fresh = 0 <= age <= profile.max_age_hours * 3600
            eligible = (
                fresh and item["latest_outcome"] == "success"
                and item["successes"] >= profile.min_successes
                and score is not None and score >= profile.min_score
            )
            latencies = item.pop("success_latencies")
            item["avg_success_latency_ms"] = (
                round(sum(latencies) / len(latencies), 2) if latencies else None
            )
            item["score"] = score
            item["fresh"] = fresh
            item["eligible"] = eligible
            item["observations"] = (
                item["successes"] + item["proxy_failures"]
                + item["restrictions"] + item["unknown"]
            )
            rows.append(item)
        rows.sort(key=lambda x: (
            not x["eligible"], -(x["score"] or 0),
            -x["successes"], x["avg_success_latency_ms"]
            if x["avg_success_latency_ms"] is not None else float("inf"),
            x["ip"], x["port"]
        ))
        return {
            "project_id": profile.project_id,
            "target_id": profile.target_id,
            "protocol": protocol,
            "mode": "passive",
            "observed_proxies": len(rows),
            "eligible_proxies": sum(r["eligible"] for r in rows),
            "ranking": rows[:limit],
            "policy": {
                "min_successes": profile.min_successes,
                "min_score": profile.min_score,
                "max_age_hours": profile.max_age_hours,
            },
            "score_definition": "Wilson 95% lower bound; target restrictions and unknown excluded from proxy-failure denominator",
        }

    def stats(self, profile: TargetProfile) -> dict:
        counts = dict(self.db.execute("""
            SELECT outcome,COUNT(*) FROM target_observations
            WHERE project_id=? AND target_id=? GROUP BY outcome
        """, (profile.project_id, profile.target_id)))
        per_protocol = dict(self.db.execute("""
            SELECT protocol,COUNT(*) FROM target_observations
            WHERE project_id=? AND target_id=? GROUP BY protocol
        """, (profile.project_id, profile.target_id)))
        unique = self.db.execute("""
            SELECT COUNT(*) FROM (
                SELECT ip,port,protocol FROM target_observations
                WHERE project_id=? AND target_id=?
                GROUP BY ip,port,protocol
            )
        """, (profile.project_id, profile.target_id)).fetchone()[0]
        return {
            "project_id": profile.project_id,
            "target_id": profile.target_id,
            "mode": "passive", "total_observations": sum(counts.values()),
            "observed_proxies": unique,
            "by_outcome": dict(sorted(counts.items())),
            "by_protocol": dict(sorted(per_protocol.items())),
        }
