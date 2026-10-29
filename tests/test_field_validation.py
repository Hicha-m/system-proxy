"""Offline regression tests for the owner-approved live sample (never egress)."""
import json
import sqlite3
import unittest
from datetime import datetime, timezone

from system_proxy.field_validation import (
    PROTOCOLS, PERMITTED_PORTS, safe_summary, select_sample, run,
)
from system_proxy.verification import Check, classify_probe_exception
from system_proxy.socks import SocksNegotiationError


def fake_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE endpoints (ip TEXT NOT NULL,port INTEGER NOT NULL,
                                protocol TEXT NOT NULL);
        CREATE TABLE checks (ip TEXT,port INTEGER,protocol TEXT,
                             status TEXT, reason TEXT, checked_at TEXT);
    """)
    for proto in PROTOCOLS:
        for i in range(1, 16):
            conn.execute("INSERT INTO endpoints VALUES (?,?,?)",
                         (f"8.8.{0 if proto == 'http' else 1 if proto == 'socks4' else 2}.{i}",
                          8080, proto))
        conn.execute("INSERT INTO endpoints VALUES (?,?,?)",
                     ("9.9.9.9", 9191, proto))  # MCP policy excludes this port
    return conn


class FieldValidationTests(unittest.TestCase):
    def test_balanced_deterministic_sample_and_policy(self):
        with fake_db() as db:
            a, counts = select_sample(db, per_protocol=4, seed=7)
            b, _ = select_sample(db, per_protocol=4, seed=7)
            self.assertEqual(a, b)
            self.assertTrue(all(len(a[p]) == 4 for p in PROTOCOLS))
            self.assertTrue(all(item[1] in PERMITTED_PORTS
                                for rows in a.values() for item in rows))
            self.assertTrue(all(counts[p]["inventory"] == 16 for p in PROTOCOLS))
            with self.assertRaises(ValueError):
                select_sample(db, per_protocol=13)

    def test_recently_checked_not_contacted(self):
        with fake_db() as db:
            db.execute("INSERT INTO checks VALUES (?,?,?,?,?,?)",
                       ("8.8.0.1",8080,"http","failed","timeout",
                        datetime.now(timezone.utc).isoformat()))
            selection, count = select_sample(db, per_protocol=12, seed=8)
            self.assertNotIn(("8.8.0.1",8080,"http"), selection["http"])
            self.assertEqual(count["http"]["eligible_not_recently_checked"],14)

    def test_retry_only_timeouts_and_never_leak_endpoints(self):
        calls=[]
        def checker(ip, port, proto, timeout):
            calls.append((ip, port, proto, timeout))
            if proto == "socks4" and timeout == 3:
                return Check(ip,port,proto,"failed",None,"timeout","now")
            return Check(ip,port,proto,"passed",10,"verified_https_content","now")
        with fake_db() as db:
            report=run(db, check=checker,
                       direct=lambda _: {"status":"failed","failed_stage":"tcp"},
                       per_protocol=3, seed=2)
        self.assertEqual(report["per_protocol"]["http"]["passed"],3)
        self.assertEqual(report["per_protocol"]["socks4"]["failed"],3)
        self.assertEqual(report["per_protocol"]["socks4"]["timeout_retry"]["attempted"],3)
        self.assertEqual(report["per_protocol"]["socks4"]["timeout_retry"]["passed_at_5_seconds"],3)
        self.assertEqual(len(calls),12)
        printed=json.dumps(report)
        self.assertNotIn("8.8.",printed)
        self.assertNotIn("8080",printed)
        self.assertEqual(report["persistence"],"read_only_no_history_changes")

    def test_socks_negotiation_errors_are_classified_without_raw_details(self):
        for reason in ("socks4_connect_rejected", "socks5_connect_rejected",
                       "socks5_auth_unavailable"):
            self.assertEqual(
                classify_probe_exception(SocksNegotiationError(reason, unavailable=True)),
                ("unavailable", reason))
        for reason in ("socks5_malformed_reply", "socks_early_eof"):
            self.assertEqual(
                classify_probe_exception(SocksNegotiationError(reason)),
                ("failed", reason))

    def test_safe_failure_codes(self):
        result=safe_summary([
            Check("8.8.8.8",8080,"http","failed",None,"https://secret/","now"),
            Check("8.8.8.8",8080,"http","unavailable",None,"network_policy_denied","now"),
        ])
        self.assertEqual(result["reasons"], {"network_policy_denied":1,"other":1})
        self.assertNotIn("secret",json.dumps(result))


if __name__ == "__main__":
    unittest.main()
