"""Cloudflare D1 transport and cautious SQLite reference-history synchronizer.

No private targets are exported. A Cloudflare token is read from the process
environment ONLY for an explicit --apply. Dry runs require no credentials.
The Cloudflare API is a fixed HTTPS hostname; no redirects are followed.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .campaign import TARGET
from .destination import wilson_lower

PROTOCOLS = {"http", "socks4", "socks5"}
OUTCOMES = {"success", "proxy_error", "unknown"}
MAX_RESPONSE_BYTES = 2_000_000


class D1Error(RuntimeError):
    """D1 transport or schema validation failed; secret-safe message."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class D1Client:
    """Minimal REST query client, using a caller-supplied narrowly scoped token.

    A mock transport is injectable for fully offline tests.
    """
    def __init__(self, account_id: str, database_id: str, token: str,
                 *, opener=None, sleep=time.sleep):
        import re
        if not (re.fullmatch(r"[0-9a-f]{32}", account_id) and
                re.fullmatch(r"[0-9a-fA-F-]{36}", database_id)):
            raise ValueError("Invalid Cloudflare account or D1 database ID")
        if not token or any(c.isspace() for c in token):
            raise ValueError("Cloudflare API token is required")
        self._token = token
        self._path = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/d1/database/{database_id}/query"
        )
        self._opener = opener or build_opener(ProxyHandler({}), _NoRedirect())
        self._sleep = sleep

    @classmethod
    def from_env(cls):
        missing = [
            key for key in (
                "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_D1_DATABASE_ID",
                "CLOUDFLARE_API_TOKEN",
            ) if not os.environ.get(key)
        ]
        if missing:
            raise D1Error(
                "Missing Cloudflare configuration: " + ", ".join(missing)
            )
        return cls(
            os.environ["CLOUDFLARE_ACCOUNT_ID"],
            os.environ["CLOUDFLARE_D1_DATABASE_ID"],
            os.environ["CLOUDFLARE_API_TOKEN"],
        )

    def query(self, sql: str, params=()) -> list[dict]:
        if not isinstance(sql, str) or not 0 < len(sql) <= 100_000:
            raise ValueError("Invalid D1 SQL size")
        if len(params) > 400:
            raise ValueError("Too many parameters in one query")
        body = json.dumps({
            "sql": sql,
            "params": ["" if p is None else str(p) for p in params],
        }, separators=(",", ":")).encode("utf-8")
        request = Request(
            self._path,
            data=body,
            method="POST",
            headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "system-proxy/d1-sync",
            },
        )
        for attempt in range(3):
            try:
                with self._opener.open(request, timeout=20) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise D1Error("D1 reply is unexpectedly large")
                payload = json.loads(raw)
            except HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    self._sleep(1 + attempt * 2)
                    continue
                raise D1Error(f"D1 HTTP request failed (status {exc.code})") from None
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                raise D1Error("D1 request failed or returned invalid JSON") from None
            if not isinstance(payload, dict) or not payload.get("success"):
                raise D1Error("D1 API reported a query failure")
            results = payload.get("result")
            if not isinstance(results, list) or any(
                not isinstance(item, dict) or item.get("success") is False
                for item in results
            ):
                raise D1Error("D1 SQL execution failed")
            return results
        raise D1Error("D1 request retries exhausted")

    def assert_schema(self):
        rows = self.query(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        )
        if not rows or not rows[0].get("results") or (
            rows[0]["results"][0].get("version") != 1
        ):
            raise D1Error("Expected D1 migration 0001 is missing")


def _chunks(rows, size):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def _insert(client, table, cols, rows, *, on_conflict="", expressions=None,
            batch_size=20):
    """Bounded, parameterized multi-row D1 writes with no SQL user input."""
    if not rows:
        return
    expressions = expressions or ["?"] * len(cols)
    for batch in _chunks(rows, batch_size):
        values = "(" + ",".join(expressions) + ")"
        sql = (
            f"INSERT INTO {table} ({','.join(cols)}) VALUES "
            + ",".join([values] * len(batch)) + " " + on_conflict
        )
        params = [value for row in batch for value in row]
        client.query(sql, params)


def _valid_proxy(ip, port, protocol):
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return (
        address.version == 4 and address.is_global
        and isinstance(port, int) and 1 <= port <= 65535
        and protocol in PROTOCOLS
    )


def _has_table(conn, name):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def _event_id(ip, port, proto, outcome, latency, checked_at):
    data = json.dumps(
        [ip, port, proto, outcome, latency, checked_at],
        ensure_ascii=True, separators=(",", ":"),
    )
    return hashlib.sha256(data.encode()).hexdigest()


