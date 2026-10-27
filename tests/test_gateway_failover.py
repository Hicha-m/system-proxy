"""Offline tests: gateway failover, cooldown, policy rejection and audit trail."""
from __future__ import annotations

import socket
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from system_proxy.gateway import Gateway, UpstreamDenied, upstream_tunnel
from system_proxy.gateway_history import GatewayAttempts
from system_proxy.history import History
from system_proxy.select import ranked_candidates
from system_proxy.verification import Check

IPS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")


def stamp(seconds: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


class FakeUpstream:
    def __init__(self, reply: bytes):
        self.reply = bytearray(reply)
        self.closed = False

    def sendall(self, data):
        pass

    def settimeout(self, timeout):
        pass

    def recv(self, n):
        response = bytes(self.reply[:n])
        del self.reply[:n]
        return response

    def close(self):
        self.closed = True


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "history.sqlite3"
        self.history = History(self.db_path)
        self.history.import_candidates([
            {"ip": ip, "port": 8080, "protocols": ["http"],
             "sources": ["hproxy"]}
            for ip in IPS
        ])
        self.audit = GatewayAttempts(self.history)

    def tearDown(self):
        self.history.close()
        self.directory.cleanup()

    def test_failure_cooldown_is_per_domain_not_global(self):
        self.history.add_checks([
            Check(IPS[0], 8080, "http", "passed", 40.0, "ok", stamp().isoformat())
        ])
        self.audit.record(
            IPS[0], 8080, "http", "example.com",
            outcome="connect_error", reason="network",
            latency_ms=17.2,
        )
        self.assertIn((IPS[0], 8080), self.audit.cooling_down("example.com", "http"))
        self.assertEqual(self.audit.cooling_down("another.example.com", "http"), set())
        self.assertEqual(self.audit.cooling_down("example.com", "socks5"), set())
        # A gateway CONNECT failure must not overwrite general checker results.
        summary = self.history.summary()["endpoints"]
        item = next(p for p in summary if p["ip"] == IPS[0])
        self.assertEqual((item["passed"], item["failed"]), (1, 0))
        self.assertTrue(item["usable_by_recent_check"])

    def test_cooldown_expiry_and_success_clears_previous_failure(self):
        self.audit.record(IPS[0], 8080, "http", "example.com",
                          outcome="connect_error", reason="timeout",
                          at=stamp(-180))
        self.assertNotIn((IPS[0], 8080),
                         self.audit.cooling_down("example.com", "http", cooldown_seconds=90))
        self.audit.record(IPS[0], 8080, "http", "example.com",
                          outcome="denied", reason="policy")
        self.assertIn((IPS[0], 8080),
                      self.audit.cooling_down("example.com", "http", cooldown_seconds=90))
        self.audit.record(IPS[0], 8080, "http", "example.com",
                          outcome="connected", reason="ok", latency_ms=25)
        self.assertNotIn((IPS[0], 8080),
                         self.audit.cooling_down("example.com", "http", cooldown_seconds=90))
        result = self.audit.summary("example.com")
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["connected"], 1)
        self.assertEqual(result["connect_error"], 1)
        self.assertEqual(result["denied"], 1)

    def test_invalid_metadata_rejected(self):
        for kwargs in (
            {"ip": "127.0.0.1", "reason": "network"},
            {"domain": "localhost", "reason": "network"},
            {"outcome": "invented", "reason": "network"},
            {"reason": "raw upstream password", "outcome": "connect_error"},
            {"latency_ms": -4, "reason": "network"},
            {"at": stamp(3600), "reason": "network"},
        ):
            params = dict(
                ip=IPS[0], port=8080, protocol="http", domain="example.com",
                outcome="connect_error", reason="network",
            )
            params.update(kwargs)
            with self.subTest(params=kwargs), self.assertRaises(ValueError):
                self.audit.record(**params)


class FailoverTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "history.sqlite3"
        with History(self.db_path) as h:
            h.import_candidates([
                {"ip": ip, "port": 8080, "protocols": ["http"],
                 "sources": ["hproxy"]}
                for ip in IPS
            ])
            h.add_checks([
                Check(ip, 8080, "http", "passed", 20.0, "ok", stamp().isoformat())
                for ip in IPS
            ])
        self.server = Gateway(
            port=0, db_path=self.db_path, allowed_domains={"example.com"},
            protocol="http", freshness_seconds=3600, min_score=0,
            token=None, timeout=1, max_tunnel_seconds=10,
            max_attempts=2, cooldown_seconds=90,
        )

    def tearDown(self):
        self.server.server_close()
        self.directory.cleanup()

    def test_transient_failure_retries_different_verified_proxy(self):
        attempted = []
        local, remote = socket.socketpair()

        def fake_connect(ip, port, protocol, host, *, timeout):
            attempted.append(ip)
            if len(attempted) == 1:
                raise ConnectionResetError("simulate broken proxy")
            return remote

        try:
            with patch("system_proxy.gateway.upstream_tunnel", side_effect=fake_connect):
                connected, status = self.server.open_verified("example.com")
            self.assertEqual(status, "connected")
            self.assertIs(connected, remote)
            self.assertEqual(len(attempted), 2)
            self.assertNotEqual(attempted[0], attempted[1])
            with History(self.db_path) as h:
                records = GatewayAttempts(h)
                stats = records.summary("example.com")
                self.assertEqual((stats["connect_error"], stats["connected"]), (1, 1))
                self.assertIn((attempted[0], 8080),
                              records.cooling_down("example.com", "http"))
                self.assertEqual(len(ranked_candidates(h, protocol="http")), 3)
                # All globally verified endpoints remain eligible by checker.
                self.assertEqual(h.summary()["checked_endpoints"], 3)
        finally:
            local.close()
            remote.close()

    def test_round_robin_across_healthy_candidates(self):
        attempted = []
        peers = []

        def fake_connect(ip, port, protocol, host, *, timeout):
            a, b = socket.socketpair()
            peers.append(b)
            attempted.append(ip)
            return a

        try:
            with patch("system_proxy.gateway.upstream_tunnel", side_effect=fake_connect):
                for _ in range(3):
                    sock, state = self.server.open_verified("example.com")
                    self.assertEqual(state, "connected")
                    sock.close()
            self.assertEqual(len(set(attempted)), 3)
        finally:
            for peer in peers:
                peer.close()

    def test_upstream_denial_never_fails_over(self):
        attempted = []
        def fake_connect(ip, port, protocol, host, *, timeout):
            attempted.append(ip)
            raise UpstreamDenied("403 policy")
        with patch("system_proxy.gateway.upstream_tunnel", side_effect=fake_connect):
            sock, state = self.server.open_verified("example.com")
        self.assertIsNone(sock)
        self.assertEqual(state, "denied")
        self.assertEqual(len(attempted), 1)
        with History(self.db_path) as h:
            history = GatewayAttempts(h)
            self.assertEqual(history.summary("example.com")["denied"], 1)
            self.assertIn((attempted[0], 8080),
                          history.cooling_down("example.com", "http"))

    def test_exhaustion_bounded_and_only_one_attempt_per_endpoint(self):
        attempted = []

        def fake_connect(ip, port, protocol, host, *, timeout):
            attempted.append(ip)
            raise TimeoutError("timed out")

        with patch("system_proxy.gateway.upstream_tunnel", side_effect=fake_connect):
            sock, state = self.server.open_verified("example.com")
        self.assertIsNone(sock)
        self.assertEqual(state, "exhausted")
        self.assertEqual(len(attempted), 2)
        self.assertEqual(len(set(attempted)), 2)
        with History(self.db_path) as h:
            self.assertEqual(GatewayAttempts(h).summary("example.com")["connect_error"], 2)

    def test_no_unverified_fallback(self):
        with History(self.db_path) as h:
            h.add_checks([
                Check(ip, 8080, "http", "failed", None, "timeout", stamp().isoformat())
                for ip in IPS
            ])
        with patch("system_proxy.gateway.upstream_tunnel") as call:
            sock, status = self.server.open_verified("example.com")
        self.assertIsNone(sock)
        self.assertEqual(status, "empty")
        call.assert_not_called()

    def test_failover_limits(self):
        for extra in ({"max_attempts": 6}, {"cooldown_seconds": 0},
                      {"cooldown_seconds": 3601}):
            options = dict(port=0, db_path=self.db_path,
                           allowed_domains={"example.com"}, protocol="http",
                           freshness_seconds=3600, min_score=0,
                           token=None, timeout=1, max_tunnel_seconds=10)
            options.update(extra)
            with self.assertRaises(ValueError):
                Gateway(**options)


class HandshakeDenialTests(unittest.TestCase):
    def test_http_403_is_not_a_transient_connection_error(self):
        from system_proxy.gateway import UpstreamDenied
        upstream = FakeUpstream(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        with self.assertRaises(UpstreamDenied):
            upstream_tunnel("8.8.8.8", 8080, "http", "example.com",
                            dial=lambda *args, **kwargs: upstream)
        self.assertTrue(upstream.closed)


if __name__ == "__main__":
    unittest.main()
