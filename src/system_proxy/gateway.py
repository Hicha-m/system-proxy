"""Loopback-only, allowlisted, fail-closed HTTPS CONNECT gateway.

Supports HTTP CONNECT, SOCKS4a and SOCKS5 NO-AUTH upstreams previously verified
by system-proxy. No transparent HTTP interception: TLS traffic is opaque.
This is a local application transport, not an open public proxy.
"""
from __future__ import annotations

import argparse
import contextlib
import hmac
import os
import sqlite3
import select as multiplex
import socket
import socketserver
import struct
import threading
import time
from pathlib import Path
from typing import Callable

from .destination import domain_key
from .history import History
from .gateway_history import GatewayAttempts
from .select import ranked_candidates

MAX_HEADER = 8192
MAX_CLIENTS = 16
MAX_TUNNEL_SECONDS = 120
MAX_BYTES_PER_DIRECTION = 16 * 1024 * 1024
_CONNECT_TIMEOUT = 7.0


class UpstreamDenied(ConnectionError):
    """The upstream explicitly declined CONNECT; no retry for this request.

    Could reflect proxy policy or destination access controls.
    """



def authority(value: str) -> tuple[str, int]:
    """Allowlisted public DNS hostname:443 only. Reject IPs and exotic syntax."""
    if not isinstance(value, str) or ":" not in value:
        raise ValueError("Expected hostname:443")
    name, separator, port = value.rpartition(":")
    if separator != ":" or port != "443":
        raise ValueError("Only port 443 is supported")
    return domain_key(name), 443


def recv_exact(conn: socket.socket, length: int) -> bytes:
    if not 0 <= length <= 8192:
        raise ValueError("Invalid response size")
    content = bytearray()
    while len(content) < length:
        chunk = conn.recv(length - len(content))
        if not chunk:
            raise ConnectionError("Upstream closed connection")
        content += chunk
    return bytes(content)


def upstream_tunnel(ip: str, port: int, protocol: str, host: str,
                    *, timeout: float = _CONNECT_TIMEOUT,
                    dial: Callable = socket.create_connection) -> socket.socket:
    """Connect through the chosen public upstream, never directly to the target."""
    from .collect import normalize_endpoint
    normalize_endpoint(f"{ip}:{port}")
    host = domain_key(host)
    if protocol not in {"http", "socks4", "socks5"} or not 0 < timeout <= 30:
        raise ValueError("Unsupported upstream")
    conn = dial((ip, port), timeout=timeout)
    try:
        conn.settimeout(timeout)
        target = host.encode("ascii")
        if protocol == "http":
            conn.sendall(b"CONNECT " + target + b":443 HTTP/1.1\r\nHost: " +
                         target + b":443\r\nConnection: keep-alive\r\n\r\n")
            headers = bytearray()
            while b"\r\n\r\n" not in headers:
                piece = conn.recv(1)
                if not piece or len(headers) >= 4096:
                    raise ConnectionError("Invalid HTTP proxy response")
                headers += piece
            status = headers.split(b"\r\n", 1)[0]
            fields = status.split(b" ", 2)
            if len(fields) < 2 or fields[0] not in {b"HTTP/1.0", b"HTTP/1.1"}:
                raise ConnectionError("Malformed HTTP CONNECT response")
            if fields[1] != b"200":
                raise UpstreamDenied("HTTP CONNECT refused by upstream")
        elif protocol == "socks5":
            conn.sendall(b"\x05\x01\x00")
            if recv_exact(conn, 2) != b"\x05\x00":
                raise ConnectionError("SOCKS5 authentication rejected")
            conn.sendall(b"\x05\x01\x00\x03" + bytes((len(target),)) +
                         target + struct.pack("!H", 443))
            result = recv_exact(conn, 4)
            if result[:3] != b"\x05\x00\x00":
                raise UpstreamDenied("SOCKS5 CONNECT refused by upstream")
            if result[3] == 1:
                recv_exact(conn, 6)
            elif result[3] == 4:
                recv_exact(conn, 18)
            elif result[3] == 3:
                recv_exact(conn, recv_exact(conn, 1)[0] + 2)
            else:
                raise ConnectionError("SOCKS5 invalid reply")
        else:
            conn.sendall(b"\x04\x01\x01\xbb\x00\x00\x00\x01\x00" +
                         target + b"\x00")
            result = recv_exact(conn, 8)
            if result[:2] != b"\x00\x5a":
                raise UpstreamDenied("SOCKS4a CONNECT refused by upstream")
        return conn
    except BaseException:
        conn.close()
        raise


def relay(left: socket.socket, right: socket.socket,
          *, lifetime: float = MAX_TUNNEL_SECONDS,
          max_bytes: int = MAX_BYTES_PER_DIRECTION) -> None:
    """Half-close when possible; time and per-direction byte budgets are bounded."""
    ends = [left, right]
    remaining = {left: max_bytes, right: max_bytes}
    active = set(ends)
    until = time.monotonic() + lifetime
    for s in ends:
        s.settimeout(None)
    while active and time.monotonic() < until:
        ready, _, _ = multiplex.select(list(active), [], [], min(1, max(0.0, until-time.monotonic())))
        for origin in ready:
            if remaining[origin] <= 0:
                return
            chunk = origin.recv(min(16_384, remaining[origin]))
            other = right if origin is left else left
            if not chunk:
                active.remove(origin)
                with contextlib.suppress(OSError):
                    other.shutdown(socket.SHUT_WR)
                continue
            remaining[origin] -= len(chunk)
            other.sendall(chunk)


