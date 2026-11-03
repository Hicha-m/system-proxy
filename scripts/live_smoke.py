"""Explicit opt-in, bounded real network smoke test.

Only contacts the existing canonical public sources and example.com HTTPS.
Runs on GitHub-hosted runner on a dedicated branch; not on normal CI.
No secrets or proxy credentials. Do not rely on live public proxies for CI.
"""
from __future__ import annotations

import json
import socket
import ssl
import tempfile
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import ProxyHandler, HTTPSHandler, Request, build_opener

from system_proxy.collect import Collector
from system_proxy.history import History
from system_proxy.verification import Check, direct_baseline, verify_candidates
from system_proxy.gateway import Gateway

TIMEOUT = 5.0


def gateway_probe(ip: str, port: int, protocol: str, db_path: Path) -> dict:
    """Start a real local listener and tunnel via the chosen *verified* proxy."""
    server = Gateway(
        port=0, db_path=db_path, allowed_domains={"example.com"},
        protocol=protocol, freshness_seconds=600, min_score=0,
        token=None, timeout=5, max_tunnel_seconds=15, max_attempts=3,
        cooldown_seconds=60,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.server_address, timeout=10) as local:
            local.settimeout(10)
            local.sendall(
                b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n"
            )
            headers = bytearray()
            while b"\r\n\r\n" not in headers and len(headers) < 8192:
                b = local.recv(1)
                if not b:
                    break
                headers += b
            status_line = bytes(headers).partition(b"\r\n")[0].decode("ascii", "replace")
            if b" 200 " not in status_line.encode():
                return {"gateway_status": status_line, "tls_ok": False}
            with ssl.create_default_context().wrap_socket(local, server_hostname="example.com") as tls:
                tls.settimeout(10)
                tls.sendall(b"GET / HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n")
                result = bytearray()
                while len(result) < 100_000:
                    block = tls.recv(8192)
                    if not block:
                        break
                    result.extend(block)
            return {
                "gateway_status": status_line,
                "tls_ok": b"Example Domain" in result
                and bytes(result).startswith(b"HTTP/1."),
            }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def main() -> None:
    print("LIVE_SMOKE_START", datetime.now(timezone.utc).isoformat(), flush=True)
    baseline = direct_baseline(TIMEOUT)
    print("BASELINE_EXAMPLE_COM", baseline, flush=True)

    report = Collector(max_pages=1, page_size=100, timeout=TIMEOUT,
                       max_bytes=8_000_000, max_rows=50_000).collect(
                           ("hproxy", "geonode"))
    for source, stats in report.sources.items():
        print("SOURCE", source, json.dumps(stats, sort_keys=True), flush=True)
    print("UNIQUE_CANDIDATES", len(report.proxies), flush=True)

    # Deterministic small sample. No concurrent probing (polite + cheap).
    candidates = report.to_dict()["proxies"]
    with tempfile.TemporaryDirectory() as temp:
        db_path = Path(temp) / "history.sqlite3"
        with History(db_path) as history:
            history.import_candidates(candidates)
            checked = []
            for protocol, limit in (("http", 8), ("socks4", 3), ("socks5", 3)):
                options = history.candidates(protocol, limit)
                print("CANDIDATES_FOR", protocol, len(options), flush=True)
                if baseline and options:
                    results = verify_candidates(
                        options, max_candidates=limit, timeout=TIMEOUT,
                        workers=2, baseline=False)
                else:
                    results = []
                history.add_checks(results)
                checked.extend(results)
                print("CHECK_RESULTS", protocol,
                      dict(Counter(c.status for c in results)), flush=True)
                for c in results:
                    print("CHECK_ITEM", c.protocol, c.status, c.reason,
                          c.latency_ms, flush=True)

        # Choose a successful tested proxy, if one exists. No unverified fallback.
        passing = [c for c in checked if c.status == "passed"]
        if passing:
            chosen = passing[0]
            print("GATEWAY_TEST_UPSTREAM", chosen.protocol, flush=True)
            try:
                outcome = gateway_probe(chosen.ip, chosen.port, chosen.protocol, db_path)
                print("GATEWAY_END_TO_END", json.dumps(outcome), flush=True)
            except Exception as exc:
                print("GATEWAY_END_TO_END_ERROR", type(exc).__name__, flush=True)
        else:
            print("GATEWAY_SKIPPED", "No locally verified live proxy in small sample", flush=True)
        with History(db_path) as history:
            summary = history.summary()
            print("HISTORY_VERIFIED", summary["checked_endpoints"],
                  "HISTORY_CANDIDATES", summary["total_endpoints"], flush=True)
            if passing:
                from system_proxy.gateway_history import GatewayAttempts
                with_history = GatewayAttempts(history).summary("example.com", protocol=passing[0].protocol)
                print("GATEWAY_AUDIT", json.dumps({
                    "total":with_history["total"],
                    "connected":with_history["connected"],
                    "errors":with_history["connect_error"],
                    "denied":with_history["denied"],
                }), flush=True)
    print("LIVE_SMOKE_END", datetime.now(timezone.utc).isoformat(), flush=True)


if __name__ == "__main__":
    main()
