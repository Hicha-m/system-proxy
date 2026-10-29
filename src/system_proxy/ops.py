"""Private, read-only operational health and sanitized D1 sync receipts.

No Cloudflare requests, network calls, credentials, endpoint addresses or
untrusted exception strings in the report. Quota usage is NOT inferred.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .observability import load_observability, write_private
from .persistence import data_directory, status as storage_status
from .retention import prune_history

SYNC_STATES = frozenset(("synced", "skipped", "failed", "disabled"))
SYNC_REASONS = frozenset((
    "ok", "no_local_campaign_database", "codespace_cloudflare_secret_missing",
    "cloudflare_d1_identifiers_missing", "transport_or_schema_error",
    "codespace_sync_disabled", "owner_disabled",
))
SYNC_FILE = "d1-sync-status.json"


def record_sync(data_dir: Path, *, outcome: str, reason: str,
                now: datetime | None = None) -> dict:
    """Write only fixed safe codes; never record identifiers, tokens or URLs."""
    if outcome not in SYNC_STATES or reason not in SYNC_REASONS:
        raise ValueError("Unrecognized D1 receipt state")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("Timezone-aware clock required")
    if data_dir.is_symlink():
        raise ValueError("Refusing symlinked private storage")
    stamp = moment.astimezone(timezone.utc).isoformat()
    payload = {"status": outcome, "reason": reason, "recorded_at": stamp}
    write_private(data_dir / "ops" / SYNC_FILE,
                  json.dumps(payload, sort_keys=True) + "\n")
    return payload


def read_sync(data_dir: Path, *, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    path = data_dir / "ops" / SYNC_FILE
    if data_dir.is_symlink() or path.is_symlink():
        return {"status": "unavailable", "reason": "unsafe_receipt"}
    if not path.exists():
        return {"status": "not_recorded"}
    try:
        if path.stat().st_size > 2048 or not path.is_file():
            raise ValueError("Bad receipt")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Bad receipt")
        state, reason, stamp = (payload.get(k) for k in
                                ("status", "reason", "recorded_at"))
        date = datetime.fromisoformat(stamp)
        if (state not in SYNC_STATES or reason not in SYNC_REASONS
                or date.tzinfo is None or date.utcoffset() is None
                or date > moment + timedelta(minutes=5)):
            raise ValueError("Bad receipt")
        return {"status": state, "reason": reason,
                "recorded_at": date.astimezone(timezone.utc).isoformat()}
    except (ValueError, TypeError, OSError, json.JSONDecodeError):
        return {"status": "unavailable", "reason": "invalid_receipt"}


def health(db: Path, *, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("Timezone-aware clock required")
    if db.is_symlink() or db.parent.is_symlink():
        raise ValueError("Unsafe database or storage directory")
    storage = storage_status(db.parent)
    observation = load_observability(db, days=14, now=moment)
    sync = read_sync(db.parent, now=moment)
    alerts = set(observation["alerts"])
    db_ok = db.is_file() and observation["status"] == "ok" and storage["status"] == "ok"
    if not db_ok:
        alerts.add("sqlite_missing_or_corrupt")
    for backup in ("latest_backup", "previous_backup"):
        if storage[backup] != True:
            alerts.add(backup + "_missing_or_corrupt")
    if sync["status"] == "failed":
        alerts.add("d1_sync_failed")
    elif sync["status"] == "skipped":
        alerts.add("d1_sync_skipped")
    elif sync["status"] == "unavailable":
        alerts.add("d1_receipt_invalid")
    elif sync["status"] == "synced":
        try:
            age = moment - datetime.fromisoformat(sync["recorded_at"])
            if age > timedelta(hours=48):
                alerts.add("d1_sync_stale")
        except (ValueError, TypeError):
            alerts.add("d1_receipt_invalid")
    retention = {"status": "not_checked"}
    if db_ok:
        # Dry-run only; never touch live historical records.
        preview = prune_history(db, days=90, max_delete=1000,
                                apply=False, now=moment)
        retention = {"status": preview["status"],
                     "eligible_preview": sum(preview["eligible"].values()),
                     "limit": preview["limit"], "retention_days": preview["days"]}
    return {
        "status": "ok" if db_ok else "degraded",
        "alerts": sorted(alerts),
        "daily_runs": observation["daily_runs"][:3],
        "sqlite": {
            "status": storage["status"],
            "latest_backup": storage["latest_backup"],
            "previous_backup": storage["previous_backup"],
            "inventory_total": observation["inventory_total"],
        },
        "retention": retention,
        "d1": sync,
        "d1_quotas": "not_observed_no_cloudflare_api_request",
        "network_requests": 0,
        "privacy": "aggregate_only_no_endpoints_tokens_or_target_names",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Private operations control")
    sub = parser.add_subparsers(dest="action", required=True)
    report = sub.add_parser("report", help="Read-only operational summary")
    report.add_argument("--db", type=Path,
                        default=data_directory() / "campaign.sqlite3")
    receipt = sub.add_parser("record-sync", help="Record a sanitized D1 sync outcome")
    receipt.add_argument("--status", choices=sorted(SYNC_STATES), required=True)
    receipt.add_argument("--reason", choices=sorted(SYNC_REASONS), required=True)
    receipt.add_argument("--data-dir", type=Path, default=data_directory())
    args = parser.parse_args(argv)
    try:
        if args.action == "record-sync":
            result = record_sync(args.data_dir, outcome=args.status,
                                 reason=args.reason)
        else:
            result = health(args.db)
    except (ValueError, OSError) as exc:
        # Never leak private paths or exception text.
        print(json.dumps({"status": "error", "reason": "ops_unavailable"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
