"""Opt-in local CLI for public multi-target demos and private client observations.

No HTTP requests, no target probes, no network listeners. The demonstration
uses reserved TEST-NET addresses and marks all outputs SYNTHETIC.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .history import History
from .multi_target import OUTCOMES, TargetObservation, TargetStore
from .target_profiles import load_profiles

TEST_NET = (("192.0.2.10", 8080), ("198.51.100.20", 3128),
            ("203.0.113.30", 1080))


def synthetic_demo(profiles: dict) -> dict:
    """Show reproducible ranking without contacting a real proxy or target."""
    now = datetime.now(timezone.utc)
    with History(":memory:") as history:
        store = TargetStore(history)
        for target_index, profile in enumerate(profiles.values()):
            if "http" not in profile.protocols:
                continue
            preferred = target_index % len(TEST_NET)
            for index, (ip, port) in enumerate(TEST_NET):
                history.conn.execute(
                    "INSERT OR IGNORE INTO endpoints VALUES (?,?,?)",
                    (ip, port, "http")
                )
                # The demo bypasses live-IP validation ONLY in an isolated
                # in-memory DB, using RFC 5737 documentation-only addresses.
                for trial in range(6):
                    if index == preferred:
                        outcome, status, latency = "success", 200, 70 + trial * 3
                    elif index == (preferred + 1) % len(TEST_NET):
                        outcome, status, latency = (
                            ("proxy_error", None, None) if trial in (1, 4)
                            else ("success", 200, 170 + trial * 7)
                        )
                    else:
                        outcome, status, latency = (
                            ("rate_limited", 429, 400) if trial == 5
                            else ("success", 200, 300 + trial * 10)
                        )
                    stamp = (now - timedelta(minutes=(6 - trial) * 12)).isoformat()
                    history.conn.execute("""
                        INSERT INTO target_observations
                          (project_id,target_id,ip,port,protocol,outcome,
                           observed_at,source,http_status,latency_ms,verified_https)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """, (profile.project_id, profile.target_id,
                          ip, port, "http", outcome, stamp, "client",
                          status, latency, int(outcome == "success")))
        history.conn.commit()
        demo = {}
        for key, profile in profiles.items():
            if "http" not in profile.protocols:
                continue
            demo["/".join(key)] = {
                "stats": store.stats(profile),
                "ranking": store.ranking(profile, protocol="http", now=now),
            }
        return {
            "synthetic": True,
            "network_requests": 0,
            "test_net_only": True,
            "warning": "Illustrative fixtures, NOT live proxy availability or third-party compatibility",
            "targets": demo,
        }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Networkless multi-destination reliability CLI")
    p.add_argument("--config", type=Path, required=True,
                   help="Local public example or owner-controlled private profile JSON")
    p.add_argument("--db", type=Path,
                   default=Path.home() / ".local/share/system-proxy/campaign.sqlite3")
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("profiles")
    sub.add_parser("demo")
    for action in ("ranking", "stats", "record"):
        subparser = sub.add_parser(action)
        subparser.add_argument("--project", required=True)
        subparser.add_argument("--target", required=True)
        if action in {"ranking", "record"}:
            subparser.add_argument("--protocol", choices=("http", "socks4", "socks5"),
                                   default="http")
        if action == "ranking":
            subparser.add_argument("--limit", type=int, default=20)
        if action == "record":
            subparser.add_argument("--ip", required=True)
            subparser.add_argument("--port", type=int, required=True)
            subparser.add_argument("--outcome", required=True,
                                   choices=sorted(OUTCOMES))
            subparser.add_argument("--http-status", type=int)
            subparser.add_argument("--latency-ms", type=float)
            subparser.add_argument("--source", choices=("client", "approved_probe"),
                                   default="client")
            subparser.add_argument("--verified-https", action="store_true")
            subparser.add_argument("--observed-at",
                                   default=datetime.now(timezone.utc).isoformat())

    args = p.parse_args(argv)
    profiles = load_profiles(args.config)
    if args.action == "profiles":
        out = {"count": len(profiles), "profiles": [
            {"project_id": v.project_id, "target_id": v.target_id,
             "hostname": v.hostname, "mode": v.mode,
             "protocols": sorted(v.protocols),
             "policy": {"min_successes": v.min_successes,
                        "min_score": v.min_score,
                        "max_age_hours": v.max_age_hours}}
            for v in profiles.values()
        ]}
    elif args.action == "demo":
        out = synthetic_demo(profiles)
    else:
        key = (args.project, args.target)
        if key not in profiles:
            p.error("Unknown project/target ID in the selected configuration")
        profile = profiles[key]
        if args.action == "record":
            args.db.parent.mkdir(parents=True, exist_ok=True)
        elif not args.db.is_file():
            p.error("No observed data yet; record authorized client results first")
        with History(args.db) as history:
            store = TargetStore(history)
            if args.action == "record":
                store.record(profile, TargetObservation(
                    ip=args.ip, port=args.port, protocol=args.protocol,
                    outcome=args.outcome, observed_at=args.observed_at,
                    source=args.source, http_status=args.http_status,
                    latency_ms=args.latency_ms,
                    verified_https=args.verified_https,
                ))
                out = {"status": "recorded", "project_id": profile.project_id,
                       "target_id": profile.target_id, "network_requests": 0}
            elif args.action == "ranking":
                out = store.ranking(profile, protocol=args.protocol, limit=args.limit)
            else:
                out = store.stats(profile)
    print(json.dumps(out, sort_keys=True, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
