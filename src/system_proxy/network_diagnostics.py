"""Fixed-destination direct HTTPS diagnostic with sanitized, stage-specific results.

No arbitrary target, environment HTTP proxy, raw exception text or credentials.
This is a diagnostic, not a substitute for proxy-side verification.
"""
from __future__ import annotations

import errno
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

HOST = "example.com"
PORT = 443
STAGES = ("dns", "tcp", "tls", "http")
STATES = frozenset({"ok", "failed", "blocked", "inconclusive"})


def _error(exc: BaseException, stage: str) -> tuple[str, str]:
    """Whitelist categories only; never log raw exceptions or network addresses."""
    if isinstance(exc, PermissionError) or (
        isinstance(exc, OSError) and exc.errno in {errno.EPERM, errno.EACCES}
    ):
        return "blocked", "permission_denied"
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "failed", "certificate_verification_failed"
    if isinstance(exc, ssl.SSLError):
        return "failed", "tls_handshake_failed"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "inconclusive", "timeout"
    if isinstance(exc, socket.gaierror):
        return "inconclusive", "dns_resolution_error"
    if isinstance(exc, ConnectionRefusedError):
        return "failed", "connection_refused"
    if isinstance(exc, (ConnectionError, BrokenPipeError)):
        return "inconclusive", "connection_interrupted"
    if isinstance(exc, OSError):
        return "inconclusive", f"{stage}_io_error"
    return "inconclusive", "unexpected_error"


def diagnose_direct_https(timeout: float = 3.0, *, resolver=None,
                          dial=None, tls_context=None) -> dict:
    """Check DNS -> TCP -> verified TLS -> HTTP at the fixed safe origin.

    Uses only public IPv4, consistent with the MCP's TCP egress policy.
    Injected dependencies permit unit tests with no network activity.
    """
    if not 0 < timeout <= 15:
        raise ValueError("Unsafe diagnostic timeout")
    resolver = resolver or socket.getaddrinfo
    dial = dial or socket.create_connection
    tls_context = tls_context or ssl.create_default_context
    steps: list[dict] = []
    def success(stage: str) -> None:
        steps.append({"stage": stage, "status": "ok", "reason": "completed"})
    def failure(stage: str, exc: BaseException) -> dict:
        status, reason = _error(exc, stage)
        steps.append({"stage": stage, "status": status, "reason": reason})
        return {"status": status, "failed_stage": stage, "reason": reason,
                "steps": steps}

    try:
        addresses = resolver(HOST, PORT, socket.AF_INET, socket.SOCK_STREAM)
        public = []
        for result in addresses:
            address = result[4][0]
            try:
                if ipaddress.ip_address(address).is_global and address not in public:
                    public.append(address)
            except ValueError:
                continue
        if not public:
            steps.append({"stage": "dns", "status": "inconclusive",
                          "reason": "no_public_ipv4"})
            return {"status": "inconclusive", "failed_stage": "dns",
                    "reason": "no_public_ipv4", "steps": steps}
        success("dns")
    except (OSError, ValueError) as exc:
        return failure("dns", exc)

    try:
        connection = dial((public[0], PORT), timeout=timeout)
    except OSError as exc:
        return failure("tcp", exc)
    success("tcp")

    with connection:
        try:
            context = tls_context()
            secure = context.wrap_socket(connection, server_hostname=HOST)
        except OSError as exc:
            return failure("tls", exc)
        success("tls")
        with secure:
            try:
                secure.settimeout(timeout)
                secure.sendall(
                    b"GET / HTTP/1.1\r\n"
                    b"Host: example.com\r\n"
                    b"User-Agent: system-proxy-network-diagnostic/0.1\r\n"
                    b"Connection: close\r\n\r\n"
                )
                response = bytearray()
                while len(response) < 128_000:
                    block = secure.recv(min(8192, 128_000-len(response)))
                    if not block:
                        break
                    response.extend(block)
                    if b"Example Domain" in response and b"\r\n\r\n" in response:
                        break
                raw = bytes(response)
                headers, marker, body = raw.partition(b"\r\n\r\n")
                line = headers.split(b"\r\n", 1)[0]
                parts = line.split(b" ", 2)
                if not marker or len(parts) < 2 or not parts[0].startswith(b"HTTP/1."):
                    steps.append({"stage": "http", "status": "inconclusive",
                                  "reason": "malformed_http_response"})
                    return {"status": "inconclusive", "failed_stage": "http",
                            "reason": "malformed_http_response", "steps": steps}
                if parts[1] != b"200":
                    reason = ("rate_limited" if parts[1] == b"429"
                              else "access_denied" if parts[1] == b"403"
                              else "unexpected_http_status")
                    steps.append({"stage": "http", "status": "failed", "reason": reason})
                    return {"status": "failed", "failed_stage": "http",
                            "reason": reason, "steps": steps}
                if b"Example Domain" not in body:
                    steps.append({"stage": "http", "status": "failed",
                                  "reason": "unexpected_content"})
                    return {"status": "failed", "failed_stage": "http",
                            "reason": "unexpected_content", "steps": steps}
                success("http")
                return {"status": "ok", "failed_stage": None,
                        "reason": "verified_https_content", "steps": steps}
            except OSError as exc:
                return failure("http", exc)
