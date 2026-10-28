"""Compare urllib's normal HTTPS path against a fixed public-IPv4 HTTPS path.

No arbitrary hosts, no redirects, no response bodies or raw exceptions in output.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from .collect import SOURCES, _NoRedirects
from .network_diagnostics import _error

SOURCES_TO_DIAGNOSE = frozenset(("geonode", "hproxy"))


class PublicIPv4HTTPSConnection(http.client.HTTPSConnection):
    """Keep URL Host and TLS SNI, but dial a verified public IPv4 address."""

    def connect(self):
        if self._tunnel_host:
            raise ValueError("HTTP tunnels are not supported")
        host = self.host
        port = self.port
        answers = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        public = []
        for item in answers:
            try:
                address = item[4][0]
                if ipaddress.ip_address(address).is_global and address not in public:
                    public.append(address)
            except (ValueError, IndexError, TypeError):
                continue
        if not public:
            raise OSError("no public IPv4 target")
        sock = socket.create_connection((public[0], port), self.timeout,
                                        self.source_address)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=host)
        except BaseException:
            sock.close()
            raise


class IPv4HTTPSHandler(HTTPSHandler):
    def https_open(self, req):
        return self.do_open(
            PublicIPv4HTTPSConnection, req,
            context=self._context, check_hostname=self._check_hostname,
        )


def _reason(exc: BaseException) -> str:
    """Do not expose the possibly credential-bearing exception string."""
    if isinstance(exc, HTTPError):
        return ("http_forbidden" if exc.code in (401,403)
                else "http_rate_limited" if exc.code == 429
                else "unexpected_http_status")
    inner = exc.reason if isinstance(exc, URLError) else exc
    return _error(inner, "https")[1]


def compare_urllib(source: str, *, timeout: float = 4,
                   opener_factory=None) -> dict:
    """Probe HTTP status/header and ONE byte with each path, no body logging."""
    if source not in SOURCES_TO_DIAGNOSE or not 0 < timeout <= 8:
        raise ValueError("Unapproved diagnostic parameters")
    url = SOURCES[source].page_url(1, 50)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.port not in (None, 443):
        raise ValueError("Not a canonical HTTPS endpoint")
    results = {}
    for mode in ("urllib_default", "urllib_ipv4"):
        try:
            if opener_factory is None:
                handlers = [ProxyHandler({}), _NoRedirects()]
                if mode == "urllib_ipv4":
                    handlers.append(IPv4HTTPSHandler(context=ssl.create_default_context()))
                opener = build_opener(*handlers)
            else:
                opener = opener_factory(mode)
            request = Request(url, headers={
                "User-Agent": "system-proxy-network-diagnostic/0.1",
                "Accept": "application/json",
            })
            with opener.open(request, timeout=timeout) as response:
                response.read(1)
                results[mode] = {
                    "status": "ok" if response.status == 200 else "failed",
                    "reason": "response_read" if response.status == 200 else "unexpected_http_status",
                }
        except (OSError, ValueError, HTTPError, URLError) as exc:
            results[mode] = {"status": "inconclusive", "reason": _reason(exc)}
        except Exception as exc:
            # The broker may raise non-OSError transport exceptions; expose
            # only the exception class from a strict controlled mapping.
            category = ({"RuntimeError": "runtime_error",
                         "TypeError": "transport_type_error",
                         "AttributeError": "transport_attribute_error"}
                        .get(type(exc).__name__, "unexpected_transport_error"))
            results[mode] = {"status": "inconclusive", "reason": category}
    return {"source": source, "paths": results}
