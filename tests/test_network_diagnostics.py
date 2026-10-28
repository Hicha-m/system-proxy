"""Offline regression tests for stage-specific diagnostics and independent proxy checks."""
from __future__ import annotations

import errno
import json
import socket
import ssl
import unittest
from types import SimpleNamespace

from system_proxy.network_diagnostics import diagnose_direct_https, _error
from system_proxy.network_cli import run_diagnostic, _safe_source_reason
from system_proxy.verification import Check, direct_baseline_details


class FakeConnection:
    def __init__(self, reply=b""):
        self.reply = bytearray(reply)
        self.sent = []
        self.server_hostname = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def settimeout(self, timeout):
        pass

    def sendall(self, payload):
        self.sent.append(payload)

    def recv(self, amount):
        data = bytes(self.reply[:amount])
        del self.reply[:amount]
        return data


def resolver(*args):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))]


def diagnostic(reply=b"HTTP/1.1 200 OK\r\nContent-Length: 14\r\n\r\nExample Domain",
               dial_error=None, tls_error=None, resolve=None):
    conn = FakeConnection()
    secure = FakeConnection(reply)

    def dial(*args, **kwargs):
        if dial_error:
            raise dial_error
        return conn

    def wrap_socket(sock, server_hostname):
        if tls_error:
            raise tls_error
        secure.server_hostname = server_hostname
        return secure

    ctx = SimpleNamespace(wrap_socket=wrap_socket)
    result = diagnose_direct_https(
        resolver=resolve or resolver, dial=dial, tls_context=lambda: ctx)
    return result, secure


class StagesTests(unittest.TestCase):
    def test_complete_chain_and_hostname_verification(self):
        result, secure = diagnostic()
        self.assertEqual(result["status"], "ok")
        self.assertEqual([v["stage"] for v in result["steps"]],
                         ["dns", "tcp", "tls", "http"])
        self.assertEqual(secure.server_hostname, "example.com")
        self.assertTrue(secure.sent[0].startswith(b"GET / HTTP/1.1"))

    def test_dns_failure(self):
        def broken(*args):
            raise socket.gaierror(-3, "sensitive details should not leak")
        result, _ = diagnostic(resolve=broken)
        self.assertEqual((result["status"],result["failed_stage"]),
                         ("inconclusive", "dns"))
        self.assertEqual(result["reason"], "dns_resolution_error")
        self.assertNotIn("sensitive", json.dumps(result))

    def test_private_dns_result_not_connected(self):
        def private(*args):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1",443))]
        result, _ = diagnostic(resolve=private)
        self.assertEqual(result["reason"],"no_public_ipv4")

    def test_tcp_refusal_and_timeout(self):
        for exc, status, reason in (
            (ConnectionRefusedError("private"),"failed","connection_refused"),
            (TimeoutError("private"),"inconclusive","timeout"),
            (PermissionError(errno.EACCES,"private"),"blocked","permission_denied")
        ):
            with self.subTest(reason=reason):
                result, _ = diagnostic(dial_error=exc)
                self.assertEqual((result["failed_stage"],result["status"],result["reason"]),
                                 ("tcp",status,reason))
                self.assertNotIn("private",json.dumps(result))

    def test_tls_cert_failure(self):
        result, _ = diagnostic(tls_error=ssl.SSLCertVerificationError(
            1, "private key should not show"))
        self.assertEqual((result["failed_stage"],result["status"],result["reason"]),
                         ("tls","failed","certificate_verification_failed"))
        self.assertNotIn("private",json.dumps(result))

    def test_http_refusals_and_content(self):
        for response, status, reason in (
            (b"HTTP/1.1 403 Forbidden\r\n\r\n", "failed", "access_denied"),
            (b"HTTP/1.1 429 Too Many Requests\r\n\r\n", "failed","rate_limited"),
            (b"HTTP/1.1 200 OK\r\n\r\nWrong page","failed","unexpected_content"),
            (b"garbled","inconclusive","malformed_http_response"),
        ):
            with self.subTest(reason=reason):
                result,_=diagnostic(reply=response)
                self.assertEqual((result["failed_stage"],result["status"],result["reason"]),
                                 ("http",status,reason))

    def test_timeout_validation(self):
        with self.assertRaises(ValueError):
            diagnose_direct_https(timeout=30)


