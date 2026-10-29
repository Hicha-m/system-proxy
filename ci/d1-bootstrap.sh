#!/usr/bin/env bash
# Bounded Codespace reference bootstrap: project-owned workflow, MCP only runs CI.
# Never uses or accesses Cloudflare credentials; D1 transfer remains Codespace-owned.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
export PROXY_D1_SYNC_ENABLED=0
export PROXY_DAILY_MAX_CHECKS="${PROXY_BOOTSTRAP_MAX_CHECKS:-40}"
export PROXY_DAILY_WORKERS="${PROXY_BOOTSTRAP_WORKERS:-4}"
export PROXY_DAILY_TIMEOUT="${PROXY_BOOTSTRAP_TIMEOUT:-2}"
export PROXY_DAILY_RUNTIME="${PROXY_BOOTSTRAP_RUNTIME:-90}"
bash "$ROOT/ci/daily.sh"
bash "$ROOT/ci/d1-preview.sh"
