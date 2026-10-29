"""P2: deterministic SQLite throughput benchmark with synthetic TEST-only data.

No real proxy is contacted, and no user database is opened or modified.
The temporary fixture is isolated and automatically deleted.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import resource
import sqlite3
import statistics
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


def benchmark(*, endpoints: int = 2000, workers: int = 4,
              queries: int = 32) -> dict:
    if type(endpoints) is not int or not 100 <= endpoints <= 20000:
        raise ValueError("endpoints must be between 100 and 20000")
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    if type(queries) is not int or not 1 <= queries <= 128:
        raise ValueError("queries must be between 1 and 128")
    start = time.perf_counter()
    now = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix="proxy-p2-bench-") as work:
        db = Path(work) / "synthetic.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.executescript("""
                CREATE TABLE endpoints (
                  ip TEXT NOT NULL, port INTEGER NOT NULL, protocol TEXT NOT NULL,
                  PRIMARY KEY(ip,port,protocol));
                CREATE TABLE url_checks (
                  ip TEXT NOT NULL, port INTEGER NOT NULL, protocol TEXT NOT NULL,
                  target_url TEXT NOT NULL, outcome TEXT NOT NULL,
                  latency_ms REAL, checked_at TEXT NOT NULL);
                CREATE INDEX idx_reference_bench ON url_checks
                  (target_url, protocol, checked_at);
            """)
            # 198.18.0.0/15 is reserved for benchmarking; no networking occurs.
            records = [
                (f"198.18.{i // 256}.{i % 256}", 8000 + i % 50000,
                 ("http", "socks4", "socks5")[i % 3])
                for i in range(endpoints)
            ]
            insert_start = time.perf_counter()
            conn.executemany("INSERT INTO endpoints VALUES (?,?,?)", records)
            conn.executemany(
                "INSERT INTO url_checks VALUES (?,?,?,?,?,?,?)",
                [
                    (ip, port, protocol, "https://example.com/",
                     ("success", "proxy_error", "unknown")[i % 3], 45 + i % 300, now)
                    for i, (ip, port, protocol) in enumerate(records)
                ],
            )
            conn.commit()
            insert_seconds = time.perf_counter() - insert_start
        uri = "file:" + quote(str(db.resolve()), safe="/") + "?mode=ro"

        def read_aggregate(index: int) -> float:
            started = time.perf_counter()
            with sqlite3.connect(uri, uri=True, timeout=4) as reader:
                reader.execute("PRAGMA query_only=ON")
                result = reader.execute("""
                    SELECT outcome,COUNT(*) FROM url_checks
                    WHERE target_url=? AND protocol=? GROUP BY outcome
                """, ("https://example.com/",
                      ("http", "socks4", "socks5")[index % 3])).fetchall()
                if sum(count for _, count in result) == 0:
                    raise RuntimeError("Missing synthetic benchmark rows")
            return (time.perf_counter() - started) * 1000

        query_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            samples = list(pool.map(read_aggregate, range(queries)))
        query_seconds = time.perf_counter() - query_start
        sorted_samples = sorted(samples)
        p95_index = max(0, math.ceil(0.95 * len(samples)) - 1)
        return {
            "mode": "synthetic_sqlite_only",
            "network_requests": 0,
            "user_data_accessed": False,
            "temporary_fixture_deleted": True,
            "endpoints": endpoints,
            "workers": workers,
            "queries": queries,
            "insert_seconds": round(insert_seconds, 4),
            "insert_rows_per_second": round(2 * endpoints / max(insert_seconds, 1e-9)),
            "query_p50_ms": round(statistics.median(sorted_samples), 3),
            "query_p95_ms": round(sorted_samples[p95_index], 3),
            "query_throughput_per_second": round(queries / max(query_seconds, 1e-9), 2),
            "peak_process_rss_kib_linux": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "total_seconds": round(time.perf_counter() - start, 3),
            "warning": "SQLite microbenchmark only. Not live proxy throughput or Codespace capacity.",
        }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bounded networkless SQLite microbenchmark")
    parser.add_argument("--endpoints", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--queries", type=int, default=32)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(benchmark(
            endpoints=args.endpoints, workers=args.workers, queries=args.queries
        ), sort_keys=True))
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