class GatewayHandler(socketserver.BaseRequestHandler):
    def handle(self):
        server: Gateway = self.server
        # Refuse rather than queue excessive socket handlers.
        if not server.capacity.acquire(blocking=False):
            self._reply(503)
            return
        try:
            self.request.settimeout(10)
            packet = bytearray()
            while b"\r\n\r\n" not in packet:
                piece = self.request.recv(min(1024, MAX_HEADER + 1 - len(packet)))
                if not piece or len(packet) >= MAX_HEADER:
                    self._reply(400)
                    return
                packet.extend(piece)
            raw_header, _, trailing = bytes(packet).partition(b"\r\n\r\n")
            lines = raw_header.split(b"\r\n")
            try:
                method, address, version = lines[0].decode("ascii").split(" ")
                if method != "CONNECT" or version != "HTTP/1.1":
                    self._reply(405)
                    return
                host, _ = authority(address)
                if host not in server.allowed_domains:
                    self._reply(403)
                    return
                # No request body or pipelined pre-TLS data allowed.
                if trailing:
                    self._reply(400)
                    return
                if server.token is not None:
                    credentials = [
                        line.split(b":", 1)[1].strip()
                        for line in lines[1:]
                        if line.lower().startswith(b"proxy-authorization:")
                    ]
                    expected = ("Bearer " + server.token).encode()
                    if len(credentials) != 1 or not hmac.compare_digest(credentials[0], expected):
                        self._reply(407, b"Proxy-Authenticate: Bearer\r\n")
                        return
            except (ValueError, UnicodeDecodeError):
                self._reply(400)
                return
            try:
                remote, state = server.open_verified(host)
            except (sqlite3.Error, OSError, ValueError):
                # Database access is mandatory: never fall back to raw lists.
                self._reply(503)
                return
            if remote is None:
                self._reply(503 if state == "empty" else 502)
                return
            # Retry is finished before replying 200. After this line, the
            # client's TLS stream is opaque and cannot be safely replayed.
            with remote:
                try:
                    self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    relay(self.request, remote, lifetime=server.max_tunnel_seconds)
                except (OSError, TimeoutError, ConnectionError):
                    return
        finally:
            server.capacity.release()

    def _reply(self, code: int, headers: bytes = b""):
        messages = {400: b"Bad Request",403: b"Forbidden",405: b"Method Not Allowed",
                    407: b"Proxy Authentication Required",502: b"Bad Gateway",
                    503: b"Service Unavailable"}
        with contextlib.suppress(OSError):
            self.request.sendall(b"HTTP/1.1 " + str(code).encode() + b" " +
                                 messages[code] + b"\r\n" + headers +
                                 b"Content-Length: 0\r\nConnection: close\r\n\r\n")


