"""Gateway tests only use loopback and fake upstreams, never real public proxies."""
import contextlib
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from system_proxy.gateway import Gateway, authority, upstream_tunnel
from system_proxy.history import History
from system_proxy.verification import Check


class FakeUpstream:
    def __init__(self, response):
        self.response = bytearray(response)
        self.sent = []
        self.closed = False
    def settimeout(self, seconds):
        pass
    def sendall(self, data):
        self.sent.append(data)
    def recv(self, n):
        piece = bytes(self.response[:n])
        del self.response[:n]
        return piece
    def close(self):
        self.closed = True


class ParserTests(unittest.TestCase):
    def test_authority(self):
        self.assertEqual(authority("EXAMPLE.com:443"), ("example.com",443))
        for address in ("example.com:80", "127.0.0.1:443", "foo.local:443",
                        "example.com:443/path", "example.com", "https://example.com:443",
                        "example.com:443@evil.com"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                authority(address)

    def test_http_upstream_connect(self):
        s = FakeUpstream(b"HTTP/1.1 200 Connection established\r\n\r\n")
        with patch("system_proxy.gateway.socket.create_connection",return_value=s):
            returned = upstream_tunnel("8.8.8.8", 8080, "http", "example.com",
                                        dial=lambda *args,**kwargs:s)
        self.assertIs(returned,s)
        self.assertIn(b"CONNECT example.com:443 HTTP/1.1",b"".join(s.sent))

    def test_http_connect_failure_closes_socket(self):
        s = FakeUpstream(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        with self.assertRaises(ConnectionError):
            upstream_tunnel("8.8.8.8",8080,"http","example.com",
                            dial=lambda *args,**kwargs:s)
        self.assertTrue(s.closed)

    def test_socks5_upstream_success(self):
        s=FakeUpstream(b"\x05\x00\x05\x00\x00\x01\x7f\x00\x00\x01\x01\xbb")
        self.assertIs(upstream_tunnel("8.8.8.8",1080,"socks5","example.com",
                                    dial=lambda *args,**kwargs:s),s)
        self.assertEqual(s.sent[0],b"\x05\x01\x00")
        self.assertIn(b"example.com",s.sent[1])

    def test_socks4_upstream_success(self):
        s=FakeUpstream(b"\x00\x5a\x00\x00\x00\x00\x00\x00")
        self.assertIs(upstream_tunnel("8.8.8.8",1080,"socks4","example.com",
                                    dial=lambda *args,**kwargs:s),s)
        self.assertIn(b"example.com",s.sent[0])


def read_response(sock):
    data=bytearray()
    while b"\r\n\r\n" not in data:
        chunk=sock.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


class ListenerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"history.sqlite3"
        with History(self.db) as h:
            h.import_candidates([{"ip":"8.8.8.8","port":8080,
                                  "protocols":["http"],"sources":["hproxy"]}])
        self.token="x"*32
        self.server=Gateway(port=0,db_path=self.db,
                            allowed_domains={"example.com"},protocol="http",
                            freshness_seconds=3600,min_score=0,
                            token=self.token,timeout=1,max_tunnel_seconds=1)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def request(self,raw):
        with socket.create_connection(self.server.server_address,timeout=2) as s:
            s.settimeout(2)
            s.sendall(raw)
            return read_response(s)

    def test_loopback_binding_and_denials(self):
        self.assertEqual(self.server.server_address[0],"127.0.0.1")
        self.assertIn(b" 403 ",self.request(
            b"CONNECT example.org:443 HTTP/1.1\r\n\r\n"))
        self.assertIn(b" 405 ",self.request(
            b"GET http://example.com/ HTTP/1.1\r\n\r\n"))
        self.assertIn(b" 407 ",self.request(
            b"CONNECT example.com:443 HTTP/1.1\r\n\r\n"))
        self.assertIn(b" 400 ",self.request(
            b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n"))

    def test_no_usable_proxy_fails_closed(self):
        raw=(b"CONNECT example.com:443 HTTP/1.1\r\n"+
             b"Proxy-Authorization: Bearer "+self.token.encode()+b"\r\n\r\n")
        self.assertIn(b" 503 ",self.request(raw))

    def test_verified_upstream_then_connect(self):
        with History(self.db) as h:
            h.add_checks([Check("8.8.8.8",8080,"http","passed",30,
                                "ok",datetime.now(timezone.utc).isoformat())])
        raw=(b"CONNECT example.com:443 HTTP/1.1\r\n"+
             b"Proxy-Authorization: Bearer "+self.token.encode()+b"\r\n\r\n")
        a,b=socket.socketpair()
        try:
            with patch("system_proxy.gateway.upstream_tunnel",return_value=b):
                with patch("system_proxy.gateway.relay",return_value=None):
                    self.assertIn(b" 200 ",self.request(raw))
        finally:
            a.close()
            with contextlib.suppress(OSError):
                b.close()

    def test_transient_upstream_failure_returns_502_before_connect(self):
        with History(self.db) as h:
            h.add_checks([Check("8.8.8.8",8080,"http","passed",20,
                                "ok",datetime.now(timezone.utc).isoformat())])
        raw=(b"CONNECT example.com:443 HTTP/1.1\r\n"+
             b"Proxy-Authorization: Bearer "+self.token.encode()+b"\r\n\r\n")
        with patch("system_proxy.gateway.upstream_tunnel",
                   side_effect=ConnectionResetError("simulated")) as dial:
            self.assertIn(b" 502 ", self.request(raw))
            self.assertEqual(dial.call_count, 1)
        with History(self.db) as h:
            from system_proxy.gateway_history import GatewayAttempts
            report=GatewayAttempts(h).summary("example.com")
            self.assertEqual(report["connect_error"], 1)
            self.assertEqual(report["connected"], 0)

    def test_tls_stream_failure_never_reconnects_after_200(self):
        with History(self.db) as h:
            h.add_checks([Check("8.8.8.8",8080,"http","passed",20,
                                "ok",datetime.now(timezone.utc).isoformat())])
        raw=(b"CONNECT example.com:443 HTTP/1.1\r\n"+
             b"Proxy-Authorization: Bearer "+self.token.encode()+b"\r\n\r\n")
        local,remote=socket.socketpair()
        try:
            with patch("system_proxy.gateway.upstream_tunnel",return_value=remote) as dial:
                with patch("system_proxy.gateway.relay",
                           side_effect=BrokenPipeError("simulated")):
                    self.assertIn(b" 200 ",self.request(raw))
            self.assertEqual(dial.call_count,1)
            with History(self.db) as h:
                from system_proxy.gateway_history import GatewayAttempts
                self.assertEqual(GatewayAttempts(h).summary("example.com")["connected"],1)
        finally:
            local.close()
            with contextlib.suppress(OSError):
                remote.close()

    def test_over_capacity_is_rejected_without_spawning_handler(self):
        from system_proxy.gateway import MAX_CLIENTS
        for _ in range(MAX_CLIENTS):
            self.assertTrue(self.server.capacity.acquire(blocking=False))
        try:
            with patch("system_proxy.gateway.GatewayHandler.handle",
                       side_effect=AssertionError("should not spawn")):
                self.assertIn(b" 503 ",self.request(
                    b"CONNECT example.com:443 HTTP/1.1\r\n\r\n"))
        finally:
            for _ in range(MAX_CLIENTS):
                self.server.capacity.release()

    def test_refuses_non_loopback_and_missing_allowlist(self):
        with self.assertRaises(ValueError):
            Gateway(port=9000,db_path=self.db,allowed_domains=set(),
                    protocol="http",freshness_seconds=3600,
                    min_score=0,token=None,timeout=1,max_tunnel_seconds=30)


if __name__=="__main__":
    unittest.main()
