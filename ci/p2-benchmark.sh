#!/usr/bin/env bash
# Offline synthetic SQLite benchmark. No real proxies or private DB.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m system_proxy.benchmark --endpoints 2000 --workers 4 --queries 32
