"""Offline lot-3 ops controls: backup integrity, private D1 receipts and dry-run."""
from __future__ import annotations

import json
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from system_proxy.history import History
from system_proxy.ops import health, read_sync, record_sync
from system_proxy.persistence import snapshot
from system_proxy.verification import Check


class OpsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "state"
        self.root.mkdir()
        self.db = self.root / "campaign.sqlite3"
        now = datetime.now(timezone.utc)
        with History(self.db) as h:
            h.import_candidates([{"ip": "8.8.8.8", "port": 8080,
                                  "protocols": ["http"], "sources": ["geonode"]}])
            h.add_checks([
                Check("8.8.8.8", 8080, "http", "failed", None,
                      "timeout", (now - timedelta(days=120)).isoformat()),
                Check("8.8.8.8", 8080, "http", "passed", 12,
                      "verified_https_content", now.isoformat()),
            ])
        self.assertEqual(snapshot(self.root), "saved")
        self.assertEqual(snapshot(self.root), "saved")

    def tearDown(self):
        self.temp.cleanup()

    def test_report_is_private_read_only_with_verified_backups(self):
        record_sync(self.root, outcome="synced", reason="ok")
        original = self.db.read_bytes()
        data = health(self.db)
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["sqlite"]["latest_backup"])
        self.assertTrue(data["sqlite"]["previous_backup"])
        self.assertEqual(data["retention"]["status"], "dry_run")
        self.assertGreaterEqual(data["retention"]["eligible_preview"], 1)
        self.assertEqual(data["d1"]["status"], "synced")
        self.assertEqual(data["network_requests"], 0)
        self.assertEqual(data["d1_quotas"], "not_observed_no_cloudflare_api_request")
        self.assertNotIn("8.8.8.8", json.dumps(data))
        self.assertEqual(original, self.db.read_bytes())

    def test_sync_receipt_skipped_failed_stale_and_permissions(self):
        now = datetime.now(timezone.utc)
        record_sync(self.root, outcome="skipped",
                    reason="codespace_cloudflare_secret_missing", now=now)
        receipt = self.root / "ops" / "d1-sync-status.json"
        self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
        self.assertIn("d1_sync_skipped", health(self.db, now=now)["alerts"])
        record_sync(self.root, outcome="failed",
                    reason="transport_or_schema_error", now=now)
        self.assertIn("d1_sync_failed", health(self.db, now=now)["alerts"])
        record_sync(self.root, outcome="synced", reason="ok",
                    now=now - timedelta(days=3))
        self.assertIn("d1_sync_stale", health(self.db, now=now)["alerts"])

    def test_receipt_rejects_unknown_values_and_unsafe_symlinks(self):
        for outcome, reason in (("token leaked", "ok"), ("synced", "secret reason")):
            with self.assertRaises(ValueError):
                record_sync(self.root, outcome=outcome, reason=reason)
        receipt = self.root / "ops" / "d1-sync-status.json"
        receipt.parent.mkdir(exist_ok=True)
        receipt.symlink_to(self.db)
        with self.assertRaises(ValueError):
            record_sync(self.root, outcome="synced", reason="ok")
        self.assertEqual(read_sync(self.root)["status"], "unavailable")
        self.assertEqual(self.db.read_bytes()[:16], b"SQLite format 3\\x00".replace(b"\\x00", b"\x00"))

    def test_missing_database_produces_degraded_state(self):
        missing = self.root / "nonexistent.sqlite3"
        report = health(missing)
        self.assertEqual(report["status"], "degraded")
        self.assertIn("sqlite_missing_or_corrupt", report["alerts"])
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
