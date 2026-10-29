"""Real loopback TCP gateway integration; upstreams are simulated, never public.

Exercises simultaneous clients, rotation, pre-200 failover, per-domain cooldown,
denials, authenticated access and the configured capacity bound.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import socket
import tempfile
import threading
import time
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from system_proxy.gateway import Gateway, MAX_CLIENTS, UpstreamDenied
from system_proxy.gateway_history import GatewayAttempts
from system_proxy.history import History
from system_proxy.verification import Check

IPS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")
TOKEN = "integration-test-only-token-of-32-chars"


def connect_request(server: Gateway, *, token: str | None = TOKEN,
                    host: str = "example.com") -> int:
    auth = ("Proxy-Authorization: Bearer " + token + "\r\n") if token else ""
    raw = (f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n"
           + auth + "\r\n").encode("ascii")
    with socket.create_connection(server.server_address, timeout=4) as sock:
        sock.settimeout(5)
        sock.sendall(raw)
        buf = bytearray()
        while b"\r\n\r\n" not in buf and len(buf) < 8192:
            part = sock.recv(4096)
            if not part:
                break
            buf.extend(part)
        line = bytes(buf).split(b"\r\n", 1)[0]
        return int(line.split(b" ")[1])


class GatewayLoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "isolated.sqlite3"
        with History(self.db) as h:
            h.import_candidates([{"ip": ip, "port": 8080,
                                  "protocols": ["http"], "sources": ["hproxy"]}
                                 for ip in IPS])
            h.add_checks([Check(ip,8080,"http","passed",20,"verified_https_content",
                                datetime.now(timezone.utc).isoformat())
                          for ip in IPS])
        self.gateway = Gateway(port=0, db_path=self.db,
                               allowed_domains={"example.com"}, protocol="http",
                               freshness_seconds=3600, min_score=0,
                               token=TOKEN, timeout=2, max_tunnel_seconds=3,
                               max_attempts=2, cooldown_seconds=90)
        self.server_thread = threading.Thread(target=self.gateway.serve_forever,
                                              kwargs={"poll_interval":0.01}, daemon=True)
        self.server_thread.start()
        self.peers = []
        self.peers_lock = threading.Lock()

    def tearDown(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.server_thread.join(timeout=3)
        with self.peers_lock:
            for peer in self.peers:
                peer.close()
        self.tmp.cleanup()

    def good_tunnel(self, ip, port, protocol, host, *, timeout):
        gateway_side, peer = socket.socketpair()
        with self.peers_lock:
            self.peers.append(peer)
        return gateway_side

    def test_simultaneous_clients_rotate_and_record_without_leaking(self):
        chosen = []
        lock = threading.Lock()

        def tunnel(ip, port, protocol, host, *, timeout):
            with lock:
                chosen.append(ip)
            return self.good_tunnel(ip, port, protocol, host, timeout=timeout)

        # The real TCP listener, handler threads, SQLite connection and audit
        # path run; only upstream traffic and stream relay are simulated.
        with patch("system_proxy.gateway.upstream_tunnel", side_effect=tunnel), \
             patch("system_proxy.gateway.relay", return_value=None):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                statuses = list(pool.map(lambda _: connect_request(self.gateway),
                                         range(12)))
        self.assertEqual(statuses, [200] * 12)
        self.assertEqual(len(chosen), 12)
        self.assertEqual(set(chosen), set(IPS))
        counts = Counter(chosen)
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
        with History(self.db) as h:
            audit = GatewayAttempts(h).summary("example.com")
            self.assertEqual(audit["connected"], 12)
            self.assertEqual(audit["connect_error"], 0)
            self.assertEqual(h.summary()["checked_endpoints"], 3)

    def test_failover_cooldown_and_policy_denial_through_real_listener(self):
        seen = []
        def tunnel(ip, port, protocol, host, *, timeout):
            seen.append(ip)
            if len(seen) == 1:
                raise ConnectionResetError("simulated")
            return self.good_tunnel(ip, port, protocol, host, timeout=timeout)
        with patch("system_proxy.gateway.upstream_tunnel", side_effect=tunnel), \
             patch("system_proxy.gateway.relay", return_value=None):
            self.assertEqual(connect_request(self.gateway), 200)
        self.assertEqual(len(set(seen)), 2)
        with History(self.db) as h:
            audit = GatewayAttempts(h)
            self.assertIn((seen[0], 8080), audit.cooling_down("example.com","http"))
            self.assertEqual((audit.summary("example.com")["connected"],
                              audit.summary("example.com")["connect_error"]), (1, 1))
        # Denial must not hop across upstreams.
        with patch("system_proxy.gateway.upstream_tunnel",
                   side_effect=UpstreamDenied("simulated policy")) as dial:
            self.assertEqual(connect_request(self.gateway), 502)
            self.assertEqual(dial.call_count,1)
        with patch("system_proxy.gateway.upstream_tunnel") as dial:
            self.assertEqual(connect_request(self.gateway, token=None), 407)
            self.assertEqual(connect_request(self.gateway, host="other.example.com"), 403)
            dial.assert_not_called()

    def test_capacity_is_bounded_and_no_upstream_is_created(self):
        for _ in range(MAX_CLIENTS):
            self.assertTrue(self.gateway.capacity.acquire(False))
        try:
            with patch("system_proxy.gateway.upstream_tunnel") as dial:
                self.assertEqual(connect_request(self.gateway), 503)
                dial.assert_not_called()
        finally:
            for _ in range(MAX_CLIENTS):
                self.gateway.capacity.release()


if __name__ == "__main__":
    unittest.main()
