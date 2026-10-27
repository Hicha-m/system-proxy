"""CLI entrypoint for checking public HTTP proxy candidates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from .history import History
from .verification import verify_candidates


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="system-proxy-check")
    p.add_argument("--input", type=Path, default=Path("proxies.json"))
    p.add_argument("--db", type=Path, default=Path("proxy-history.sqlite3"))
    p.add_argument("--output", type=Path, default=Path("proxy-report.json"))
    p.add_argument("--max-checks", type=int, default=25)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--timeout", type=float, default=5)
    p.add_argument("--freshness-seconds", type=int, default=3600)
    p.add_argument("--report-only", action="store_true")
    args = p.parse_args(argv)
    if not (1 <= args.max_checks <= 1000 and 1 <= args.workers <= 16
            and 0 < args.timeout <= 30 and args.freshness_seconds > 0):
        p.error("Unsafe limits")
    with History(args.db) as store:
        if not args.report_only:
            # Bound untrusted JSON read; no arbitrary code, URLs, or credentials.
            if args.input.stat().st_size > 20_000_000:
                p.error("Input too large")
            data = json.loads(args.input.read_text(encoding="utf-8"))
            if data.get("schema_version") != 1 or not isinstance(data.get("proxies"), list):
                p.error("Input must be a system-proxy schema v1 report")
            if len(data["proxies"]) > 50000:
                p.error("Too many candidates")
            store.import_candidates(data["proxies"])
            targets = store.candidates("http", args.max_checks)
            checks = verify_candidates(
                targets, timeout=args.timeout, workers=args.workers,
                max_candidates=args.max_checks)
            store.add_checks(checks)
        report = store.summary(stale_after_seconds=args.freshness_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "total_endpoints": report["total_endpoints"],
        "checked_endpoints": report["checked_endpoints"],
        "recently_passing": sum(e["usable_by_recent_check"] for e in report["endpoints"]),
        "sources": report["source_stats"], "db": str(args.db),
        "report": str(args.output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