class IndependentTests(unittest.TestCase):
    def report(self):
        return SimpleNamespace(
            to_dict=lambda: {"proxies": [
                {"ip":"8.8.8.8","port":8080,"protocols":["http"],
                 "sources":["geonode"]}
            ]},
            sources={"geonode":{"errors":[]}},
        )

    def test_direct_failed_does_not_skip_proxy_and_proxy_pass_is_ok(self):
        calls=[]
        def verify(entries, **kwargs):
            calls.append(kwargs)
            return [Check("8.8.8.8",8080,"http","passed",50,"ok","2026-10-28T12:00:00+00:00")]
        report=run_diagnostic(
            direct_check=lambda timeout: {"status":"inconclusive","reason":"timeout",
                                          "failed_stage":"tls","steps":[]},
            collect=lambda ids:self.report(),
            verify=verify,
        )
        self.assertEqual(report["status"],"ok")
        self.assertTrue(report["live_proxy_available"])
        self.assertEqual(report["tested_endpoints"],1)
        self.assertFalse(calls[0]["baseline"])
        self.assertEqual(calls[0]["workers"],4)
        self.assertEqual(calls[0]["max_candidates"],12)

    def test_sample_failure_not_systemwide_block(self):
        def verify(entries, **kwargs):
            return [Check("8.8.8.8",8080,"http","failed",None,"timeout","2026-10-28T12:00:00+00:00")]
        report=run_diagnostic(
            direct_check=lambda timeout: {"status":"blocked","reason":"permission_denied",
                                          "failed_stage":"tcp","steps":[]},
            collect=lambda ids:self.report(),verify=verify,
        )
        self.assertEqual(report["status"],"inconclusive")
        self.assertEqual(report["proxy_results"]["http"]["status"],"failed")
        self.assertTrue(report["tests_completed"])
        self.assertFalse(report["live_proxy_available"])

    def test_fallback_source_and_redacted_error(self):
        calls=[]
        def collect(ids):
            calls.extend(ids)
            if ids == ["geonode"]:
                return SimpleNamespace(
                    to_dict=lambda: {"proxies":[]},
                    sources={"geonode":{"errors":["HTTPError: HTTP Error 403: secret/token"]}},
                )
            return self.report()
        def verify(entries, **kwargs):
            return [Check("8.8.8.8",8080,"http","passed",40,"ok","2026-10-28T12:00:00+00:00")]
        outcome=run_diagnostic(
            direct_check=lambda timeout: {"status":"failed","reason":"tls_handshake_failed",
                                          "failed_stage":"tls","steps":[]},
            collect=collect,verify=verify,
        )
        self.assertEqual(calls,["geonode","hproxy"])
        self.assertEqual(outcome["collection"]["sources"][0]["reason"],"source_http_forbidden")
        self.assertEqual(outcome["collection"]["sources"][1]["status"],"ok")
        self.assertEqual(outcome["status"],"ok")
        self.assertTrue(outcome["live_proxy_available"])
        self.assertNotIn("secret",json.dumps(outcome))

    def test_source_codes_are_allowlisted(self):
        self.assertEqual(_safe_source_reason(["HTTPError: HTTP Error 429: API token=123"]),
                         "source_rate_limited")
        self.assertEqual(_safe_source_reason(["ValueError: sensitive payload"]),
                         "source_invalid_or_oversized_response")
        self.assertEqual(_safe_source_reason(["URLError: Authorization: ABC"]),
                         "source_network_error")

    def test_collection_failure_is_independent_and_sanitized(self):
        def broken(ids):
            raise ValueError("https://secret:password@host/path")
        report=run_diagnostic(
            direct_check=lambda timeout: {"status":"ok","reason":"verified_https_content",
                                          "failed_stage":None,"steps":[]},
            collect=broken,
        )
        self.assertEqual(report["direct"]["status"],"ok")
        self.assertEqual(report["collection"]["status"],"inconclusive")
        self.assertFalse(report["tests_completed"])
        self.assertNotIn("password",json.dumps(report))
        self.assertNotIn("https://",json.dumps(report))

if __name__ == "__main__":
    unittest.main()
