"""Bounded HTTPS-through-HTTP-proxy checker.

A successful check means the chosen HTTPS origin returned the expected content.
It does not imply anonymity, safety, or success against other target sites.
Only public IP literals are accepted. SOCKS4a and SOCKS5 NO-AUTH supported.
"""
from __future__ import annotations

import concurrent.futures
import ipaddress
import ssl
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
from typing import Callable

from .collect import normalize_endpoint
from .socks import probe_socks


@dataclass(frozen=True)
class Check:
    ip: str
    port: int
    protocol: str
    status: str  # passed, failed, unavailable
    latency_ms: float | None
    reason: str
    checked_at: str


def probe_http(ip: str, port: int, timeout: float, *, target: str = "https://example.com/") -> tuple[bool, str]:
    """Verify TLS through HTTP CONNECT and an expected safe response body.

    The target is deliberately hard-coded by the caller; this is NOT an
    arbitrary-URL fetch API. The TLS chain and hostname are verified by Python.
    """
    normalize_endpoint(f"{ip}:{port}")
    if not (0 < timeout <= 30):
        raise ValueError("Invalid timeout")
    proxy = f"http://{ip}:{port}"
    opener = build_opener(ProxyHandler({"https": proxy}),
                          HTTPSHandler(context=ssl.create_default_context()))
    request = Request(target, headers={"User-Agent": "system-proxy-checker/0.1"})
    with opener.open(request, timeout=timeout) as response:
        content = response.read(128_000)
        if response.status != 200 or b"Example Domain" not in content:
            return False, "unexpected_response"
    return True, "verified_https_content"


def direct_baseline(timeout: float) -> bool:
    """Fail closed when the expected destination is unreachable without proxy."""
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open("https://example.com/", timeout=timeout) as response:
            return response.status == 200 and b"Example Domain" in response.read(128_000)
    except (OSError, URLError, HTTPError):
        return False


Probe = Callable[[str, int, float], tuple[bool, str]]


def check_one(ip: str, port: int, protocol: str, timeout: float,
              probe: Probe = probe_http, socks_probe=probe_socks) -> Check:
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).isoformat()
    try:
        normalize_endpoint(f"{ip}:{port}")
        if protocol not in {"http", "socks4", "socks5"}:
            return Check(ip, port, protocol, "unavailable", None, "protocol_not_implemented", stamp)
        start = time.monotonic()
        if protocol == "http":
            ok, reason = probe(ip, port, timeout)
        else:
            ok, reason = socks_probe(ip, port, timeout, protocol)
        elapsed = round((time.monotonic() - start) * 1000, 2)
        return Check(ip, port, protocol, "passed" if ok else "failed", elapsed, reason[:100], stamp)
    except (OSError, ValueError, TimeoutError, HTTPError, URLError, ssl.SSLError) as exc:
        return Check(ip, port, protocol, "failed", None, type(exc).__name__, stamp)


def verify_candidates(candidates: list[tuple[str, int, str]], *,
                      timeout: float = 5.0, workers: int = 6,
                      max_candidates: int = 100, probe: Probe = probe_http,
                      baseline: bool = True) -> list[Check]:
    if not (0 < timeout <= 30 and 1 <= workers <= 16 and 1 <= max_candidates <= 1000):
        raise ValueError("Unsafe checker limits")
    # One check per endpoint/protocol, preserving order for reporting.
    unique = list(dict.fromkeys(candidates))[:max_candidates]
    for ip, port, _ in unique:
        normalize_endpoint(f"{ip}:{port}")
    if baseline and any(proto in {"http", "socks4", "socks5"} for _, _, proto in unique) and not direct_baseline(timeout):
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).isoformat()
        return [Check(ip, port, proto, "unavailable", None, "check_target_unavailable", stamp)
                for ip, port, proto in unique]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(
            lambda entry: check_one(entry[0], entry[1], entry[2], timeout, probe),
            unique,
        ))
