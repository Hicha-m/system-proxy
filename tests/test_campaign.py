"""Campaign orchestration tests use only offline fixtures."""
import json
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
            again=run(db=db,max_checks=10,fetch=fake_fetch,
                      verifier=verify,runtime_seconds=20)
            self.assertEqual(again["attempts_this_run"],0)
            self.assertEqual(len(called),1)

    def test_invalid_limits(self):
        with self.assertRaises(ValueError):
            run(db=Path("unused.db"),max_checks=10001)


if __name__=="__main__":
    unittest.main()
