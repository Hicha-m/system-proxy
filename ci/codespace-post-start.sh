#!/usr/bin/env bash
# Executed by Codespaces postStartCommand, inside the Codespace user session.
# The GitHub Codespaces secret never needs to be copied into Remote Dev MCP.
set -euo pipefail
if [[ "${CODESPACES:-false}" != "true" ]]; then
  printf '{"status":"skipped","reason":"not_codespace"}\n'
  exit 0
fi
if [[ "${PROXY_D1_SYNC_ENABLED:-1}" != "1" ]]; then
  printf '{"status":"skipped","reason":"codespace_sync_disabled"}\n'
  exit 0
fi
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
# Only a local, one-off call; no daemon, no secret persistence, no cron setup.
# The existing 'ci/daily.sh' also syncs after a successful native collection.
bash "$ROOT/ci/codespace-d1-sync.sh"
