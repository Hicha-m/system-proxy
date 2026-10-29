#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
python3 - <<'PY'
import sqlite3
from pathlib import Path
schema = Path('database/migrations/0001_init.sql').read_text()
with sqlite3.connect(':memory:') as db:
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript(schema)
    tables = {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    required = {
        'schema_migrations','proxy_endpoints','proxy_sources',
        'reference_observations','target_observations','ranking_snapshots',
        'collection_runs','daily_statistics'
    }
    assert required.issubset(tables)
    assert db.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] == 1
print('D1 schema offline smoke passed: 8 tables, migration 0001, no network')
PY
