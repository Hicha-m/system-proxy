#!/usr/bin/env bash
# Launch a best-effort periodic D1 sync entirely within the Codespace.
# Secrets are only inherited by local subprocesses; never written to disk.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ "${CODESPACES:-false}" != "true" ]] || [[ "${PROXY_D1_BACKGROUND_SYNC:-1}" == "0" ]]; then
  exit 0
fi
if [[ -z "${CLOUDFLARE_API_TOKEN:-}" || -z "${CLOUDFLARE_ACCOUNT_ID:-}" || -z "${CLOUDFLARE_D1_DATABASE_ID:-}" ]]; then
  # Start harmlessly with no secret, will retry only when the Codespace restarts.
  printf 'D1 background sync not started: required Codespaces secrets unavailable.\n'
  exit 0
fi
mkdir -p "${HOME}/.local/state/system-proxy"
LOG="${HOME}/.local/state/system-proxy/d1-sync.log"
# Lock held by the actual loop, not the launcher. No secrets in arguments.
nohup bash "$ROOT/ci/codespace-d1-loop.sh" </dev/null >>"$LOG" 2>&1 &
disown || true
printf 'Codespace D1 sync loop launched (hourly while Codespace runs).\n'
