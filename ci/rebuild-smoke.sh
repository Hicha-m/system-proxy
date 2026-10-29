#!/usr/bin/env bash
# Offline, disposable workspace rebuild simulation. Never alters real data.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m unittest discover -s tests -p 'test_persistence.py' -v
