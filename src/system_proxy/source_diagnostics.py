"""Bounded HTTPS diagnostics for the two canonical proxy-list providers.

Only fixed source URLs from SOURCES are accepted. No raw exceptions, IP
addresses, authentication data or response bodies enter diagnostic output.
"""
from __future__ import annotations

import ipaddress
import socket
import ssl
from urllib.parse import urlsplit

from .collect import SOURCES
from .network_diagnostics import _error

ALLOWED = frozenset(("geonode", "hproxy"))


def diagnose_source(source_id: str, *, timeout: float = 3.0,
                    resolver=None, dial=None, tls_context=None) -> dict:
    if source_id not in ALLOWED or not 0 < timeout <= 8:
        raise ValueError("Source or timeout not permitted")
    source = SOURCES[source_id]
    parsed = urlsplit(source.page_url(1, 50))
    if parsed.scheme != "https" or not parsed.hostname or parsed.port not in (None, 443):
        raise ValueError("Invalid source configuration")
    host = parsed.hostname
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    steps = []

    def ok(stage):
        steps.append({"stage": stage, "status": "ok", "reason": "completed"})

    def fail(stage, reason, status="inconclusive"):
        steps.append({"stage": stage, "status": status, "reason": reason})
        return {"source": source_id, "status": status, "failed_stage": stage,
                "reason": reason, "steps": steps}

    def exception(stage, exc):
        status, reason = _error(exc, stage)
        return fail(stage, reason, status)

    resolve = resolver or socket.getaddrinfo
    connect = dial or socket.create_connection
    context = tls_context or ssl.create_default_context

    try:
        answers = resolve(host, 443, socket.AF_INET, socket.SOCK_STREAM)
        ips = []
        for answer in answers:
            try:
                ip = answer[4][0]
                if ipaddress.ip_address(ip).is_global and ip not in ips:
                    ips.append(ip)
            except (ValueError, IndexError, KeyError, TypeError):
                continue
        if not ips:
            return fail("dns", "no_public_ipv4")
        ok("dns")
    except (OSError, ValueError, TypeError) as exc:
        return exception("dns", exc)

    try:
        sock = connect((ips[0], 443), timeout=timeout)
    except OSError as exc:
        return exception("tcp", exc)
    ok("tcp")
    with sock:
        try:
            secure = context().wrap_socket(sock, server_hostname=host)
        except OSError as exc:
            return exception("tls", exc)
        ok("tls")
        with secure:
            try:
                secure.settimeout(timeout)
                secure.sendall(
                    ("GET " + path + " HTTP/1.1\r\nHost: " + host +
                     "\r\nUser-Agent: system-proxy-network-diagnostic/0.1\r\n"
                     "Accept: application/json\r\nConnection: close\r\n\r\n").encode("ascii")
                )
                header = bytearray()
                while b"\r\n\r\n" not in header and len(header) < 8192:
                    part = secure.recv(min(1024, 8192 - len(header)))
                    if not part:
                        break
                    header.extend(part)
                first = bytes(header).split(b"\r\n", 1)[0].split(b" ", 2)
                if len(first) < 2 or first[0] not in (b"HTTP/1.0", b"HTTP/1.1"):
                    return fail("http", "malformed_http_response")
                status = first[1]
                if status == b"200":
                    ok("http")
                    return {"source": source_id, "status": "ok",
                            "failed_stage": None, "reason": "http_200",
                            "steps": steps}
                if status in (b"401", b"403"):
                    return fail("http", "access_denied", "failed")
                if status == b"429":
                    return fail("http", "rate_limited", "failed")
                if status in (b"301", b"302", b"303", b"307", b"308"):
                    return fail("http", "redirect_not_followed", "inconclusive")
                return fail("http", "unexpected_http_status", "failed")
            except OSError as exc:
                return exception("http", exc)
