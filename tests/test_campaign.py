"""Campaign orchestration tests use only offline fixtures."""
import json
import http.client
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from system_proxy.campaign import collect_inventory, run
from system_proxy.history import History
from system_proxy.verification import Check


class CampaignTests(unittest.TestCase):
    def test_collection_deduplicates_and_imports(self):
        from system_proxy.collect import SOURCES
        def fake_fetch(url, timeout, size):
            if "hproxy" in url:
                return json.dumps([{"ip":"1.1.1.1","port":8080,
                                    "protocols":["http"]}]).encode()
            return json.dumps({"data":[{"ip":"1.1.1.1","port":8080,
                                         "protocols":["http"]}]}).encode()
        with History(":memory:") as h:
            result=collect_inventory(h, max_candidates=20,fetch=fake_fetch)
            self.assertEqual(result["unique_endpoints"],1)
            self.assertEqual(len(h.candidates("http")),1)

    def test_ranking_uses_only_real_success_and_resumes(self):
        def fake_fetch(url, timeout, size):
            if "hproxy" in url:
                return b"[]"
            return json.dumps({"data":[{"ip":"1.1.1.1","port":8080,
                                         "protocols":["http"]}]}).encode()
        called=[]
        def verify(entries,**kwargs):
            called.extend(entries)
            return [Check(ip,port,proto,"passed",85,"verified_https_content",
                          datetime.now(timezone.utc).isoformat())
                    for ip,port,proto in entries]
        with tempfile.TemporaryDirectory() as td:
            db=Path(td)/"campaign.db"
            result=run(db=db,max_checks=10,fetch=fake_fetch,
                       verifier=verify,runtime_seconds=20)
            self.assertEqual(result["checks"]["passed"],1)
            self.assertEqual(result["ranking"]["http"][0]["successes"],1)
            self.assertGreater(result["ranking"]["http"][0]["score"],0)
            again=run(db=db,max_checks=10,fetch=fake_fetch,
                      verifier=verify,runtime_seconds=20)
            self.assertEqual(again["attempts_this_run"],0)
            self.assertEqual(len(called),1)

    def test_bad_connect_http_response_does_not_abort_batch(self):
        from system_proxy.verification import check_one
        from unittest.mock import patch
        with patch("system_proxy.verification.probe_http",
                   side_effect=http.client.BadStatusLine("<html>")):
            result=check_one("8.8.8.8",8080,"http",1.0,
                             probe=lambda *a: (_ for _ in ()).throw(
                                 http.client.BadStatusLine("<html>")))
        self.assertEqual(result.status,"failed")
        self.assertEqual(result.reason,"BadStatusLine")

    def test_large_public_feed_filters_private_credentials_and_types(self):
        def fake_fetch(url, timeout, limit):
            if "proxifly" in url:
                return (b"socks5://8.8.8.8:1080\n"
                        b"socks4://9.9.9.9:1080\n"
                        b"http://1.1.1.1:8080\n"
                        b"ftp://4.4.4.4:21\n"
                        b"http://10.0.0.1:80\n"
                        b"http://name:secret@8.8.4.4:80\n")
            if "hproxy" in url:
                return b"[]"
            return b'{"data":[]}'
        with History(":memory:") as h:
            result=collect_inventory(h,max_candidates=10000,fetch=fake_fetch)
            self.assertEqual(result["unique_endpoints"],3)
            self.assertEqual(len(h.candidates("socks5")),1)
            self.assertEqual(len(h.candidates("socks4")),1)
            self.assertEqual(len(h.candidates("http")),1)

    def test_policy_denial_is_not_proxy_failure(self):
        from system_proxy.verification import check_one
        def denied(*args):
            raise PermissionError("policy denied")
        result=check_one("8.8.8.8",8080,"http",1.0,probe=denied)
        self.assertEqual(result.status,"unavailable")

    def test_unexpected_endpoint_exception_is_isolated(self):
        from system_proxy.verification import check_one
        def bad(*args):
            raise RuntimeError("secrets must not be logged")
        result=check_one("8.8.8.8",8080,"http",1.0,probe=bad)
        self.assertEqual(result.status,"unavailable")
        self.assertEqual(result.reason,"unexpected_checker_exception")

    def test_invalid_limits(self):
        with self.assertRaises(ValueError):
            run(db=Path("unused.db"),max_checks=10001)


if __name__=="__main__":
    unittest.main()
