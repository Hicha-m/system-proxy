#!/usr/bin/env bash
# Explicit opt-in network diagnostic; credentials and raw errors are never logged.
# All egress is still controlled by the MCP owner policy (not by this script).
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
exec python3 -m system_proxy.network_cli
