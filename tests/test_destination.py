"""Offline, deterministic destination health and ranking tests."""
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path

from system_proxy.destination import (
    DestinationHealth, Observation, domain_key, classify_status, wilson_lower,
)
from system_proxy.history import History
from system_proxy.verification import Check


def stamp(seconds=0):
    return (datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()


class InputTests(unittest.TestCase):
    def test_domains_are_canonical(self):
        self.assertEqual(domain_key("Example.COM."),"example.com")
        self.assertEqual(domain_key("BÜCHER.DE"),"xn--bcher-kva.de")
        for value in ("https://example.com", "example.com:443", "localhost",
                      "192.168.1.1", "foo.local", "foo.internal",
                      "user@example.com", "foo/path", "x..com",""):
            with self.subTest(value=value),self.assertRaises(ValueError):
                domain_key(value)

    def test_status_is_conservative(self):
        self.assertEqual(classify_status(200),"success")
        self.assertEqual(classify_status(302),"success")
        self.assertEqual(classify_status(403),"target_denied")
        self.assertEqual(classify_status(429),"rate_limited")
        self.assertEqual(classify_status(503),"unknown")
        self.assertIsNone(wilson_lower(0,0))
        self.assertLess(wilson_lower(1,0),50)


class DestinationTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.path=Path(self.t.name)/"history.sqlite3"
        self.h=History(self.path)
        self.h.import_candidates([
            {"ip":"8.8.8.8","port":8080,"protocols":["http"],"sources":["hproxy"]},
            {"ip":"1.1.1.1","port":3128,"protocols":["http"],"sources":["geonode"]},
            {"ip":"9.9.9.9","port":8080,"protocols":["http"],"sources":["proxycompass"]},
        ])
        self.d=DestinationHealth(self.h)

    def tearDown(self):
        self.h.close()
        self.t.cleanup()

    def observation(self,ip,domain,outcome,port=8080,seconds=0,http_status=None):
        self.d.record(Observation(ip,port,"http",domain,outcome,stamp(seconds),
                                  http_status=http_status,latency_ms=42))

    def test_different_domain_outcomes_do_not_pollute_global_checks(self):
        self.h.add_checks([Check("8.8.8.8",8080,"http","passed",30,"ok",stamp())])
        self.observation("8.8.8.8","example.com","success",http_status=200)
        self.observation("8.8.8.8","jobs.example","target_denied",http_status=403)
        self.observation("1.1.1.1","jobs.example","success",port=3128,http_status=200)
        example=self.d.report("example.com")["ranked"]
        jobs=self.d.report("jobs.example")["ranked"]
        self.assertTrue(next(x for x in example if x["ip"]=="8.8.8.8")["eligible"])
        self.assertFalse(next(x for x in jobs if x["ip"]=="8.8.8.8")["eligible"])
        self.assertEqual(jobs[0]["ip"],"1.1.1.1")
        self.assertTrue(next(x for x in jobs if x["ip"]=="1.1.1.1")["eligible"])
        global_ep=next(x for x in self.h.summary()["endpoints"] if x["ip"]=="8.8.8.8")
        self.assertEqual((global_ep["passed"],global_ep["failed"]),(1,0))
        self.assertEqual(global_ep["latest_status"],"passed")

    def test_cooldown_on_latest_denied_or_rate_limit(self):
        self.observation("8.8.8.8","shop.example","success")
        self.observation("8.8.8.8","shop.example","rate_limited",http_status=429)
        item=next(x for x in self.d.report("shop.example")["ranked"] if x["ip"]=="8.8.8.8")
        self.assertFalse(item["eligible"])
        self.assertEqual(item["denied_or_failed"],1)
        self.assertEqual(item["last_http_status"],429)

    def test_stale_and_global_failure(self):
        self.observation("8.8.8.8","example.com","success",seconds=-7200)
        item=next(x for x in self.d.report("example.com")["ranked"] if x["ip"]=="8.8.8.8")
        self.assertFalse(item["destination_fresh"])
        self.h.add_checks([Check("8.8.8.8",8080,"http","failed",None,"timeout",stamp())])
        self.observation("8.8.8.8","example.com","success")
        item=next(x for x in self.d.report("example.com")["ranked"] if x["ip"]=="8.8.8.8")
        self.assertFalse(item["eligible"])
        self.assertFalse(item["global_recently_passing"])

    def test_invalid_observations_rejected(self):
        bad=[
          Observation("127.0.0.1",80,"http","example.com","success",stamp()),
          Observation("8.8.8.8",8080,"http","localhost","success",stamp()),
          Observation("8.8.8.8",8080,"http","example.com","made_up",stamp()),
          Observation("8.8.8.8",8080,"http","example.com","success",stamp(3600)),
          Observation("8.8.8.8",8080,"http","example.com","success","2026-01-01T12:00:00"),
          Observation("8.8.8.8",8080,"http","example.com","success",stamp(),latency_ms=-1),
        ]
        for value in bad:
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.d.record(value)
        self.assertEqual(self.d.report("example.com")["total_candidates"],3)

    def test_history_survives_reopening(self):
        self.observation("8.8.8.8","example.com","success")
        self.h.close()
        self.h=History(self.path)
        self.d=DestinationHealth(self.h)
        self.assertEqual(next(x for x in self.d.report("example.com")["ranked"]
                              if x["ip"]=="8.8.8.8")["observations"],1)


if __name__=="__main__":
    unittest.main()
