"""P1: deterministic, networkless reliability and retention regression tests."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import socket
import ssl

from system_proxy.destination import DestinationHealth, Observation
from system_proxy.history import History
from system_proxy.project_export import export_projects
from system_proxy.retention import prune_history
from system_proxy.persistence import _backup_dir
from system_proxy.verification import Check, check_one, classify_probe_exception

NOW = datetime.now(timezone.utc)


class P1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name) / "state"
        self.folder.mkdir()
        self.db = self.folder / "campaign.sqlite3"
        self.h = History(self.db)
        self.h.import_candidates([{"ip":"8.8.8.8","port":8080,"protocols":["http"],"sources":["geonode"]}])
        self.h.conn.execute("""CREATE TABLE url_checks (
            ip TEXT,port INTEGER,protocol TEXT,target_url TEXT,
            outcome TEXT,latency_ms REAL,checked_at TEXT)""")
        self.h.conn.commit()

    def tearDown(self):
        self.h.close()
        self.temp.cleanup()

    def insert(self, stamp, outcome, ip="8.8.8.8", port=8080):
        self.h.conn.execute(
            "INSERT INTO url_checks VALUES (?,?,?,?,?,?,?)",
            (ip,port,"http","https://example.com/",outcome,20,stamp.isoformat())
        )
        self.h.conn.commit()

    def test_recent_window_excludes_old_success_and_stale_proxy(self):
        self.insert(NOW-timedelta(days=40),"success")
        self.insert(NOW-timedelta(days=39),"success")
        self.insert(NOW-timedelta(minutes=1),"success")
        self.assertEqual(export_projects(self.h.conn,self.folder/"out",now=NOW)["reference-http"],0)
        self.insert(NOW-timedelta(minutes=2),"success")
        exports=export_projects(self.h.conn,self.folder/"out",now=NOW)
        self.assertEqual(exports["reference-http"],1)
        doc=json.loads((self.folder/"out/reference-http/proxies.json").read_text())
        self.assertEqual(doc["proxies"][0]["observations"],2)
        self.assertEqual(doc["proxies"][0]["score_window_hours"],168)

    def test_latest_is_timestamp_not_last_insert(self):
        self.insert(NOW-timedelta(seconds=3),"success")
        self.insert(NOW-timedelta(seconds=2),"success")
        self.insert(NOW-timedelta(days=40),"proxy_error") # late arrival, outside score window
        self.assertEqual(export_projects(self.h.conn,self.folder/"out",now=NOW)["reference-http"],1)

    def test_per_domain_restrictions_excluded_from_failure_score(self):
        d=DestinationHealth(self.h)
        for outcome, status in [("success",200),("target_denied",403),
                                ("rate_limited",429),("challenge",None)]:
            d.record(Observation("8.8.8.8",8080,"http","shop.example",outcome,
                                 NOW.isoformat(),http_status=status))
        item=next(x for x in d.report("shop.example")["ranked"] if x["ip"]=="8.8.8.8")
        self.assertEqual(item["restrictions"],3)
        self.assertEqual(item["transport_failures"],0)
        self.assertEqual(item["denied_or_failed"],3)
        self.assertFalse(item["eligible"])
        self.assertGreater(item["destination_score"],0)
        d.record(Observation("8.8.8.8",8080,"http","shop.example","proxy_error",
                             NOW.isoformat()))
        item=next(x for x in d.report("shop.example")["ranked"] if x["ip"]=="8.8.8.8")
        self.assertEqual(item["transport_failures"],1)
        self.assertEqual(item["restrictions"],3)

    def test_error_taxonomy_never_includes_exception_message(self):
        errors=[(PermissionError("secret"),"unavailable","network_policy_denied"),
                (socket.gaierror(-2,"internal target"),"unavailable","dns_resolution_failed"),
                (TimeoutError("secret"),"failed","timeout"),
                (ConnectionRefusedError("secret"),"failed","proxy_connection_error"),
                (HTTPError("https://internal/",429,"secret",{},None),"unavailable","proxy_or_target_denied"),
                (URLError(socket.gaierror(-2,"secret")),"unavailable","dns_resolution_failed"),
                (ssl.SSLError("secret"),"failed","tls_error")]
        for exc, status, reason in errors:
            with self.subTest(reason=reason):
                self.assertEqual(classify_probe_exception(exc),(status,reason))
                self.assertNotIn("secret",str(classify_probe_exception(exc)))
        def f(*args):
            raise HTTPError("https://example.com/",429,"secret",{},None)
        self.assertEqual(check_one("8.8.8.8",8080,"http",1,f).status,"unavailable")

    def test_retention_dry_run_and_backup_before_apply(self):
        past=NOW-timedelta(days=110)
        self.insert(past-timedelta(days=1),"success")
        self.insert(past,"proxy_error")
        self.insert(NOW-timedelta(minutes=2),"success")
        d=DestinationHealth(self.h)
        d.record(Observation("8.8.8.8",8080,"http","example.com","success",past.isoformat()))
        d.record(Observation("8.8.8.8",8080,"http","example.com","rate_limited",
                             NOW.isoformat()))
        self.h.add_checks([
            Check("8.8.8.8",8080,"http","passed",20,"ok",past.isoformat()),
            Check("8.8.8.8",8080,"http","failed",None,"timeout",NOW.isoformat())])
        before=self.h.conn.execute("SELECT COUNT(*) FROM url_checks").fetchone()[0]
        preview=prune_history(self.db,days=90,now=NOW)
        self.assertEqual(preview["status"],"dry_run")
        self.assertEqual(preview["eligible"]["url_checks"],2)
        self.assertEqual(self.h.conn.execute("SELECT COUNT(*) FROM url_checks").fetchone()[0],before)
        self.assertFalse(_backup_dir(self.folder).exists())
        self.h.close()
        result=prune_history(self.db,days=90,now=NOW,apply=True)
        self.assertEqual(result["status"],"applied")
        self.assertEqual(result["deleted"],4) # 2 reference + 1 check + 1 destination
        self.assertTrue((_backup_dir(self.folder)/"latest.sqlite3").is_file())
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM url_checks").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM destination_observations").fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0],1)
        self.h=History(self.db)

    def test_retention_keeps_latest_by_timestamp_when_old_inserted_later(self):
        old=NOW-timedelta(days=120)
        self.insert(NOW-timedelta(days=40),"success")
        self.insert(old,"proxy_error")
        self.h.close()
        result=prune_history(self.db,days=90,now=NOW,apply=True)
        self.assertEqual(result["eligible"]["url_checks"],1)
        self.h=History(self.db)
        self.assertEqual(self.h.conn.execute(
            "SELECT outcome FROM url_checks").fetchall(),[("success",)])

    def test_retention_does_not_delete_last_stale_observation(self):
        old=NOW-timedelta(days=150)
        self.insert(old,"proxy_error")
        self.h.close()
        result=prune_history(self.db,days=90,now=NOW,apply=True)
        self.assertEqual(result["deleted"],0)
        self.h=History(self.db)
        self.assertEqual(self.h.conn.execute("SELECT COUNT(*) FROM url_checks").fetchone()[0],1)

    def test_retention_preserves_latest_per_project_and_target(self):
        from system_proxy.multi_target import TargetStore
        TargetStore(self.h)
        old = NOW - timedelta(days=130)
        middle = NOW - timedelta(days=110)
        fresh = NOW - timedelta(days=2)
        # An old observation arriving after a new one must not become
        # the protected latest record. Independent targets retain theirs.
        entries = [
            ("project-a", "target-a", "success", fresh),
            ("project-a", "target-a", "proxy_error", old),
            ("project-a", "target-a", "success", middle),
            ("project-a", "target-b", "rate_limited", middle),
            ("project-b", "target-a", "proxy_error", old),
        ]
        for project, target, outcome, instant in entries:
            self.h.conn.execute("""
                INSERT INTO target_observations
                  (project_id,target_id,ip,port,protocol,outcome,
                   observed_at,source,http_status,latency_ms,verified_https)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (project,target,"8.8.8.8",8080,"http",outcome,
                  instant.isoformat(),"client",None,None,0))
        self.h.conn.commit()
        preview = prune_history(self.db, days=90, now=NOW)
        self.assertEqual(preview["eligible"]["target_observations"], 2)
        self.assertEqual(self.h.conn.execute(
            "SELECT COUNT(*) FROM target_observations").fetchone()[0], 5)
        self.h.close()
        applied = prune_history(self.db, days=90, now=NOW, apply=True)
        self.assertEqual(applied["eligible"]["target_observations"], 2)
        with sqlite3.connect(self.db) as conn:
            actual = conn.execute("""
                SELECT project_id,target_id,outcome FROM target_observations
                ORDER BY project_id,target_id
            """).fetchall()
        self.assertEqual(actual, [
            ("project-a", "target-a", "success"),
            ("project-a", "target-b", "rate_limited"),
            ("project-b", "target-a", "proxy_error"),
        ])
        self.h = History(self.db)

    def test_retention_limits_and_missing_db(self):
        with self.assertRaises(ValueError):
            prune_history(self.db,days=1)
        with self.assertRaises(ValueError):
            prune_history(self.db,max_delete=1000000)
        self.assertEqual(prune_history(self.folder/"missing.db")["status"],"no_database")


if __name__=="__main__":
    unittest.main()
