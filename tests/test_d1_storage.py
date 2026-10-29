"""Cloudflare D1 migration and synchronizer tests. Strictly offline."""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from system_proxy.d1_storage import (
    D1Client, D1Error, _event_id, _valid_proxy, apply_reference_sync,
    plan_reference_sync, main,
)
from system_proxy.history import History

MIGRATION = Path(__file__).parents[1] / "database/migrations/0001_init.sql"


class InMemoryD1:
    """Executes exactly the generated D1 SQL in SQLite for offline parity."""
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        self.queries = []

    def query(self, sql, params=()):
        self.queries.append((sql, tuple(params)))
        with self.conn:
            cur = self.conn.execute(
                sql, ["" if p is None else str(p) for p in params]
            )
            cols = [x[0] for x in cur.description] if cur.description else []
            rows = [dict(zip(cols, row)) for row in cur.fetchall()] if cols else []
        return [{"success": True, "results": rows}]

    def assert_schema(self):
        value = self.query("SELECT version FROM schema_migrations")[0]["results"][0]
        assert value["version"] == 1


class D1SchemaTests(unittest.TestCase):
    def test_schema_builds_and_has_no_sensitive_destination_url_columns(self):
        store = InMemoryD1()
        required = {
            "schema_migrations","proxy_endpoints","proxy_sources",
            "reference_observations","target_observations",
            "ranking_snapshots","collection_runs","daily_statistics",
        }
        tables = {row[0] for row in store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        self.assertTrue(required <= tables)
        target_cols = [row[1] for row in store.conn.execute(
            "PRAGMA table_info('target_observations')"
        )]
        self.assertNotIn("url", target_cols)
        self.assertNotIn("api_token", target_cols)
        self.assertNotIn("hostname", target_cols)
        self.assertEqual(store.conn.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0],1)
        store.conn.close()

    def test_constraints_and_foreign_keys_enforced(self):
        store = InMemoryD1()
        with self.assertRaises(sqlite3.IntegrityError):
            store.conn.execute(
                "INSERT INTO reference_observations "
                "(event_id,ip,port,protocol,outcome,checked_at) "
                "VALUES ('x','8.8.8.8',80,'http','success','2026-10-28T00:00:00Z')"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            store.conn.execute(
                "INSERT INTO proxy_endpoints VALUES "
                "('8.8.8.8',99999,'http','2026','2026')"
            )
        store.conn.close()


class D1ImporterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "campaign.sqlite3"
        self.now = datetime.now(timezone.utc)
        with History(self.db) as hist:
            hist.import_candidates([
                {"ip": "8.8.8.8", "port": 8080,
                 "protocols": ["http"], "sources": ["geonode"]},
                {"ip": "9.9.9.9", "port": 1080,
                 "protocols": ["socks5"], "sources": ["speedx-socks5"]},
            ])
            hist.conn.execute("""
                CREATE TABLE url_checks (
                    ip TEXT, port INTEGER, protocol TEXT, target_url TEXT,
                    outcome TEXT, latency_ms REAL, checked_at TEXT)
            """)
            for i, (outcome,latency) in enumerate([
                ("success",100.0), ("success",120.0),
                ("proxy_error",None), ("success",80.0),
            ]):
                stamp = (self.now-timedelta(minutes=5-i)).isoformat()
                hist.conn.execute(
                    "INSERT INTO url_checks VALUES (?,?,?,?,?,?,?)",
                    ("8.8.8.8",8080,"http","https://example.com/",
                     outcome,latency,stamp)
                )
            # Non-reference checks must NOT be copied to public D1.
            hist.conn.execute(
                "INSERT INTO url_checks VALUES (?,?,?,?,?,?,?)",
                ("8.8.8.8",8080,"http","https://private.example/",
                 "success",25.0,self.now.isoformat())
            )
            hist.conn.commit()

    def tearDown(self):
        self.temp.cleanup()

    def test_sync_is_idempotent_and_public_reference_only(self):
        plan = plan_reference_sync(self.db, now=self.now)
        self.assertEqual(len(plan["endpoints"]),2)
        self.assertEqual(len(plan["sources"]),2)
        self.assertEqual(len(plan["events"]),4)
        self.assertEqual(len(plan["scores"]),1)
        self.assertEqual(len(plan["stats"]),1)
        store = InMemoryD1()
        output = apply_reference_sync(store,plan)
        self.assertEqual(output["reference_events"],4)
        self.assertEqual(store.conn.execute(
            "SELECT COUNT(*) FROM proxy_endpoints"
        ).fetchone()[0], 2)
        self.assertEqual(store.conn.execute(
            "SELECT COUNT(*) FROM reference_observations"
        ).fetchone()[0],4)
        self.assertEqual(store.conn.execute(
            "SELECT successes,failures FROM ranking_snapshots"
        ).fetchone(), (3,1))
        self.assertGreater(store.conn.execute(
            "SELECT wilson_score FROM ranking_snapshots"
        ).fetchone()[0],0)
        self.assertEqual(store.conn.execute(
            "SELECT successes,failures FROM daily_statistics"
        ).fetchone(),(3,1))
        # A retry or resume cannot create duplicate history rows.
        apply_reference_sync(store,plan)
        self.assertEqual(store.conn.execute(
            "SELECT COUNT(*) FROM reference_observations"
        ).fetchone()[0],4)
        self.assertEqual(store.conn.execute(
            "SELECT COUNT(*) FROM proxy_sources"
        ).fetchone()[0],2)
        self.assertNotIn("private.example",str(store.queries))
        store.conn.close()

    def test_daily_campaign_metadata_is_idempotently_persisted(self):
        with sqlite3.connect(self.db) as db:
            db.execute("""
                CREATE TABLE daily_runs (
                    day TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT,
                    collected INTEGER, new_candidates INTEGER, attempted INTEGER,
                    passed INTEGER, failed INTEGER)
            """)
            stamp=self.now.isoformat()
            db.execute(
                "INSERT INTO daily_runs VALUES (?,?,?,?,?,?,?,?)",
                ("2026-10-29",stamp,stamp,5000,120,300,25,275)
            )
        plan=plan_reference_sync(self.db,now=self.now)
        self.assertEqual(len(plan["runs"]),1)
        store=InMemoryD1()
        apply_reference_sync(store,plan)
        self.assertEqual(store.conn.execute(
            "SELECT run_id,endpoints_seen,checks_attempted FROM collection_runs"
        ).fetchone(),("reference-2026-10-29",5000,300))
        apply_reference_sync(store,plan)
        self.assertEqual(store.conn.execute(
            "SELECT COUNT(*) FROM collection_runs"
        ).fetchone()[0],1)
        store.conn.close()

    def test_limits_and_age_filter(self):
        with self.assertRaises(ValueError):
            plan_reference_sync(self.db,max_endpoints=0)
        with self.assertRaises(ValueError):
            plan_reference_sync(self.db,max_events=10001)
        later = self.now + timedelta(days=4)
        plan = plan_reference_sync(self.db,now=later,since_hours=24)
        self.assertEqual(len(plan["events"]),0)
        self.assertEqual(len(plan["scores"]),0)

    def test_dry_run_does_not_touch_cloudflare_or_require_token(self):
        with patch.dict("os.environ",{},clear=True), patch(
            "system_proxy.d1_storage.D1Client.from_env",
            side_effect=AssertionError("Should not open remote API")
        ):
            with contextlib.redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(main(["--db",str(self.db)]),0)
        result=json.loads(stream.getvalue())
        self.assertEqual(result["status"],"dry_run")
        self.assertEqual(result["network_requests"],0)
        self.assertEqual(result["counts"]["events"],4)

    def test_import_requires_schema(self):
        class MissingSchema:
            def assert_schema(self):
                raise D1Error("No migration")
        with self.assertRaises(D1Error):
            apply_reference_sync(MissingSchema(),
                plan_reference_sync(self.db, now=self.now))


class D1TransportTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self,payload):
            self.raw=json.dumps(payload).encode()
        def __enter__(self):
            return self
        def __exit__(self,*args):
            pass
        def read(self,size):
            return self.raw[:size]

    class FakeOpener:
        def __init__(self,payload):
            self.payload=payload
            self.request=None
        def open(self,request,timeout):
            self.request=request
            return D1TransportTests.FakeResponse(self.payload)

    def test_token_remains_header_only_and_error_is_safe(self):
        opener=self.FakeOpener({
            "success":True,"result":[{"success":True,"results":[{"version":1}]}]
        })
        client=D1Client("a"*32,"b"*8+"-"+ "b"*4+"-"+"b"*4+"-"+"b"*4+"-"+"b"*12,
                        "token-do-not-log",opener=opener)
        self.assertEqual(client.query("SELECT version FROM schema_migrations")[0]
                         ["results"][0]["version"],1)
        self.assertEqual(opener.request.get_header("Authorization"),"Bearer token-do-not-log")
        self.assertNotIn("token-do-not-log",opener.request.data.decode())
        self.assertEqual(opener.request.full_url.split("/")[2],"api.cloudflare.com")
        bad=self.FakeOpener({"success":False,"errors":[{"message":"token-do-not-log"}]})
        client._opener=bad
        with self.assertRaises(D1Error) as exc:
            client.query("SELECT 1")
        self.assertNotIn("token-do-not-log",str(exc.exception))

    def test_invalid_cloudflare_ids_fail_closed(self):
        with self.assertRaises(ValueError):
            D1Client("not-an-account","not-a-database","token")

    def test_event_ids_stable(self):
        a=_event_id("8.8.8.8",80,"http","success",25,"2026-10-29")
        self.assertEqual(a,_event_id("8.8.8.8",80,"http","success",25,"2026-10-29"))
        self.assertNotEqual(a,_event_id("8.8.8.8",80,"http","proxy_error",25,"2026-10-29"))
        self.assertTrue(_valid_proxy("8.8.8.8",80,"http"))
        self.assertFalse(_valid_proxy("127.0.0.1",80,"http"))


if __name__=="__main__":
    unittest.main()
