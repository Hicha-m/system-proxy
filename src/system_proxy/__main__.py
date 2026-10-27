"""CLI for the first public-source collection slice (no proxy forwarding yet)."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from .collect import Collector, SOURCES


def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".system-proxy-", suffix=".tmp", delete=False,
        ) as file:
            temp = file.name
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temp, path)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="system-proxy",
        description="Collect public proxy candidates once per source, with provenance. "
                    "Does not verify or forward traffic.",
    )
    parser.add_argument("--list-sources", action="store_true", help="List canonical sources")
    parser.add_argument("--source", action="append", choices=sorted(SOURCES),
                        help="Sources to collect (repeatable). Defaults: hproxy, geonode")
    parser.add_argument("--include-experimental", action="store_true",
                        help="Explicitly opt into not-yet-validated APIs")
    parser.add_argument("--max-pages", type=int, default=2, help="Maximum pages per paginated source (1-20)")
    parser.add_argument("--page-size", type=int, default=500, help="Results per page (1-500)")
    parser.add_argument("--timeout", type=float, default=12.0, help="Request timeout in seconds")
    parser.add_argument("--output", type=Path, default=Path("proxies.json"),
                        help="Destination JSON (default: proxies.json)")
    args = parser.parse_args(argv)
    if args.list_sources:
        print(json.dumps([
            {"id": s.id, "format": s.format, "url": s.url,
             "referenced_by": list(s.referenced_by), "experimental": s.experimental}
            for s in SOURCES.values()
        ], indent=2))
        return 0
    try:
        collector = Collector(timeout=args.timeout, max_pages=args.max_pages,
                              page_size=args.page_size)
        report = collector.collect(
            args.source or ["hproxy", "geonode"],
            include_experimental=args.include_experimental,
        )
    except ValueError as exc:
        parser.error(str(exc))
    data = report.to_dict()
    _atomic_json(args.output, data)
    print(json.dumps({
        "output": str(args.output),
        "unique_endpoints": data["unique_endpoints"],
        "source_stats": data["source_stats"],
        "locally_verified": False,
    }, indent=2))
    # Partial failure is visible in source_stats; exit nonzero if all sources failed.
    return 1 if report.sources and all(s["errors"] for s in report.sources.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
