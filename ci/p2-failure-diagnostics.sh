#!/usr/bin/env bash
# Read-only aggregate failure diagnosis. Never expose proxy IPs or target domains.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
cd "$ROOT"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$ROOT/src"
source "$ROOT/ci/storage.sh"
storage_prepare
python3 - <<'PY'
import json, os, sqlite3
from pathlib import Path
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
db=Path(os.environ["SYSTEM_PROXY_DATA_DIR"])/"campaign.sqlite3"
if not db.is_file() or db.is_symlink():
    raise SystemExit("Database absent or unsafe")
uri="file:"+quote(str(db.resolve()),safe="/")+"?mode=ro"
cutoff=(datetime.now(timezone.utc)-timedelta(days=14)).isoformat()
out={"window_days":14,"status":"ok"}
with sqlite3.connect(uri,uri=True,timeout=10) as conn:
    conn.execute("PRAGMA query_only=ON")
    tables={x[0] for x in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "checks" in tables:
        rows=conn.execute("""SELECT protocol,status,reason,COUNT(*) FROM checks
            WHERE checked_at>=? GROUP BY protocol,status,reason
            ORDER BY COUNT(*) DESC""",(cutoff,)).fetchall()
        # All reason values are untrusted; only publish recognized stable codes.
        valid_reasons=frozenset({
            "verified_https_content","unexpected_response","timeout","tls_error",
            "dns_resolution_failed","network_policy_denied","proxy_or_target_denied",
            "upstream_or_target_error","invalid_http_response","url_error_unknown",
            "proxy_connection_error","network_or_protocol_error","invalid_probe_request",
            "unexpected_checker_exception","protocol_not_implemented",
            "check_target_unavailable","connection_refused","connection_reset",
            "network_timeout","socket_error","socks_error","socks_handshake_error",
            "verified_https","success","unknown","unavailable","unexpected_content",
            "socks_auth_failed","socks_connect_failed","socks_reply_error"
        })
        valid_status=frozenset({"passed","failed","unavailable"})
        valid_protocol=frozenset({"http","https","socks4","socks5"})
        safe=Counter()
        for proto,status,reason,n in rows:
            safe[(proto if proto in valid_protocol else "other",
                  status if status in valid_status else "other",
                  reason if reason in valid_reasons else "other")]+=n
        out["checks_by_protocol_status_reason"]=[
            {"protocol":p,"status":s,"reason":r,"count":n}
            for (p,s,r),n in sorted(safe.items(),key=lambda x:(-x[1],x[0]))
        ]
    if "url_checks" in tables:
        out["url_checks_by_protocol_outcome"]=[
            {"protocol":proto if proto in ("http","https","socks4","socks5") else "other",
             "outcome":outcome if outcome in ("success","proxy_error","unknown") else "other",
             "count":n}
            for proto,outcome,n in conn.execute("""
                SELECT protocol,outcome,COUNT(*) FROM url_checks
                WHERE checked_at>=? AND target_url='https://example.com/'
                GROUP BY protocol,outcome ORDER BY COUNT(*) DESC
            """,(cutoff,))
        ]
    if "daily_runs" in tables:
        out["daily_summary"]=[{"day":day,"attempted":a,"passed":p,"failed":f}
            for day,a,p,f in conn.execute(
                "SELECT day,attempted,passed,failed FROM daily_runs ORDER BY day DESC LIMIT 7")]
print(json.dumps(out,sort_keys=True))
PY