def plan_reference_sync(sqlite_path: Path, *, max_endpoints: int = 5000,
                        max_events: int = 2000,
                        max_sources: int = 10000,
                        since_hours: int = 72,
                        now: datetime | None = None):
    """Read only an existing SQLite campaign DB; never collect or probe."""
    if not (1 <= max_endpoints <= 50000 and
            1 <= max_events <= 10000 and
            1 <= max_sources <= 50000 and
            1 <= since_hours <= 720):
        raise ValueError("D1 synchronization limits exceeded")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Timezone-aware clock required")
    if not sqlite_path.is_file():
        raise FileNotFoundError("Source SQLite campaign database does not exist")
    conn = sqlite3.connect(sqlite_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if not _has_table(conn, "endpoints"):
            raise ValueError("Not a recognized campaign database")
        seen = now.isoformat()
        selected = []
        if _has_table(conn, "provenance"):
            # Prefer recently seen sources under a bounded first-sync budget,
            # while preserving their actual first/last sighting timestamps.
            sql = (
                "SELECT e.ip,e.port,e.protocol,"
                " COALESCE((SELECT MIN(p.first_seen) FROM provenance p "
                "WHERE p.ip=e.ip AND p.port=e.port AND p.protocol=e.protocol),?),"
                " COALESCE((SELECT MAX(p.last_seen) FROM provenance p "
                "WHERE p.ip=e.ip AND p.port=e.port AND p.protocol=e.protocol),?) "
                "FROM endpoints e "
                "WHERE e.protocol IN ('http','socks4','socks5') "
                "ORDER BY 5 DESC,e.ip,e.port,e.protocol LIMIT ?"
            )
            cursor = conn.execute(sql, (seen,seen,max_endpoints))
        else:
            cursor = (
                (*row,seen,seen) for row in conn.execute(
                    "SELECT ip,port,protocol FROM endpoints "
                    "WHERE protocol IN ('http','socks4','socks5') "
                    "ORDER BY ip,port,protocol LIMIT ?", (max_endpoints,)
                )
            )
        for ip, port, proto, first_seen, last_seen in cursor:
            if _valid_proxy(ip, port, proto):
                selected.append((ip,port,proto,first_seen,last_seen))
        selected_keys = {(r[0],r[1],r[2]) for r in selected}
        sources = []
        if _has_table(conn, "provenance"):
            for ip, port, proto, source, first, last in conn.execute(
                "SELECT ip,port,protocol,source,first_seen,last_seen "
                "FROM provenance ORDER BY last_seen DESC,ip,port,protocol,source"
            ):
                if ((ip,port,proto) in selected_keys
                        and isinstance(source, str) and 1 <= len(source) <= 80):
                    sources.append((ip,port,proto,source,first,last))
                if len(sources) >= max_sources:
                    break
        events = []
        reference_keys = set()
        threshold = (now - timedelta(hours=since_hours)).isoformat()
        if _has_table(conn, "url_checks"):
            for ip, port, proto, outcome, latency, checked_at in conn.execute(
                "SELECT ip,port,protocol,outcome,latency_ms,checked_at "
                "FROM url_checks WHERE target_url=? AND checked_at>=? "
                "ORDER BY checked_at DESC,rowid DESC LIMIT ?",
                (TARGET, threshold, max_events * 2),
            ):
                key = ip,port,proto
                if key not in selected_keys or outcome not in OUTCOMES:
                    continue
                if latency is not None and not (
                    isinstance(latency,(int,float)) and 0 <= latency <= 600000
                ):
                    continue
                event_id = _event_id(ip,port,proto,outcome,latency,checked_at)
                events.append(
                    (event_id,ip,port,proto,outcome,latency,checked_at,"reference")
                )
                reference_keys.add(key)
                if len(events) >= max_events:
                    break
        scores = []
        if reference_keys:
            # Local aggregation on persistent history; does not read D1 rows.
            grouped = {}
            for ip,port,proto,outcome,stamp in conn.execute(
                "SELECT ip,port,protocol,outcome,checked_at FROM url_checks "
                "WHERE target_url=? ORDER BY checked_at,rowid", (TARGET,)
            ):
                key = ip,port,proto
                if key not in reference_keys:
                    continue
                record = grouped.setdefault(key, {
                    "success": 0, "fail": 0, "unknown": 0,
                    "last_outcome": None, "last_stamp": None
                })
                if outcome == "success":
                    record["success"] += 1
                elif outcome == "proxy_error":
                    record["fail"] += 1
                else:
                    record["unknown"] += 1
                record["last_outcome"] = outcome
                record["last_stamp"] = stamp
            for (ip, port, proto), record in grouped.items():
                scores.append((
                    "reference", "", "", ip,port,proto,
                    record["success"], record["fail"],0,record["unknown"],
                    wilson_lower(record["success"],record["fail"]),
                    record["last_outcome"], record["last_stamp"], seen
                ))
        stats = []
        if _has_table(conn, "url_checks"):
            for day,protocol,n,good,bad in conn.execute(
                "SELECT substr(checked_at,1,10),protocol,COUNT(*),"
                "SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN outcome='proxy_error' THEN 1 ELSE 0 END) "
                "FROM url_checks WHERE target_url=? AND checked_at>=? "
                "GROUP BY substr(checked_at,1,10),protocol",
                (TARGET, threshold),
            ):
                if protocol in PROTOCOLS:
                    stats.append((
                        day,"reference","","",protocol,n,good,bad,0,seen
                    ))
        return {
            "endpoints":selected,
            "sources":sources,
            "events":events,
            "scores":scores,
            "stats":stats,
            "source":str(sqlite_path),
            "window_hours":since_hours,
        }
    finally:
        conn.close()


def apply_reference_sync(client: D1Client, plan: dict) -> dict:
    """Idempotent upserts; caller must have explicitly opted into remote writes."""
    client.assert_schema()
    _insert(client, "proxy_endpoints",
            ["ip","port","protocol","first_seen_at","last_seen_at"],
            plan["endpoints"],
            on_conflict=(
                "ON CONFLICT(ip,port,protocol) DO UPDATE SET "
                "last_seen_at=MAX(proxy_endpoints.last_seen_at,excluded.last_seen_at)"
            ))
    _insert(client,"proxy_sources",
            ["ip","port","protocol","source","first_seen_at","last_seen_at"],
            plan["sources"], on_conflict=(
                "ON CONFLICT(ip,port,protocol,source) DO UPDATE SET "
                "last_seen_at=MAX(proxy_sources.last_seen_at,excluded.last_seen_at)"
            ))
    _insert(client,"reference_observations",
            ["event_id","ip","port","protocol","outcome","latency_ms","checked_at","origin_run"],
            plan["events"], on_conflict="ON CONFLICT(event_id) DO NOTHING",
            expressions=["?","?","?","?","?","NULLIF(?,'')","?","?"])
    _insert(client,"ranking_snapshots",
            ["scope","project_id","target_id","ip","port","protocol",
             "successes","failures","restrictions","unknown_count","wilson_score",
             "last_outcome","last_checked_at","computed_at"],
            plan["scores"],
            expressions=["?"] * 10 + ["NULLIF(?,'')","?","?","?"],
            on_conflict=(
                "ON CONFLICT(scope,project_id,target_id,ip,port,protocol) "
                "DO UPDATE SET successes=excluded.successes, "
                "failures=excluded.failures, restrictions=excluded.restrictions, "
                "unknown_count=excluded.unknown_count, "
                "wilson_score=excluded.wilson_score, "
                "last_outcome=excluded.last_outcome, "
                "last_checked_at=excluded.last_checked_at, "
                "computed_at=excluded.computed_at "
                "WHERE ranking_snapshots.last_checked_at IS NULL "
                "OR excluded.last_checked_at>=ranking_snapshots.last_checked_at"
            ),batch_size=15)
    _insert(client,"daily_statistics",
            ["day","scope","project_id","target_id","protocol","attempted",
             "successes","failures","restrictions","updated_at"],
            plan["stats"],
            on_conflict=(
                "ON CONFLICT(day,scope,project_id,target_id,protocol) "
                "DO UPDATE SET attempted=MAX(daily_statistics.attempted,excluded.attempted), "
                "successes=MAX(daily_statistics.successes,excluded.successes), "
                "failures=MAX(daily_statistics.failures,excluded.failures), "
                "updated_at=excluded.updated_at"
            ))
    return {
        "status":"synced",
        "endpoints":len(plan["endpoints"]),
        "sources":len(plan["sources"]),
        "reference_events":len(plan["events"]),
        "ranking_snapshots":len(plan["scores"]),
        "daily_stats_rows":len(plan["stats"]),
    }


def main(argv=None):
    p=argparse.ArgumentParser(
        description="Optional, bounded SQLite-to-Cloudflare-D1 reference sync"
    )
    p.add_argument("--db",type=Path,default=Path.home()/".local/share/system-proxy/campaign.sqlite3")
    p.add_argument("--max-endpoints",type=int,default=5000)
    p.add_argument("--max-events",type=int,default=2000)
    p.add_argument("--max-sources",type=int,default=10000)
    p.add_argument("--since-hours",type=int,default=72)
    p.add_argument("--apply",action="store_true",
                   help="Perform D1 writes using a privately supplied Cloudflare token")
    args=p.parse_args(argv)
    plan=plan_reference_sync(
        args.db,max_endpoints=args.max_endpoints,max_events=args.max_events,
        max_sources=args.max_sources,since_hours=args.since_hours
    )
    if not args.apply:
        result={"status":"dry_run","network_requests":0,
                "counts":{key:len(plan[key]) for key in
                          ("endpoints","sources","events","scores","stats")}}
    else:
        client=D1Client.from_env()
        result=apply_reference_sync(client,plan)
    print(json.dumps(result,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
