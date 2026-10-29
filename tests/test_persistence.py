"""P0: offline regression for Codespace rebuilds and SQLite recovery."""
from __future__ import annotations

import contextlib
import io
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from system_proxy.history import History
from system_proxy.persistence import (
    StorageError, _backup_dir, data_directory, main, prepare, snapshot, status,
)


class PersistentCodespaceStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "workspaces"
        self.workspace.mkdir()
        self.state = self.workspace / ".system-proxy-state"
        self.legacy = self.root / "home/.local/share/system-proxy/campaign.sqlite3"
        self.legacy.parent.mkdir(parents=True)
        self._make_db(self.legacy)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _make_db(path: Path):
        with History(path) as h:
            h.import_candidates([{
                "ip": "8.8.8.8", "port": 8080,
                "protocols": ["http"], "sources": ["test-source"]
            }])

    @staticmethod
    def _count(path: Path):
        with sqlite3.connect(path) as conn:
            return conn.execute("SELECT COUNT(*) FROM endpoints").fetchone()[0]

    def test_codespace_defaults_to_persistent_workspace_and_home_fallback(self):
        selected = data_directory(
            env={"CODESPACES": "true", "HOME": str(self.root / "home")},
            workspace_root=self.workspace)
        self.assertEqual(selected, self.state)
        fallback = data_directory(
            env={"HOME": str(self.root / "home")},
            workspace_root=self.root / "missing")
        self.assertEqual(fallback, self.root / "home/.local/share/system-proxy")
        with self.assertRaises(StorageError):
            data_directory(env={"CODESPACES": "true"}, workspace_root=self.root / "missing")
        override = self.root / "override"
        self.assertEqual(data_directory(
            env={"SYSTEM_PROXY_DATA_DIR": str(override)}, workspace_root=self.workspace),
            override)

    def test_legacy_migration_is_consistent_and_idempotent(self):
        result = prepare(self.state, legacy=self.legacy)
        self.assertEqual(result, "migrated")
        self.assertEqual(self._count(self.state / "campaign.sqlite3"), 1)
        self.assertTrue(self.legacy.exists())  # never remove the source
        self.assertEqual(prepare(self.state, legacy=self.legacy), "existing")
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.state / "campaign.sqlite3").stat().st_mode), 0o600)

    def test_snapshot_survives_simulated_rebuild_and_restores_missing_db(self):
        prepare(self.state, legacy=self.legacy)
        self.assertEqual(snapshot(self.state), "saved")
        backed = _backup_dir(self.state) / "latest.sqlite3"
        self.assertEqual(stat.S_IMODE(backed.stat().st_mode), 0o600)
        # Simulate rebuilt disposable HOME and a missing working SQLite file.
        self.legacy.unlink()
        (self.state / "campaign.sqlite3").unlink()
        self.assertEqual(prepare(self.state, legacy=self.legacy), "restored")
        self.assertEqual(self._count(self.state / "campaign.sqlite3"), 1)
        self.assertEqual(status(self.state)["status"], "ok")

    def test_previous_backup_is_used_when_latest_is_corrupt(self):
        prepare(self.state, legacy=self.legacy)
        snapshot(self.state)
        with sqlite3.connect(self.state / "campaign.sqlite3") as conn:
            conn.execute("INSERT INTO endpoints (ip,port,protocol) VALUES ('9.9.9.9',8080,'http')")
        snapshot(self.state)
        self.assertEqual(self._count(self.state / "campaign.sqlite3"), 2)
        backups = _backup_dir(self.state)
        (backups / "latest.sqlite3").write_bytes(b"corrupt!")
        (self.state / "campaign.sqlite3").unlink()
        self.assertEqual(prepare(self.state, legacy=self.legacy), "restored")
        self.assertEqual(self._count(self.state / "campaign.sqlite3"), 1)

    def test_corrupt_primary_is_not_replaced_automatically(self):
        prepare(self.state, legacy=self.legacy)
        snapshot(self.state)
        db = self.state / "campaign.sqlite3"
        db.write_bytes(b"corrupt!")
        with self.assertRaises(StorageError):
            prepare(self.state, legacy=self.legacy)
        self.assertEqual(db.read_bytes(), b"corrupt!")
        self.assertEqual(status(self.state)["status"], "corrupt")

    def test_no_legacy_or_backup_does_not_create_fake_history(self):
        self.legacy.unlink()
        self.assertEqual(prepare(self.state, legacy=self.legacy), "empty")
        self.assertEqual(status(self.state)["status"], "no_database")
        self.assertEqual(snapshot(self.state), "no_database")
        self.assertFalse((self.state / "campaign.sqlite3").exists())

    def test_bad_legacy_fails_without_overwriting_anything(self):
        self.legacy.write_bytes(b"not sqlite")
        with self.assertRaises(StorageError):
            prepare(self.state, legacy=self.legacy)
        self.assertFalse((self.state / "campaign.sqlite3").exists())

    def test_cli_rebuild_status_and_snapshot(self):
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            rc = main(["prepare", "--data-dir", str(self.state),
                       "--legacy", str(self.legacy)])
        self.assertEqual(rc, 0)
        self.assertEqual(stream.getvalue().strip(), str(self.state))
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(main(["snapshot", "--data-dir", str(self.state)]), 0)
        self.assertIn('"saved"', stream.getvalue())


if __name__ == "__main__":
    unittest.main()
