"""Canonical public-source collector.

One source identifier = one fetch pipeline, regardless of how many upstream
projects reference it. Public-list data is UNTRUSTED and NOT locally verified.

No upstream project source code is copied into this module.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


@dataclass(frozen=True)
class Source:
    id: str
    url: str
    format: str
    referenced_by: tuple[str, ...]
    experimental: bool = False

    def page_url(self, page: int, page_size: int) -> str:
        if self.format == "hproxy":
            return self.url  # HProxy returns the complete live JSON list.
        if self.format == "geonode":
            params = {"page": page, "limit": page_size, "sort_by": "lastChecked", "sort_type": "desc"}
        elif self.format == "proxycompass":
            params = {"page": page, "page_size": page_size}
        else:
            raise ValueError(f"Unknown source format: {self.format}")
        return self.url + "?" + urlencode(params)


# These are canonical data providers, NOT additional collectors to run next to
# full Monosans/Maximilianfeix/ProxyGather instances.
SOURCES: dict[str, Source] = {
    "hproxy": Source(
        "hproxy",
        "https://hproxy.com/api/proxy-list?format=json",
        "hproxy",
        ("monosans/proxy-scraper-checker", "maximilianfeix/proxy-scraper"),
    ),
    "geonode": Source(
        "geonode",
        "https://proxylist.geonode.com/api/proxy-list",
        "geonode",
        ("maximilianfeix/proxy-scraper", "Skillter/ProxyGather"),
    ),
    "proxycompass": Source(
        "proxycompass",
        "https://proxycompass.com/api/v1/free-proxies/live",
        "proxycompass",
        (),
        experimental=True,  # API response shape/rate limits not live-validated yet.
    ),
}

SUPPORTED_PROTOCOLS = frozenset({"http", "https", "socks4", "socks5"})
METADATA_FIELDS = (
    "country", "countryCode", "country_code", "city", "asn", "isp",
    "anonymity", "anonymityLevel", "latency", "latency_ms",
    "uptime_pct", "uptime_24h", "uptime_7d", "verification_count",
    "lastChecked", "last_checked", "last_checked_at", "speed", "status",
    "protocols", "protocol", "type",
)
Fetch = Callable[[str, float, int], bytes]


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_fetch(url: str, timeout: float = 12, max_bytes: int = 8_000_000) -> bytes:
    """Retrieve a known HTTPS URL; no environment proxies or HTTP redirects."""
    if urlsplit(url).scheme != "https":
        raise ValueError("Public-source fetching requires HTTPS")
    opener = build_opener(ProxyHandler({}), _NoRedirects())
    request = Request(url, headers={"User-Agent": "system-proxy/0.1", "Accept": "application/json"})
    with opener.open(request, timeout=timeout) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Source response exceeds size limit")
    return data


def normalize_endpoint(value: str) -> tuple[str, int, str | None]:
    """Parse and canonicalize a PUBLIC IP:port; ignore unsafe/local addresses."""
    if not isinstance(value, str) or len(value) > 300:
        raise ValueError("Not a proxy address")
    value = value.strip()
    explicit_protocol = None
    if "://" in value:
        parsed = urlsplit(value)
        explicit_protocol = parsed.scheme.lower()
        if (explicit_protocol not in SUPPORTED_PROTOCOLS
                or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("Invalid proxy URL")
        host, port = parsed.hostname, parsed.port
    else:
        if value.startswith("["):
            match = re.fullmatch(r"\[([0-9a-fA-F:.]+)\]:(\d+)", value)
            if not match:
                raise ValueError("Invalid bracketed IP")
            host, port = match.group(1), int(match.group(2))
        else:
            host, separator, port_text = value.rpartition(":")
            if not separator or not port_text.isascii() or not port_text.isdigit():
                raise ValueError("Invalid endpoint")
            port = int(port_text)
    if not host or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("Invalid port or host")
    ip = ipaddress.ip_address(host)
    if not ip.is_global:
        raise ValueError("Non-public endpoint")
    return ip.compressed.lower(), port, explicit_protocol


def _protocols(row: dict, explicit: str | None) -> set[str]:
    raw = row.get("protocols", row.get("protocol", row.get("type", [])))
    if isinstance(raw, str):
        raw = re.split(r"[,;/\s]+", raw.lower())
    elif not isinstance(raw, list):
        raw = []
    protocols = {str(item).lower() for item in raw} & SUPPORTED_PROTOCOLS
    if explicit:
        protocols.add(explicit)
    return protocols


def _metadata(row: dict) -> dict:
    """Allowlist primitive fields only; never persist arbitrary provider content."""
    metadata: dict = {}
    for key in METADATA_FIELDS:
        value = row.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if value is not None:
                metadata[key] = value[:256] if isinstance(value, str) else value
        elif isinstance(value, list):
            items = [v[:64] for v in value[:12] if isinstance(v, str)]
            if items:
                metadata[key] = items
    return metadata


def _rows(body: bytes) -> list:
    root = json.loads(body.decode("utf-8-sig"))
    for _ in range(5):
        if isinstance(root, list):
            return root
        if not isinstance(root, dict):
            raise ValueError("Unexpected API response")
        for key in ("data", "proxies", "results", "items", "list"):
            if key in root and isinstance(root[key], (dict, list)):
                root = root[key]
                break
        else:
            # Empty-data response is not silently treated as a proxy.
            raise ValueError("Unexpected API response: missing proxy list")
    raise ValueError("Too much nesting in API response")


@dataclass
class Proxy:
    ip: str
    port: int
    protocols: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    observations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ip": self.ip,
            "port": self.port,
            "protocols": sorted(self.protocols),
            "sources": sorted(self.sources),
            "locally_verified": False,
            "observations": self.observations,
        }


@dataclass
class Report:
    proxies: dict[tuple[str, int], Proxy] = field(default_factory=dict)
    sources: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "verification": "not_performed",
            "source_stats": self.sources,
            "unique_endpoints": len(self.proxies),
            "proxies": [
                self.proxies[key].to_dict()
                for key in sorted(self.proxies)
            ],
        }


class Collector:
    def __init__(
        self,
        fetch: Fetch = http_fetch,
        *,
        timeout: float = 12,
        max_pages: int = 2,
        page_size: int = 500,
        max_bytes: int = 8_000_000,
        max_rows: int = 30_000,
    ):
        if not (0 < timeout <= 120 and 1 <= max_pages <= 20 and 1 <= page_size <= 500
                and 1024 <= max_bytes <= 20_000_000 and 1 <= max_rows <= 50_000):
            raise ValueError("Unsafe collector limits")
        self.fetch = fetch
        self.timeout = timeout
        self.max_pages = max_pages
        self.page_size = page_size
        self.max_bytes = max_bytes
        self.max_rows = max_rows

    def collect(self, ids: list[str] | tuple[str, ...] = ("hproxy", "geonode"),
                *, include_experimental: bool = False) -> Report:
        report = Report()
        response_cache: dict[str, bytes] = {}
        for source_id in dict.fromkeys(ids):
            if source_id not in SOURCES:
                raise ValueError(f"Unknown source: {source_id}")
            source = SOURCES[source_id]
            if source.experimental and not include_experimental:
                raise ValueError(f"{source_id} is experimental; explicitly opt in")
            seen: set[tuple[str, int]] = set()
            stats: dict = {"pages": 0, "candidate_rows": 0, "accepted_rows": 0,
                           "invalid_rows": 0, "unique_endpoints": 0,
                           "referenced_by": list(source.referenced_by), "errors": []}
            report.sources[source_id] = stats
            for page in range(1, (1 if source.format == "hproxy" else self.max_pages) + 1):
                url = source.page_url(page, self.page_size)
                if urlsplit(url).hostname != urlsplit(source.url).hostname:
                    raise ValueError("Source host changed")
                try:
                    body = response_cache.get(url)
                    if body is None:
                        body = self.fetch(url, self.timeout, self.max_bytes)
                        if len(body) > self.max_bytes:
                            raise ValueError("Source exceeds response limit")
                        response_cache[url] = body
                    rows = _rows(body)
                    if len(rows) > self.max_rows:
                        raise ValueError("Source exceeds row limit")
                except (ValueError, UnicodeError, OSError, HTTPError) as exc:
                    # Do not abort other providers on one provider's failure.
                    stats["errors"].append(f"{type(exc).__name__}: {str(exc)[:160]}")
                    break
                stats["pages"] += 1
                stats["candidate_rows"] += len(rows)
                if not rows:
                    break
                for row in rows:
                    try:
                        if isinstance(row, str):
                            row = {"proxy": row}
                        if not isinstance(row, dict):
                            raise ValueError("Invalid row")
                        address = row.get("proxy") or row.get("address")
                        if not address:
                            host = row.get("ip", row.get("ip_address"))
                            port = row.get("port")
                            address = f"{host}:{port}" if host is not None and port is not None else ""
                        ip, port, explicit = normalize_endpoint(address)
                        protocols = _protocols(row, explicit)
                        if not protocols:
                            raise ValueError("No supported protocol")
                    except (ValueError, TypeError, OverflowError):
                        stats["invalid_rows"] += 1
                        continue
                    stats["accepted_rows"] += 1
                    key = ip, port
                    proxy = report.proxies.setdefault(key, Proxy(ip, port))
                    proxy.protocols.update(protocols)
                    proxy.sources.add(source.id)
                    if key not in seen:
                        proxy.observations.append({
                            "source": source.id,
                            "metadata": _metadata(row),
                            "reported_by": list(source.referenced_by),
                        })
                        seen.add(key)
                # A shorter page normally means exhaustion, but GeoNode/ProxyCompass
                # can cap the server-side page size. Prefer bounded page count rather
                # than silently treating any short page as last.
            stats["unique_endpoints"] = len(seen)
        return report
