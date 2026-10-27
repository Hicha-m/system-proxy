"""All tests are offline and use fixtures; no public proxies or API keys."""

from __future__ import annotations

import json
import unittest
from urllib.parse import parse_qs, urlsplit

from system_proxy.collect import (
    Collector, SOURCES, normalize_endpoint, _rows, http_fetch,
)


def as_bytes(value):
    return json.dumps(value).encode("utf-8")


class NormalizeTests(unittest.TestCase):
    def test_public_endpoints_and_protocol(self):
        self.assertEqual(
            normalize_endpoint("SOCKS5://8.8.8.8:1080"),
            ("8.8.8.8", 1080, "socks5"),
        )
        self.assertEqual(normalize_endpoint("1.1.1.1:80"), ("1.1.1.1", 80, None))
        self.assertEqual(
            normalize_endpoint("[2606:4700:4700::1111]:8080"),
            ("2606:4700:4700::1111", 8080, None),
        )

    def test_rejects_private_credentials_hostname_and_malformed(self):
        bad = [
            "127.0.0.1:80", "192.168.1.1:80", "10.0.0.1:3128",
            "localhost:8080", "example.com:80", "1.1.1.1:0",
            "1.1.1.1:65536", "http://user:pass@8.8.8.8:80",
            "http://8.8.8.8:80/internal", "1.1.1.1:port",
            "ftp://8.8.8.8:21", "8.8.8.8:80?secret=1",
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_endpoint(value)

    def test_registry_links_without_duplicate_canonical_ids(self):
        self.assertIn("monosans/proxy-scraper-checker", SOURCES["hproxy"].referenced_by)
        self.assertIn("maximilianfeix/proxy-scraper", SOURCES["hproxy"].referenced_by)
        self.assertIn("Skillter/ProxyGather", SOURCES["geonode"].referenced_by)
        self.assertTrue(SOURCES["proxycompass"].experimental)
        self.assertEqual(len(SOURCES), len(set(SOURCES)))


class ParserTests(unittest.TestCase):
    def test_json_shapes(self):
        expected = [{"ip": "8.8.8.8", "port": 80, "protocols": ["http"]}]
        for doc in (expected, {"data": expected}, {"results": expected},
                    {"data": {"proxies": expected}}):
            self.assertEqual(_rows(as_bytes(doc)), expected)

    def test_malformed_json_and_shapes(self):
        for doc in (b"not json", as_bytes({"error": "failed"}), as_bytes("not list")):
            with self.subTest(doc=doc), self.assertRaises(ValueError):
                _rows(doc)


class CollectorTests(unittest.TestCase):
    def test_merge_provenance_metadata_protocols_and_invalid_rows(self):
        called = []
        hproxy = [
            {"ip": "8.8.8.8", "port": 80, "protocols": ["HTTP", "HTTPS"],
             "country": "US", "uptime_7d": 94.5, "latency_ms": 120,
             "last_checked": "2026-10-27T08:00:00Z", "status": "alive"},
            {"ip": "127.0.0.1", "port": 9999, "protocols": ["http"]},
            {"ip": "9.9.9.9", "port": 443, "protocols": ["socks5"]},
        ]

        def fetch(url, timeout, max_bytes):
            called.append(url)
            if "hproxy.com" in url:
                return as_bytes(hproxy)
            page = int(parse_qs(urlsplit(url).query)["page"][0])
            if page == 1:
                return as_bytes({"data": [
                    {"ip": "8.8.8.8", "port": "80", "protocols": ["http"],
                     "country": "US", "anonymityLevel": "elite",
                     "lastChecked": "2026-10-27T09:00:00Z"},
                    {"ip": "1.1.1.1", "port": 8080, "protocols": ["socks4"]},
                    {"ip": "8.8.8.8", "port": 80, "protocols": ["http"]},
                    {"ip": "10.0.0.9", "port": 8080, "protocols": ["http"]},
                ]})
            return as_bytes({"data": [
                {"ip": "4.2.2.2", "port": 3128, "protocols": ["http"]}
            ]})

        report = Collector(fetch=fetch, max_pages=2).collect(
            ["hproxy", "geonode", "hproxy"])
        data = report.to_dict()
        self.assertEqual(len(called), 3)  # HProxy once + two GeoNode pages.
        self.assertEqual(data["unique_endpoints"], 4)
        merged = next(p for p in data["proxies"] if p["ip"] == "8.8.8.8")
        self.assertEqual(merged["protocols"], ["http", "https"])
        self.assertEqual(merged["sources"], ["geonode", "hproxy"])
        self.assertEqual(len(merged["observations"]), 2)
        self.assertTrue(any(o["metadata"].get("uptime_7d") == 94.5
                            for o in merged["observations"]))
        self.assertTrue(any(o["metadata"].get("lastChecked") == "2026-10-27T09:00:00Z"
                            for o in merged["observations"]))
        self.assertFalse(merged["locally_verified"])
        self.assertEqual(data["verification"], "not_performed")
        self.assertEqual(report.sources["geonode"]["unique_endpoints"], 3)
        self.assertEqual(report.sources["hproxy"]["invalid_rows"], 1)

    def test_partial_failure_does_not_stop_other_source(self):
        def fetch(url, timeout, max_bytes):
            if "hproxy.com" in url:
                raise OSError("upstream unavailable")
            return as_bytes({"data": [{"ip": "8.8.8.8", "port": 80,
                                       "protocols": ["http"]}]})
        result = Collector(fetch=fetch, max_pages=1).collect(["hproxy", "geonode"])
        self.assertEqual(len(result.proxies), 1)
        self.assertEqual(len(result.sources["hproxy"]["errors"]), 1)
        self.assertEqual(result.sources["geonode"]["errors"], [])

    def test_proxycompass_opt_in_with_nested_json(self):
        requested = []

        def fetch(url, timeout, max_bytes):
            requested.append(url)
            return as_bytes({"data": {"items": [
                {"ip_address": "1.1.1.1", "port": 80, "protocol": "HTTP",
                 "country": "AU", "latency": 40},
            ]}})

        with self.assertRaises(ValueError):
            Collector(fetch=fetch).collect(["proxycompass"])
        self.assertFalse(requested)
        report = Collector(fetch=fetch, max_pages=1).collect(
            ["proxycompass"], include_experimental=True)
        self.assertEqual(len(report.proxies), 1)
        self.assertEqual(report.proxies[("1.1.1.1", 80)].protocols, {"http"})

    def test_limits_and_response_overflow(self):
        with self.assertRaises(ValueError):
            Collector(max_pages=100)
        with self.assertRaises(ValueError):
            Collector(page_size=600)
        report = Collector(
            fetch=lambda url, timeout, limit: b"x" * (limit + 1),
            max_bytes=1024,
        ).collect(["hproxy"])
        self.assertEqual(report.proxies, {})
        self.assertEqual(len(report.sources["hproxy"]["errors"]), 1)

    def test_no_redirects_beyond_configured_host(self):
        self.assertEqual(SOURCES["geonode"].page_url(2, 500).count("page="), 1)
        self.assertEqual(
            parse_qs(urlsplit(SOURCES["geonode"].page_url(2, 500)).query)["page"],
            ["2"],
        )


if __name__ == "__main__":
    unittest.main()
