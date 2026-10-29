#!/usr/bin/env bash
# Non-network diagnostic: only reports boolean presence, never secret contents.
set -euo pipefail
export SYSTEM_PROXY_TEST_SECRET="dummy-test-value"
python3 - <<'PY'
import json
import os
import subprocess
import sys
names = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_D1_DATABASE_ID")
child_code = """
import json, os
names = ('CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_D1_DATABASE_ID')
print(json.dumps({'dummy_received': os.environ.get('SYSTEM_PROXY_TEST_SECRET') == 'dummy-test-value',
                  'cloudflare_present': {name: bool(os.environ.get(name)) for name in names}}))
"""
child = subprocess.run([sys.executable, "-c", child_code], check=True, capture_output=True, text=True)
print(json.dumps({"dummy_parent": os.environ.get("SYSTEM_PROXY_TEST_SECRET") == "dummy-test-value",
                  "cloudflare_parent_present": {name: bool(os.environ.get(name)) for name in names},
                  "child": json.loads(child.stdout)}, sort_keys=True))
PY
