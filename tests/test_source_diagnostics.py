"""No-network tests of canonical provider HTTPS stages and sanitized reports."""
import json
import socket
import ssl
import unittest
from types import SimpleNamespace
from system_proxy.source_diagnostics import diagnose_source
from system_proxy.network_cli import run_diagnostic


def dns(*args):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]


class Fake:
    def __init__(self, response=b"HTTP/1.1 200 OK\r\n\r\n"):
        self.response = bytearray(response)
        self.hostname = None
        self.request = b""
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def settimeout(self, *_): pass
    def sendall(self, data): self.request += data
    def recv(self, size):
        b = bytes(self.response[:size]); del self.response[:size]; return b


class ProviderTests(unittest.TestCase):
    def run_case(self, source="geonode", *, response=b"HTTP/1.1 200 OK\r\n\r\n",
                 tcp_error=None, tls_error=None):
        raw = Fake()
        secure = Fake(response)
        def dial(*args, **kwargs):
            if tcp_error: raise tcp_error
            return raw
        def wrap(sock, *, server_hostname):
            if tls_error: raise tls_error
            secure.hostname = server_hostname
            return secure
        ctx=SimpleNamespace(wrap_socket=wrap)
        result=diagnose_source(source,resolver=dns,dial=dial,tls_context=lambda:ctx)
        return result,secure

    def test_hosts_and_https_get_fixed(self):
        for source,host in (("geonode","proxylist.geonode.com"),
                            ("hproxy","hproxy.com")):
            result,secure=self.run_case(source)
            self.assertEqual(result["status"],"ok")
            self.assertEqual(secure.hostname,host)
            self.assertIn(("Host: "+host+"\r\n").encode(),secure.request)
            self.assertEqual([s["stage"] for s in result["steps"]],
                             ["dns","tcp","tls","http"])

    def test_403_and_429(self):
        for status,reason in ((b"403", "access_denied"),(b"429","rate_limited")):
            result,_=self.run_case(response=b"HTTP/1.1 "+status+b" Error\r\n\r\n")
            self.assertEqual((result["failed_stage"],result["reason"]),
                             ("http",reason))

    def test_tls_and_tcp_failures_sanitized(self):
        a,_=self.run_case(tls_error=ssl.SSLCertVerificationError(1,"SECRET"))
        b,_=self.run_case(tcp_error=TimeoutError("SECRET"))
        self.assertEqual(a["reason"],"certificate_verification_failed")
        self.assertEqual(b["reason"],"timeout")
        self.assertNotIn("SECRET",json.dumps([a,b]))

    def test_unknown_source_refused(self):
        with self.assertRaises(ValueError):
            diagnose_source("attacker-controlled-domain")

    def test_failure_collector_calls_stage_diagnostic(self):
        called=[]
        def collect(ids):
            return SimpleNamespace(sources={ids[0]:{"errors":["URLError: SECRET"]}},
                                   to_dict=lambda:{"proxies":[]})
        def check(name,*,timeout):
            called.append(name)
            return {"source":name,"status":"inconclusive","failed_stage":"tls",
                    "reason":"timeout","steps":[]}
        report=run_diagnostic(
            direct_check=lambda timeout:{"status":"ok","steps":[]},
            collect=collect,source_check=check)
        self.assertEqual(called,["geonode","hproxy"])
        self.assertEqual(report["collection"]["sources"][0]["https_diagnostic"]["failed_stage"],"tls")
        self.assertNotIn("SECRET",json.dumps(report))

if __name__=="__main__":
    unittest.main()
