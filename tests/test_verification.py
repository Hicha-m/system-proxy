"""Deterministic offline tests for verifier, history, scoring and reporting."""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from system_proxy.verification import Check, check_one, verify_candidates
from system_proxy.history import History


def timestamp(offset=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()


class CheckTests(unittest.TestCase):
    def test_success_failure_timeout_and_protocol(self):
        ok = check_one("8.8.8.8", 80, "http", 1, lambda *_: (True, "verified"))
        self.assertEqual(ok.status, "passed")
        self.assertGreaterEqual(ok.latency_ms, 0)
        fail = check_one("8.8.8.8", 80, "http", 1, lambda *_: (False, "bad_content"))
        self.assertEqual(fail.status, "failed")
        self.assertEqual(fail.reason, "bad_content")
        def timeout(*_):
            raise TimeoutError()
        self.assertEqual(check_one("8.8.8.8", 80, "http", 1, timeout).status, "failed")
        unsupported = check_one("8.8.8.8", 80, "socks5", 1, lambda *_: (True, "ok"))
        self.assertEqual((unsupported.status, unsupported.reason),
                         ("unavailable", "protocol_not_implemented"))

    def test_dedup_and_bounded_concurrency(self):
        calls = []
        def fake(ip, port, timeout):
            calls.append((ip, port))
            return True, "ok"
        checks = verify_candidates([
            ("8.8.8.8", 80, "http"), ("8.8.8.8", 80, "http"),
            ("1.1.1.1", 3128, "http"), ("9.9.9.9", 80, "http"),
        ], baseline=False, probe=fake, workers=2, max_candidates=2)
        self.assertEqual(len(checks), 2)
        self.assertEqual(set(calls), {("8.8.8.8", 80), ("1.1.1.1", 3128)})
        with self.assertRaises(ValueError):
            verify_candidates([], workers=100, baseline=False)
        with self.assertRaises(ValueError):
            verify_candidates([("192.168.0.1", 80, "http")], baseline=False)
        with self.assertRaises(ValueError):
            verify_candidates([], timeout=100, baseline=False)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "checks.db"
        self.db = History(self.path)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def load(self):
        self.db.import_candidates([
            {"ip":"8.8.8.8", "port":80, "protocols":["http", "socks5"],
             "sources":["hproxy","geonode"]},
            {"ip":"1.1.1.1", "port":3128, "protocols":["http"],
             "sources":["proxycompass"]},
        ])

    def test_history_persists_and_preserves_multiple_sources(self):
        self.load()
        self.load()
        self.assertEqual(len(self.db.candidates("http")), 2)
        self.db.add_checks([
            Check("8.8.8.8",80,"http","passed",33.0,"ok",timestamp()),
            Check("8.8.8.8",80,"http","failed",None,"timeout",timestamp()),
            Check("8.8.8.8",80,"http","passed",19.0,"ok",timestamp()),
            Check("1.1.1.1",3128,"http","passed",12.0,"ok",timestamp()),
        ])
        report = self.db.summary()
        ep = next(e for e in report["endpoints"] if e["ip"]=="8.8.8.8" and e["protocol"]=="http")
        self.assertEqual((ep["passed"], ep["failed"], ep["unavailable"]), (2,1,0))
        self.assertEqual(ep["sources"], ["geonode", "hproxy"])
        self.assertTrue(ep["usable_by_recent_check"])
        self.assertLess(ep["reliability_score"], 70)
        self.assertEqual(report["source_stats"]["proxycompass"]["exclusive_recently_passing"], 1)
        self.assertEqual(report["source_stats"]["geonode"]["exclusive_recently_passing"], 0)
        self.db.close()
        self.db = History(self.path)
        self.assertEqual(self.db.summary()["checked_endpoints"], 2)

    def test_last_failure_and_expired_checks_are_unusable(self):
        self.load()
        self.db.add_checks([
            Check("8.8.8.8",80,"http","passed",10.0,"ok",timestamp(-7200)),
            Check("1.1.1.1",3128,"http","passed",10.0,"ok",timestamp()),
            Check("1.1.1.1",3128,"http","failed",None,"timeout",timestamp()),
        ])
        report = self.db.summary(stale_after_seconds=60)
        self.assertFalse(any(e["usable_by_recent_check"] for e in report["endpoints"]))
        expired = next(e for e in report["endpoints"] if e["ip"]=="8.8.8.8" and e["protocol"]=="http")
        self.assertFalse(expired["fresh"])
        fail = next(e for e in report["endpoints"] if e["ip"]=="1.1.1.1")
        self.assertEqual(fail["latest_status"], "failed")

    def test_unavailable_does_not_reduce_reliability(self):
        self.load()
        self.db.add_checks([
            Check("8.8.8.8",80,"http","passed",10.0,"ok",timestamp(-40)),
            Check("8.8.8.8",80,"http","unavailable",None,"check_target_unavailable",timestamp()),
        ])
        ep = next(e for e in self.db.summary()["endpoints"]
                  if e["ip"]=="8.8.8.8" and e["protocol"]=="http")
        self.assertEqual((ep["passed"], ep["failed"], ep["unavailable"]), (1,0,1))
        self.assertFalse(ep["usable_by_recent_check"])

    def test_import_rejects_non_public_ips(self):
        with self.assertRaises(ValueError):
            self.db.import_candidates([{
                "ip":"127.0.0.1","port":80,"protocols":["http"],"sources":["geonode"]}])
        self.assertEqual(self.db.summary()["total_endpoints"], 0)


if __name__ == "__main__":
    unittest.main()
