"""Offline HTTPS transport comparison tests; no public network."""
import json
import unittest
from types import SimpleNamespace
from urllib.error import URLError
from unittest.mock import patch

from system_proxy.urllib_diagnostics import compare_urllib, _reason


class FakeResponse:
    status = 200
    def __enter__(self): return self
    def __exit__(self,*a): pass
    def read(self,n): return b"x"


class FakeOpener:
    def __init__(self,error=None): self.error=error
    def open(self,request,timeout):
        if self.error: raise self.error
        return FakeResponse()


class Tests(unittest.TestCase):
    def test_both_paths_with_fixed_source(self):
        modes=[]
        result=compare_urllib("geonode",opener_factory=lambda m:(
            modes.append(m) or FakeOpener()))
        self.assertEqual(modes,["urllib_default","urllib_ipv4"])
        self.assertEqual(result["paths"]["urllib_ipv4"]["status"],"ok")
        self.assertEqual(result["paths"]["urllib_default"]["reason"],"response_read")

    def test_urllib_error_is_classified_and_redacted(self):
        def op(mode):
            return FakeOpener(URLError(TimeoutError("secret token password")))
        result=compare_urllib("hproxy",opener_factory=op)
        self.assertEqual(result["paths"]["urllib_default"]["reason"],"timeout")
        self.assertNotIn("secret",json.dumps(result))

    def test_collector_retries_only_canonical_source(self):
        from system_proxy.collect import http_fetch, SOURCES
        from unittest.mock import patch
        called=[]
        class Fail:
            def open(self,*args,**kwargs):
                called.append("original")
                raise URLError(OSError("broker failed"))
        class Good:
            def open(self,*args,**kwargs):
                called.append("ipv4")
                return FakeResponse()
        with patch("system_proxy.collect.build_opener",
                   side_effect=[Fail(),Good()]):
            data=http_fetch(SOURCES["geonode"].page_url(1,50))
        self.assertEqual(data,b"x")
        self.assertEqual(called,["original","ipv4"])

    def test_collector_never_retries_arbitrary_https(self):
        from system_proxy.collect import http_fetch
        from unittest.mock import patch
        with patch("system_proxy.collect.build_opener",return_value=FakeOpener(
                   URLError(OSError("blocked")))) as opening:
            with self.assertRaises(URLError):
                http_fetch("https://other.example.com/test")
            self.assertEqual(opening.call_count,1)

    def test_refuses_arbitrary_destination(self):
        for source in ("example.com", "localhost", "../credentials"):
            with self.assertRaises(ValueError):
                compare_urllib(source)


if __name__=="__main__":
    unittest.main()
