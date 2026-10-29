"""Offline multi-destination demonstration and ranking regression tests."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from system_proxy.history import History
from system_proxy.multi_target import TargetObservation, TargetStore
from system_proxy.target_cli import synthetic_demo
from system_proxy.target_profiles import load_profiles

BASE = {
    "schema_version": 1,
    "description": "Test fixture, never requests URLs",
    "projects": [{
        "id": "demo",
        "targets": [
            {"id": "first", "url": "https://www.google.com/", "mode": "passive",
             "protocols": ["http", "socks4"],
             "policy": {"min_successes": 3, "min_score": 35, "max_age_hours": 24}},
            {"id": "second", "url": "https://chatgpt.com/", "mode": "passive",
             "protocols": ["http"],
             "policy": {"min_successes": 3, "min_score": 35, "max_age_hours": 24}},
        ],
    }],
}


def stamp(hours=0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


class Profiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "targets.json"

    def tearDown(self):
        self.temp.cleanup()

    def config(self, value=BASE):
        self.path.write_text(json.dumps(value), encoding="utf-8")
        return load_profiles(self.path)

    def test_public_examples_are_passive_and_networkless(self):
        public = (Path(__file__).parents[1] /
                  "config/examples/public-targets.json")
        profiles = load_profiles(public)
        urls = {x.url for x in profiles.values()}
        self.assertIn("https://chatgpt.com/", urls)
        self.assertIn("https://gemini.google.com/app", urls)
        self.assertIn("https://www.google.com/", urls)
        self.assertTrue(all(x.mode == "passive" for x in profiles.values()))
        self.assertEqual(len(profiles), 4)

    def test_rejects_unsafe_and_unsupported_profiles(self):
        from copy import deepcopy
        cases = [
            ("http://google.com/", "passive"),
            ("https://127.0.0.1/", "passive"),
            ("https://localhost/", "passive"),
            ("https://user:secret@www.google.com/", "passive"),
            ("https://www.google.com/?key=secret", "passive"),
            ("https://www.google.com/", "active"),
            ("https://www.google.com/#frag", "passive"),
            ("https://www.google.com:444/", "passive"),
        ]
        for url, mode in cases:
            config = deepcopy(BASE)
            config["projects"][0]["targets"][0]["url"] = url
            config["projects"][0]["targets"][0]["mode"] = mode
            with self.subTest(url=url, mode=mode), self.assertRaises(ValueError):
                self.config(config)

    def test_rejects_extra_fields_and_duplicate_ids(self):
        from copy import deepcopy
        cfg = deepcopy(BASE)
        cfg["projects"][0]["targets"][0]["api_key"] = "should-not-be-in-repo"
        with self.assertRaises(ValueError):
            self.config(cfg)
        cfg = deepcopy(BASE)
        cfg["projects"][0]["targets"][1]["id"] = "first"
        with self.assertRaises(ValueError):
            self.config(cfg)

    def test_demo_is_offline_reserved_addresses_and_labeled(self):
        result = synthetic_demo(self.config())
        self.assertTrue(result["synthetic"])
        self.assertTrue(result["test_net_only"])
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(len(result["targets"]), 2)
        for target in result["targets"].values():
            ranked = target["ranking"]["ranking"]
            self.assertEqual(target["stats"]["total_observations"], 18)
            self.assertEqual(ranked[0]["ip"], "192.0.2.10")
            self.assertTrue(ranked[0]["eligible"])
            denied = next(x for x in ranked if x["ip"] == "203.0.113.30")
            self.assertFalse(denied["eligible"])
            self.assertEqual(denied["restrictions"], 1)
            self.assertEqual(denied["proxy_failures"], 0)


class Ranking(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        p = Path(self.temp.name) / "profiles.json"
        p.write_text(json.dumps(BASE))
        self.profiles = load_profiles(p)
        self.db = Path(self.temp.name) / "history.sqlite3"
        self.history = History(self.db)
        self.store = TargetStore(self.history)
        self.first = self.profiles["demo", "first"]
        self.second = self.profiles["demo", "second"]

    def tearDown(self):
        self.history.close()
        self.temp.cleanup()

    def add(self, profile, *, ip="8.8.8.8", port=8080, status=200,
            outcome="success", hours=0, verified=True, protocol="http",
            latency=100):
        self.store.record(profile, TargetObservation(
            ip=ip, port=port, protocol=protocol, outcome=outcome,
            http_status=status, observed_at=stamp(hours),
            latency_ms=latency, verified_https=verified))

    def test_isolated_rankings_persist_and_require_repetition(self):
        self.add(self.first)
        self.assertFalse(self.store.ranking(self.first)["ranking"][0]["eligible"])
        self.add(self.first)
        self.add(self.first)
        a = self.store.ranking(self.first)
        self.assertTrue(a["ranking"][0]["eligible"])
        self.assertEqual(a["ranking"][0]["successes"], 3)
        self.assertEqual(self.store.ranking(self.second)["observed_proxies"], 0)
        self.add(self.second, status=403, outcome="target_denied", verified=False)
        self.assertFalse(self.store.ranking(self.second)["ranking"][0]["eligible"])
        self.assertEqual(self.store.stats(self.second)["by_outcome"]["target_denied"], 1)
        self.history.close()
        self.history = History(self.db)
        self.store = TargetStore(self.history)
        self.assertEqual(self.store.ranking(self.first)["eligible_proxies"], 1)

    def test_denials_and_rate_limits_do_not_lower_global_reliability(self):
        for _ in range(4):
            self.add(self.first)
        self.add(self.first, status=429, outcome="rate_limited", verified=False)
        self.add(self.first, status=403, outcome="target_denied", verified=False)
        result = self.store.ranking(self.first)["ranking"][0]
        self.assertEqual(result["proxy_failures"], 0)
        self.assertEqual(result["restrictions"], 2)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["latest_outcome"], "target_denied")
        self.assertGreater(result["score"], 0)
        self.assertEqual(self.history.summary()["checked_endpoints"], 0)

    def test_failure_reduces_score_and_latest_failure_disables(self):
        for _ in range(3):
            self.add(self.first)
        previous = self.store.ranking(self.first)["ranking"][0]["score"]
        self.add(self.first, outcome="proxy_error", status=None, verified=False)
        after = self.store.ranking(self.first)["ranking"][0]
        self.assertLess(after["score"], previous)
        self.assertFalse(after["eligible"])

    def test_late_arriving_old_failure_does_not_override_recent_success(self):
        for _ in range(3):
            self.add(self.first, hours=0)
        self.add(self.first, outcome="proxy_error", status=None,
                 verified=False, hours=2)
        ranked = self.store.ranking(self.first)["ranking"][0]
        self.assertEqual(ranked["latest_outcome"], "success")
        self.assertTrue(ranked["eligible"])
        self.assertEqual(ranked["proxy_failures"], 1)

    def test_stale_results_are_not_eligible(self):
        for _ in range(4):
            self.add(self.first, hours=48)
        ranked = self.store.ranking(self.first)["ranking"][0]
        self.assertFalse(ranked["fresh"])
        self.assertFalse(ranked["eligible"])

    def test_invalid_successs_unsafe_ip_and_bad_status_rejected(self):
        invalid = [
            dict(status=200, verified=False, outcome="success"),
            dict(status=None, verified=True, outcome="success"),
            dict(ip="127.0.0.1", status=200),
            dict(status=403, outcome="success"),
            dict(status=429, outcome="proxy_error", verified=False),
            dict(protocol="socks5"),
            dict(latency=-1),
        ]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.add(self.first, **item)
        self.assertEqual(self.store.stats(self.first)["total_observations"], 0)

    def test_multiple_protocols_are_independent(self):
        for _ in range(3):
            self.add(self.first, protocol="socks4")
        self.assertEqual(self.store.ranking(self.first, protocol="socks4")["eligible_proxies"], 1)
        self.assertEqual(self.store.ranking(self.first, protocol="http")["observed_proxies"], 0)
        with self.assertRaises(ValueError):
            self.store.ranking(self.second, protocol="socks5")


if __name__ == "__main__":
    unittest.main()
