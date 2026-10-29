# Phase 1 — public multi-destination reliability demonstration

This phase is **public code and public example profiles**, with a real,
networkless per-target observation store and rankings. It does **not** send
active proxy traffic to ChatGPT, Gemini, Google, or any other target.

## What is included

- \`config/examples/public-targets.json\`: passive example profiles for
  \`chatgpt.com\`, \`gemini.google.com/app\`, \`www.google.com\`, and the reference
  \`example.com\` target. These are illustrations, **not test permissions**.
- \`target_profiles.py\`: strict target profile parser (HTTPS only, no credentials,
  query strings, private/loopback IPs, or unsupported active mode).
- \`multi_target.py\`: SQLite history and ranking by \`project_id\` +
  \`target_id\` + protocol, without any network requests.
- \`target_cli.py\`: offline demonstration, profile listing, recording results
  of *already performed and authorized* requests, ranking and statistics.
- Automated offline tests covering independent histories, stale results,
  403/429 interpretation, minimum sample count, protocol isolation and unsafe
  configuration rejection.

**This is not yet the production Cloudflare D1/VPS/API deployment.**
The current storage backend is the existing SQLite history, and no HTTP API
server or automatic third-party target probing is introduced.

## Try the complete public demonstration (offline)

From the repository root:

~~~bash
python -m pip install -e .
system-proxy-targets --config config/examples/public-targets.json profiles
system-proxy-targets --config config/examples/public-targets.json demo
~~~

The demo uses only **RFC 5737 TEST-NET addresses** (192.0.2.0/24,
198.51.100.0/24 and 203.0.113.0/24). All results are **synthetic**.
The JSON output explicitly contains \`synthetic: true\` and
\`network_requests: 0\`. It demonstrates how the same proxy can have
different destination rankings without making any real requests.

## Record genuine observations (no request is made here)

A client application must first make an **authorized HTTPS request** through
a proxy and verify its TLS session and expected response. It can then report
the actual outcome using the CLI. The CLI itself never connects to the
destination or proxy.

~~~bash
system-proxy-targets --config config/examples/public-targets.json \
  --db "$HOME/.local/share/system-proxy/campaign.sqlite3" \
  record --project public-web-demo --target google \
  --ip PROXY_PUBLIC_IP --port 8080 --protocol http \
  --outcome success --http-status 200 --verified-https \
  --latency-ms 150
~~~

Replace \`PROXY_PUBLIC_IP\` with an actual public proxy IP **only after** a
successful authorized request. Fabricating a positive observation damages
the ranking. The CLI enforces \`--verified-https\` and a 2xx/3xx response
for a success. For explicit destination restrictions report:

~~~bash
system-proxy-targets --config config/examples/public-targets.json \
  --db "$HOME/.local/share/system-proxy/campaign.sqlite3" \
  record --project public-web-demo --target google \
  --ip PROXY_PUBLIC_IP --port 8080 --protocol http \
  --outcome rate_limited --http-status 429
~~~

**A 403/429 or challenge is not counted as proof of proxy failure.** It is
also **not an eligible success**, and is not a reason to evade a site's access
restrictions.

Then inspect target-specific reports with no network access:

~~~bash
system-proxy-targets --config config/examples/public-targets.json \
  ranking --project public-web-demo --target google --protocol http

system-proxy-targets --config config/examples/public-targets.json \
  stats --project public-web-demo --target google
~~~

Ranked entries are considered eligible only when the last observation
is a verified success, the last observation is recent enough, the required
number of successful observations is reached, and the Wilson 95% lower-bound
score meets the profile threshold.

The ranking is **destination-specific**: a success on \`example.com\` does not
make a proxy eligible for \`chatgpt.com\` or \`google.com\`.

## The same open source code can power private installations

Copy the public example to a **file outside the repository**, e.g.
\`/etc/system-proxy/targets.json\`, and substitute *your own* permitted
targets and opaque project IDs. Launch the same CLI with
\`--config /etc/system-proxy/targets.json\` and a private SQLite path.

There is **no second private codebase**. The project IDs and real endpoints
must never be embedded in public code, GitHub Actions logs or artifacts.
When an authenticated API is implemented in a later phase, it will read
the same policy/observations and protect both target discovery and rankings.

\`.gitignore\` excludes common local private profile paths as an additional
guard, but **ignoring files is not a security boundary**. Never commit
credentials, private production databases, or confidential observations.

## Next phases (not implemented here)

1. Owner-approved active-probe adapters, explicit host allowlists, safe
   DNS/redirect handling, per-target limits and operator authorization.
2. Move catalog/history/aggregate scores to Cloudflare D1; have a VPS
   perform authorized network verification jobs.
3. Authenticated per-project API, access controls and production profile
   management, with no real private targets in the public repository.
4. Bounded retention, backups and restore tests on an independent machine.

Note: the pre-existing daily reference-check campaign still checks only
\`https://example.com/\`. It does not automatically use these passive profiles.
