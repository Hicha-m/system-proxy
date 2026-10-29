#!/usr/bin/env bash
# Explicit opt-in project validation: read-only DB, public proxies, fixed HTTPS target.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m system_proxy.field_validation
