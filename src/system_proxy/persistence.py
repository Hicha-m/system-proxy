"""Rebuild-safe, private SQLite state for system-proxy.

In Codespaces the project source checkout is disposable; /workspaces persists
across container rebuilds. No Cloudflare credentials or data leave the machine.
A deleted Codespace is NOT covered; off-site backups are a separate decision.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path


class StorageError(RuntimeError):
    """Invalid or unsafe local SQLite state; never silently overwrite."""


def data_directory(*, env=None, workspace_root: Path = Path("/workspaces")) -> Path:
    env = os.environ if env is None else env
    if env.get("SYSTEM_PROXY_DATA_DIR"):
        return Path(env["SYSTEM_PROXY_DATA_DIR"]).expanduser().resolve()
    if workspace_root.is_dir():
        return (workspace_root / ".system-proxy-state").resolve()
    if env.get("CODESPACES", "").lower() == "true":
        raise StorageError("Codespace workspace volume is missing; refusing ephemeral state")
    return (Path(env.get("HOME", str(Path.home()))) /
            ".local/share/system-proxy").resolve()


def _private_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise StorageError("Refusing a symlinked storage directory")
    path.chmod(0o700)


def _backup_dir(data_dir: Path) -> Path:
    # Outside the state folder and outside the Git checkout.
    return data_dir.parent / ("." + data_dir.name.lstrip(".") + "-backups")


@contextmanager
def _locked(data_dir: Path):
    _private_dir(data_dir)
    with (data_dir / ".storage.lock").open("a+b") as lock:
        os.chmod(data_dir / ".storage.lock", 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _check(db: Path) -> None:
    if not db.is_file() or db.is_symlink():
        raise StorageError("SQLite database missing or unsafe")
    try:
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=15) as conn:
            if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise StorageError("SQLite integrity check failed")
            if not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='endpoints'"
            ).fetchone():
                raise StorageError("Not a system-proxy campaign database")
    except sqlite3.DatabaseError as exc:
        raise StorageError("SQLite integrity check failed") from exc


def _copy_consistent(source: Path, target: Path):
    """SQLite backup API handles WAL and active readers/writers correctly."""
    _check(source)
    _private_dir(target.parent)
    fd, name = tempfile.mkstemp(prefix=".system-proxy-", suffix=".sqlite3", dir=target.parent)
    os.close(fd)
    tmp = Path(name)
    try:
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=15) as src:
            with sqlite3.connect(tmp, timeout=15) as dst:
                src.backup(dst, pages=256, sleep=0.1)
        _check(tmp)
        tmp.chmod(0o600)
        with tmp.open("rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def prepare(data_dir: Path, *, legacy: Path | None = None) -> str:
    """Idempotent startup: use existing DB, restore snapshot or migrate legacy."""
    data_dir = data_dir.resolve()
    db = data_dir / "campaign.sqlite3"
    with _locked(data_dir):
        if db.exists() or db.is_symlink():
            _check(db)  # fail closed on corruption; never silently replace
            db.chmod(0o600)
            return "existing"
        backups = _backup_dir(data_dir)
        for candidate in (backups / "latest.sqlite3", backups / "previous.sqlite3"):
            if candidate.is_file():
                try:
                    _copy_consistent(candidate, db)
                    return "restored"
                except StorageError:
                    continue
        if legacy and legacy.resolve() != db and legacy.is_file():
            _copy_consistent(legacy, db)
            return "migrated"
        return "empty"


def snapshot(data_dir: Path) -> str:
    """Keep two verified generations; never overwrite the only valid copy."""
    data_dir = data_dir.resolve()
    db = data_dir / "campaign.sqlite3"
    with _locked(data_dir):
        if not db.is_file():
            return "no_database"
        _check(db)
        backups = _backup_dir(data_dir)
        _private_dir(backups)
        staging = backups / ".incoming.sqlite3"
        # Remove abandoned staging output from a killed previous process.
        staging.unlink(missing_ok=True)
        _copy_consistent(db, staging)
        latest = backups / "latest.sqlite3"
        previous = backups / "previous.sqlite3"
        if latest.exists():
            try:
                _check(latest)
            except StorageError:
                # Do not replace a good previous with a corrupt latest.
                latest.unlink()
            else:
                os.replace(latest, previous)
        os.replace(staging, latest)
        return "saved"


def status(data_dir: Path) -> dict:
    data_dir = data_dir.resolve()
    db = data_dir / "campaign.sqlite3"
    state = {"database": str(db), "status": "no_database",
             "latest_backup": False, "previous_backup": False}
    for kind, filename in (("latest_backup", "latest.sqlite3"),
                           ("previous_backup", "previous.sqlite3")):
        f = _backup_dir(data_dir) / filename
        if f.is_file():
            try:
                _check(f)
                state[kind] = True
            except StorageError:
                state[kind] = "corrupt"
    if db.exists():
        try:
            _check(db)
            with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
                state["endpoints"] = conn.execute("SELECT COUNT(*) FROM endpoints").fetchone()[0]
            state["status"] = "ok"
        except StorageError:
            state["status"] = "corrupt"
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description="Codespace persistent SQLite lifecycle")
    parser.add_argument("action", choices=("prepare", "snapshot", "status"))
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--legacy", type=Path)
    args = parser.parse_args(argv)
    root = (args.data_dir or data_directory()).resolve()
    try:
        if args.action == "prepare":
            legacy = args.legacy or (Path.home() / ".local/share/system-proxy/campaign.sqlite3")
            result = prepare(root, legacy=legacy)
            print(str(root))  # For scripts; no credentials or private data.
            print(json.dumps({"storage": result}), file=__import__("sys").stderr)
        elif args.action == "snapshot":
            print(json.dumps({"storage_backup": snapshot(root)}))
        else:
            print(json.dumps(status(root), sort_keys=True))
        return 0
    except (StorageError, OSError, sqlite3.DatabaseError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}),
              file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
