"""Record authorized application outcomes and inspect domain-aware health.

No network activity: this CLI only reads and writes the local SQLite database.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .destination import DestinationHealth, Observation, OUTCOMES, classify_status
from .history import History


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="system-proxy-domain")
    parser.add_argument("--db", type=Path, default=Path("proxy-history.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record", help="Record one result from your own authorized request")
    record.add_argument("--ip", required=True)
    record.add_argument("--port", type=int, required=True)
    record.add_argument("--protocol", default="http", choices=["http","https","socks4","socks5"])
    record.add_argument("--domain", required=True, help="Hostname only, not a URL")
    record.add_argument("--http-status", type=int)
    record.add_argument("--outcome", choices=sorted(OUTCOMES),
                        help="Explicit classification overrides status-only classification")
    record.add_argument("--latency-ms", type=float)
    report = sub.add_parser("report", help="No network access")
    report.add_argument("--domain", required=True)
    report.add_argument("--protocol", default="http", choices=["http","https","socks4","socks5"])
    report.add_argument("--freshness-seconds", type=int, default=3600)
    report.add_argument("--limit", type=int, default=20)
    report.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    try:
        with History(args.db) as h:
            health = DestinationHealth(h)
            if args.command == "record":
                outcome = args.outcome or classify_status(args.http_status)
                health.record(Observation(
                    ip=args.ip,port=args.port,protocol=args.protocol,domain=args.domain,
                    outcome=outcome,http_status=args.http_status,
                    latency_ms=args.latency_ms,
                    observed_at=datetime.now(timezone.utc).isoformat(),
                ))
                print(json.dumps({"recorded":True,"outcome":outcome}))
            else:
                result = health.report(args.domain, protocol=args.protocol,
                                       freshness_seconds=args.freshness_seconds,
                                       limit=args.limit)
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(json.dumps(result,indent=2)+"\n",encoding="utf-8")
                print(json.dumps(result,indent=2))
        return 0
    except (ValueError,OverflowError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
