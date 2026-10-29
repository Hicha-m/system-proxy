#!/usr/bin/env bash
# Shared project-owned SQLite lifecycle. No Cloudflare secrets are accessed.
# Bash callers must set ROOT and PYTHONPATH before sourcing this file.
storage_prepare() {
  umask 077
  local data_dir
  data_dir="$(python3 -m system_proxy.persistence prepare)" || return
  export SYSTEM_PROXY_DATA_DIR="$data_dir"
}
storage_snapshot() {
  python3 -m system_proxy.persistence snapshot --data-dir "$SYSTEM_PROXY_DATA_DIR"
}
storage_snapshot_on_exit() {
  local rc="$?"
  trap - EXIT
  if [[ -f "$SYSTEM_PROXY_DATA_DIR/campaign.sqlite3" ]]; then
    if ! storage_snapshot; then
      printf '{"storage_backup":"failed"}\n' >&2
      if [[ "$rc" -eq 0 ]]; then rc=1; fi
    fi
  fi
  exit "$rc"
}