class Gateway(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *, port: int, db_path: Path, allowed_domains: set[str],
                 protocol: str, freshness_seconds: int, min_score: float,
                 token: str | None, timeout: float, max_tunnel_seconds: int,
                 require_domain_history: bool = False,
                 max_attempts: int = 3, cooldown_seconds: int = 90):
        if not (port == 0 or 1024 <= port <= 65535) or not 1 <= max_tunnel_seconds <= 600:
            raise ValueError("Unsafe listener or time limits")
        if not 0 < timeout <= 30 or not 1 <= freshness_seconds <= 2_592_000:
            raise ValueError("Unsafe validation limits")
        if protocol not in {"http", "socks4", "socks5"}:
            raise ValueError("Unknown upstream protocol")
        if not 1 <= max_attempts <= 5 or not 1 <= cooldown_seconds <= 3600:
            raise ValueError("Unsafe failover limits")
        if not allowed_domains or len(allowed_domains) > 30:
            raise ValueError("Must provide 1-30 explicit allowed domains")
        if token is not None and len(token) < 24:
            raise ValueError("Use a token of at least 24 characters")
        self.allowed_domains = {domain_key(d) for d in allowed_domains}
        self.db_path = Path(db_path)
        self.protocol = protocol
        self.freshness_seconds = freshness_seconds
        self.min_score = min_score
        self.require_domain_history = require_domain_history
        self.token = token
        self.timeout = timeout
        self.max_tunnel_seconds = max_tunnel_seconds
        self.capacity = threading.BoundedSemaphore(MAX_CLIENTS)
        self.max_attempts = max_attempts
        self.cooldown_seconds = cooldown_seconds
        self.rotation_lock = threading.Lock()
        self.rotation_cursors: dict[str, int] = {}
        # Always loopback; never trust a caller-supplied bind address.
        super().__init__(("127.0.0.1", port), GatewayHandler)


    def _rotation_offset(self, host: str, size: int) -> int:
        """Round-robin across currently eligible, non-cooling endpoints."""
        with self.rotation_lock:
            cursor = self.rotation_cursors.get(host, 0)
            self.rotation_cursors[host] = cursor + 1
        return cursor % size

    def open_verified(self, host: str) -> tuple[socket.socket | None, str]:
        """Try distinct validated endpoints before 200, never after.

        A policy rejection is TERMINAL for this request, and a temporary
        connection error cools down only this endpoint+protocol+domain.
        A successful tunnel is *not* a successful HTTPS page request.
        """
        with History(self.db_path) as h:
            history = GatewayAttempts(h)
            candidates = ranked_candidates(
                h,
                protocol=self.protocol,
                domain=host if self.require_domain_history else None,
                freshness_seconds=self.freshness_seconds,
                min_score=self.min_score,
                limit=1000,
            )
            excluded = history.cooling_down(
                host, self.protocol, cooldown_seconds=self.cooldown_seconds,
            )
            candidates = [
                candidate for candidate in candidates
                if (candidate["ip"], candidate["port"]) not in excluded
            ]
            if not candidates:
                return None, "empty"
            offset = self._rotation_offset(host, len(candidates))
            ordered = candidates[offset:] + candidates[:offset]
            for candidate in ordered[:self.max_attempts]:
                started = time.monotonic()
                try:
                    remote = upstream_tunnel(
                        candidate["ip"], candidate["port"], self.protocol,
                        host, timeout=self.timeout,
                    )
                except UpstreamDenied:
                    # Explicit upstream refusal (HTTP CONNECT 403, 429, etc.)
                    # must not trigger hopping to another proxy.
                    history.record(
                        candidate["ip"], candidate["port"], self.protocol,
                        host, outcome="denied", reason="policy",
                        latency_ms=min(120_000, round((time.monotonic()-started)*1000, 2)),
                    )
                    return None, "denied"
                except (OSError, TimeoutError, ConnectionError, ValueError) as exc:
                    if isinstance(exc, TimeoutError) or isinstance(exc, socket.timeout):
                        reason = "timeout"
                    elif isinstance(exc, (ConnectionRefusedError, ConnectionResetError, BrokenPipeError)):
                        reason = "network"
                    elif isinstance(exc, ConnectionError):
                        reason = "handshake"
                    elif isinstance(exc, OSError):
                        reason = "network"
                    else:
                        reason = "unknown"
                    history.record(
                        candidate["ip"], candidate["port"], self.protocol,
                        host, outcome="connect_error", reason=reason,
                        latency_ms=min(120_000, round((time.monotonic()-started)*1000, 2)),
                    )
                    continue
                # If persisting the result fails, close the tunnel; do not
                # bypass our mandatory audit trail.
                try:
                    history.record(
                        candidate["ip"], candidate["port"], self.protocol,
                        host, outcome="connected", reason="ok",
                        latency_ms=min(120_000, round((time.monotonic()-started)*1000, 2)),
                    )
                except BaseException:
                    remote.close()
                    raise
                return remote, "connected"
            return None, "exhausted"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="system-proxy-gateway")
    p.add_argument("--db", type=Path, default=Path("proxy-history.sqlite3"))
    p.add_argument("--port", type=int, default=8899)
    p.add_argument("--allow-domain", action="append", required=True,
                   help="Repeat for each exact permitted hostname")
    p.add_argument("--protocol", choices=["http", "socks4", "socks5"], default="http")
    p.add_argument("--require-domain-history", action="store_true")
    p.add_argument("--min-score", type=float, default=0)
    p.add_argument("--freshness-seconds", type=int, default=3600)
    p.add_argument("--timeout", type=float, default=7)
    p.add_argument("--max-tunnel-seconds", type=int, default=120)
    p.add_argument("--max-attempts", type=int, default=3,
                   help="Distinct verified upstreams per CONNECT (1-5)")
    p.add_argument("--cooldown-seconds", type=int, default=90,
                   help="Per-domain upstream failure cooldown (1-3600)")
    args = p.parse_args(argv)
    # Optional local token is not printed or persisted.
    token = os.environ.get("SYSTEM_PROXY_GATEWAY_TOKEN") or None
    try:
        with Gateway(port=args.port, db_path=args.db,
                     allowed_domains=set(args.allow_domain), protocol=args.protocol,
                     require_domain_history=args.require_domain_history,
                     min_score=args.min_score, freshness_seconds=args.freshness_seconds,
                     token=token, timeout=args.timeout,
                     max_tunnel_seconds=args.max_tunnel_seconds,
                     max_attempts=args.max_attempts,
                     cooldown_seconds=args.cooldown_seconds) as server:
            print(f"HTTPS CONNECT gateway on 127.0.0.1:{args.port} "
                  f"for {len(server.allowed_domains)} allowed domain(s); "
                  f"upstream={args.protocol}; authenticated={bool(token)}",flush=True)
            server.serve_forever(poll_interval=0.5)
    except (ValueError, OSError) as exc:
        p.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
