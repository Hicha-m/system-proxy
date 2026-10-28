#!/usr/bin/env bash
# Universal entry point for the Remote Dev MCP's generic "test" action.
# Works both in the networkless /project sandbox and in GitHub Actions.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m unittest discover -s tests -v
