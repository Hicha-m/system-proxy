"""No-network regression coverage for daily refresh and reusable exports."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from system_proxy.daily import plan_checks, run_daily
from system_proxy.project_export import export_projects
from system_proxy.verification import Check


class DailyTests(unittest.TestCase):
    @staticmethod
    def fetch(url, timeout, size):
        if "proxylist.geonode.com" in url:
            return json.dumps({"data": [
                {"ip": "1.1.1.1", "port": 8080, "protocols": ["http"]}
            ]}).encode()
        if "hproxy.com" in url:
            return b"[]"
        if url.endswith("/http.txt"):
            return b"8.8.8.8:8080\n"
        return b""

    def test_daily_new_proxies_resume_and_two_successes_required_for_export(self):
        called = []
        def verify(entries, **kwargs):
            called.extend(entries)
            return [
                Check(ip, port, protocol, "passed", 90,
                      "verified_https_content", datetime.now(timezone.utc).isoformat())
                for ip, port, protocol in entries
            ]
        with tempfile.TemporaryDirectory() as root:
            db = Path(root) / "history.sqlite3"
            now = datetime.now(timezone.utc)
            first = run_daily(db=db, max_checks=10, runtime_seconds=20,
                              fetch=self.fetch, verifier=verify, now=now)
            self.assertEqual(first["status"], "ok")
            self.assertGreaterEqual(first["new_candidates"], 2)
            self.assertEqual(first["attempted"], 2)
            self.assertTrue(all(count == 0 for count in first["exports"].values()))
            duplicate = run_daily(db=db, max_checks=10, runtime_seconds=20,
                                  fetch=self.fetch, verifier=verify, now=now)
            self.assertEqual(duplicate["status"], "already_completed_today")
            self.assertEqual(len(called), 2)
            next_day = run_daily(db=db, max_checks=10, runtime_seconds=20,
                                 fetch=self.fetch, verifier=verify,
                                 now=now + timedelta(days=1))
            self.assertEqual(next_day["new_candidates"], 0)
            self.assertEqual(next_day["attempted"], 2)
            self.assertGreaterEqual(next_day["exports"]["reference-http"], 2)
            self.assertTrue((Path(root) / "exports/reference-http/proxies.json").is_file())
            exports = json.loads(
                (Path(root) / "exports/reference-http/proxies.json").read_text()
            )
            self.assertTrue(all(row["successes"] >= 2 for row in exports["proxies"]))

    def test_failed_proxy_cooldown_and_fresh_success_planning(self):
        with sqlite3.connect(":memory:") as db:
            db.executescript("""
                CREATE TABLE endpoints (ip TEXT, port INT, protocol TEXT);
                CREATE TABLE url_checks (ip TEXT,port INT,protocol TEXT,
                    target_url TEXT,outcome TEXT, latency_ms REAL,checked_at TEXT);
                INSERT INTO endpoints VALUES ('1.1.1.1',80,'http');
                INSERT INTO endpoints VALUES ('8.8.8.8',80,'http');
            """)
            now = datetime.now(timezone.utc)
            old = (now - timedelta(days=4)).isoformat()
            db.execute("INSERT INTO url_checks VALUES (?,?,?,?,?,?,?)",
                       ("1.1.1.1",80,"http","https://example.com/","proxy_error",None,old))
            db.execute("INSERT INTO url_checks VALUES (?,?,?,?,?,?,?)",
                       ("8.8.8.8",80,"http","https://example.com/","success",20,
                        now.isoformat()))
            selected, summary = plan_checks(
                db, previous_keys={("1.1.1.1",80,"http"),("8.8.8.8",80,"http")},
                now=now, max_checks=10
            )
            self.assertEqual(selected, [("1.1.1.1",80,"http")])
            self.assertEqual(summary["due_failed"], 1)

    def test_small_daily_budget_covers_all_available_protocols(self):
        # Historically a limit of three always selected the first HTTP rows.
        with sqlite3.connect(":memory:") as db:
            db.executescript("""
                CREATE TABLE endpoints (ip TEXT,port INT,protocol TEXT);
                CREATE TABLE url_checks (ip TEXT,port INT,protocol TEXT,
                    target_url TEXT,outcome TEXT,latency_ms REAL,checked_at TEXT);
            """)
            for i in range(15):
                db.execute("INSERT INTO endpoints VALUES (?,?,?)",
                           (f"8.8.8.{i+1}",8080,"http"))
            for proto in ("socks4","socks5"):
                db.execute("INSERT INTO endpoints VALUES (?,?,?)",
                           ("1.1.1.1",1080,proto))
            selected, summary = plan_checks(
                db, previous_keys=set(), now=datetime.now(timezone.utc),
                max_checks=3)
            self.assertEqual({entry[2] for entry in selected},
                             {"http","socks4","socks5"})
            self.assertEqual(summary["new_candidates"],17)
            self.assertEqual(summary["selected"],3)

    def test_export_filters_stale_or_failed_and_rejects_wrong_target(self):
        with tempfile.TemporaryDirectory() as root:
            db = sqlite3.connect(":memory:")
            db.execute("""CREATE TABLE url_checks (
                ip TEXT,port INT,protocol TEXT,target_url TEXT,
                outcome TEXT,latency_ms REAL,checked_at TEXT)""")
            now = datetime.now(timezone.utc)
            for ip, statuses in [
                ("1.1.1.1", ["success","success"]),
                ("8.8.8.8", ["success","proxy_error"]),
            ]:
                for outcome in statuses:
                    db.execute("INSERT INTO url_checks VALUES (?,?,?,?,?,?,?)",
                               (ip,8080,"http","https://example.com/",outcome,100,now.isoformat()))
            result = export_projects(db, Path(root), now=now)
            self.assertEqual(result["reference-http"], 1)
            self.assertIn("1.1.1.1", (Path(root) / "reference-http/proxies.txt").read_text())
            self.assertNotIn("8.8.8.8", (Path(root) / "reference-http/proxies.txt").read_text())
            profiles = Path(root) / "bad.json"
            profiles.write_text(json.dumps({"projects": [
                {"name": "other", "protocol": "http",
                 "target_url": "http://127.0.0.1/"}
            ]}))
            with self.assertRaises(ValueError):
                export_projects(db, Path(root), profiles=profiles, now=now)
            db.close()


if __name__ == "__main__":
    unittest.main()
