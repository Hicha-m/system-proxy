"""Fail-closed behavior for the Codespace -> Cloudflare D1 sync job."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class D1SyncScriptTests(unittest.TestCase):
    def run_sync(self, *, create_db: bool, secrets: dict[str, str] | None = None):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "state"
            data_dir.mkdir(mode=0o700)
            if create_db:
                db = sqlite3.connect(data_dir / "campaign.sqlite3")
                db.execute("CREATE TABLE endpoints (ip TEXT)")
                db.commit()
                db.close()

            env = os.environ.copy()
            env["SYSTEM_PROXY_DATA_DIR"] = str(data_dir)
            for key in (
                "CLOUDFLARE_ACCOUNT_ID",
                "CLOUDFLARE_D1_DATABASE_ID",
                "CLOUDFLARE_API_TOKEN",
            ):
                env.pop(key, None)
            env.update(secrets or {})

            result = subprocess.run(
                ["bash", "ci/codespace-d1-sync.sh"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            receipt_path = data_dir / "ops" / "d1-sync-status.json"
            receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
            return result, receipt

    def test_missing_local_database_fails_job_and_records_failure(self):
        result, receipt = self.run_sync(create_db=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["reason"], "no_local_campaign_database")

    def test_missing_codespace_token_fails_job_and_records_failure(self):
        result, receipt = self.run_sync(
            create_db=True,
            secrets={
                "CLOUDFLARE_ACCOUNT_ID": "0e52a0a9c70bfb137e85da4cdd8ac280",
                "CLOUDFLARE_D1_DATABASE_ID": "5319d235-6da9-4abf-ba22-c5cb5e544491",
            },
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["reason"], "codespace_cloudflare_secret_missing")

    def test_missing_database_identifiers_fail_job_and_record_failure(self):
        result, receipt = self.run_sync(
            create_db=True,
            secrets={"CLOUDFLARE_API_TOKEN": "test-placeholder-token"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["reason"], "cloudflare_d1_identifiers_missing")


if __name__ == "__main__":
    unittest.main()
