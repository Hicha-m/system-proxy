"""P2 observability, confidentiality, resource bounds and concurrency regression."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from system_proxy.benchmark import benchmark
from system_proxy.history import History
from system_proxy.observability import (
    load_observability, render_dashboard, write_private,
)
from system_proxy.verification import Check


NOW = datetime.now(timezone.utc)


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "campaign.sqlite3"
        with History(self.db) as h:
            h.import_candidates([{
                "ip": "8.8.8.8", "port": 8080,
                "protocols": ["http"],
                "sources": ["geonode", "secret-token-marker", "unrecognized-project"],
            }])
            h.add_checks([Check(
                "8.8.8.8", 8080, "http", "failed", None,
                "secret-unknown-diagnostic", NOW.isoformat(),
            )])
            h.conn.executescript("""
                CREATE TABLE daily_runs (
                    day TEXT, started_at TEXT, ended_at TEXT, collected INTEGER,
                    new_candidates INTEGER, attempted INTEGER,
                    passed INTEGER, failed INTEGER);
                CREATE TABLE url_checks (
                    ip TEXT, port INTEGER, protocol TEXT, target_url TEXT,
                    outcome TEXT, latency_ms REAL, checked_at TEXT);
                CREATE TABLE destination_observations (
                    outcome TEXT, observed_at TEXT, domain TEXT);
                CREATE TABLE target_observations (
                    outcome TEXT, observed_at TEXT, project_id TEXT);
                CREATE TABLE gateway_attempts (
                    outcome TEXT, attempted_at TEXT, domain TEXT);
            """)
            h.conn.execute(
                "INSERT INTO daily_runs VALUES (?,?,?,?,?,?,?,?)",
                (NOW.date().isoformat(), NOW.isoformat(), NOW.isoformat(),
                 12, 3, 10, 8, 2),
            )
            h.conn.execute("INSERT INTO url_checks VALUES (?,?,?,?,?,?,?)", (
                "8.8.8.8", 8080, "http", "https://example.com/",
                "success", 45, NOW.isoformat(),
            ))
            h.conn.execute("INSERT INTO url_checks VALUES (?,?,?,?,?,?,?)", (
                "8.8.8.8", 8080, "http", "https://example.com/",
                "proxy_error", 45, (NOW - timedelta(days=40)).isoformat(),
            ))
            h.conn.execute("INSERT INTO destination_observations VALUES (?,?,?)",
                           ("target_denied", NOW.isoformat(), "private.example"))
            h.conn.execute("INSERT INTO target_observations VALUES (?,?,?)",
                           ("rate_limited", NOW.isoformat(), "secret-project"))
            h.conn.execute("INSERT INTO gateway_attempts VALUES (?,?,?)",
                           ("connected", NOW.isoformat(), "secret.example"))
            h.conn.commit()

    def tearDown(self):
        self.temp.cleanup()

    def test_aggregates_without_sensitive_details_and_unchanged_db(self):
        before = self.db.read_bytes()
        report = load_observability(self.db, now=NOW)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["inventory_total"], 1)
        self.assertEqual(report["reference_outcomes"]["success"], 1)
        self.assertNotIn("proxy_error", report["reference_outcomes"])
        self.assertEqual(report["destination_outcomes"]["target_denied"], 1)
        self.assertEqual(report["target_outcomes"]["rate_limited"], 1)
        self.assertEqual(report["gateway_outcomes"]["connected"], 1)
        self.assertEqual(report["check_reasons"]["other"], 1)
        self.assertEqual(report["sources"], [
            {"source": "other", "endpoints": 2},
            {"source": "geonode", "endpoints": 1},
        ])
        payload = json.dumps(report)
        html = render_dashboard(report)
        for secret in ("8.8.8.8", "secret-token", "unrecognized-project", "secret-project",
                       "private.example", "secret-unknown-diagnostic"):
            self.assertNotIn(secret, payload)
            self.assertNotIn(secret, html)
        self.assertEqual(report["network_requests"], 0)
        self.assertEqual(before, self.db.read_bytes())

    def test_dashboard_is_static_and_network_disabled(self):
        page = render_dashboard(load_observability(self.db))
        self.assertIn("Content-Security-Policy", page)
        self.assertIn("connect-src 'none'", page)
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("<iframe", page.lower())
        self.assertNotIn("https://", page.lower())
        path = self.root / "private" / "dashboard.html"
        write_private(path, page)
        self.assertEqual(path.read_text(), page)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_missing_database_cannot_create_an_empty_one(self):
        missing = self.root / "missing.sqlite3"
        report = load_observability(missing)
        self.assertEqual(report["status"], "no_database")
        self.assertFalse(missing.exists())

    def test_symlink_database_and_output_refused(self):
        link = self.root / "db-link.sqlite3"
        link.symlink_to(self.db)
        with self.assertRaises(ValueError):
            load_observability(link)
        report = self.root / "existing.txt"
        report.write_text("unchanged")
        output = self.root / "link.txt"
        output.symlink_to(report)
        with self.assertRaises(ValueError):
            write_private(output, "unsafe")
        self.assertEqual(report.read_text(), "unchanged")

    def test_bounded_days_and_timezone(self):
        for days in (0, 91, -1, True):
            with self.subTest(days=days), self.assertRaises(ValueError):
                load_observability(self.db, days=days)
        with self.assertRaises(ValueError):
            load_observability(self.db, now=datetime(2026, 1, 1))

    def test_partial_schema_is_supported(self):
        partial = self.root / "empty.sqlite3"
        with sqlite3.connect(partial) as conn:
            conn.execute("CREATE TABLE endpoints (ip TEXT, port INT, protocol TEXT)")
            conn.execute("INSERT INTO endpoints VALUES ('192.0.2.1',1234,'http')")
        report = load_observability(partial)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["inventory_total"], 1)
        self.assertIn("no_daily_campaign_record", report["alerts"])


class BenchmarkTests(unittest.TestCase):
    def test_small_synthetic_parallel_run(self):
        result = benchmark(endpoints=120, workers=2, queries=6)
        self.assertEqual(result["mode"], "synthetic_sqlite_only")
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(result["endpoints"], 120)
        self.assertTrue(result["temporary_fixture_deleted"])
        self.assertFalse(result["user_data_accessed"])
        self.assertGreaterEqual(result["query_p95_ms"], result["query_p50_ms"])
        self.assertGreater(result["insert_rows_per_second"], 0)

    def test_limits_reject_unbounded_resource_requests(self):
        for options in (
            {"endpoints": 1}, {"endpoints": 20001},
            {"workers": 0}, {"workers": 9},
            {"queries": 0}, {"queries": 129},
            {"endpoints": True},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                benchmark(**options)


if __name__ == "__main__":
    unittest.main()
