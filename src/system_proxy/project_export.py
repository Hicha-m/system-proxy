"""Read-only, conservative proxy exports for separately configured consumers.

Only proxies checked against the fixed reference HTTPS URL can be exported.
An exported candidate is *not* certified to work for a different domain.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .campaign import TARGET
from .destination import wilson_lower

DEFAULT_PROJECTS = {
    "projects": [
        {"name": "reference-http", "protocol": "http", "target_url": TARGET,
         "min_successes": 2, "min_score": 30, "max_age_hours": 24, "limit": 250},
        {"name": "reference-socks4", "protocol": "socks4", "target_url": TARGET,
         "min_successes": 2, "min_score": 30, "max_age_hours": 24, "limit": 250},
        {"name": "reference-socks5", "protocol": "socks5", "target_url": TARGET,
         "min_successes": 2, "min_score": 30, "max_age_hours": 24, "limit": 250},
    ]
}


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        os.chmod(tmp, 0o600)
        handle.write(content)
    os.replace(tmp, path)


def export_projects(conn: sqlite3.Connection, output_dir: Path, *,
                    profiles: Path | None = None,
                    now: datetime | None = None) -> dict[str, int]:
    config = json.loads(profiles.read_text(encoding="utf-8")) if profiles else DEFAULT_PROJECTS
    if not isinstance(config, dict) or not isinstance(config.get("projects"), list):
        raise ValueError("Invalid project profiles")
    if len(config["projects"]) > 20:
        raise ValueError("Too many project profiles")
    now = now or datetime.now(timezone.utc)
    result = {}
    for project in config["projects"]:
        if not isinstance(project, dict):
            raise ValueError("Invalid profile")
        name = project.get("name")
        protocol = project.get("protocol")
        target = project.get("target_url")
        min_successes = project.get("min_successes", 2)
        min_score = project.get("min_score", 30)
        max_age_hours = project.get("max_age_hours", 24)
        limit = project.get("limit", 250)
        score_window_hours = project.get("score_window_hours", 168)
        min_observations = project.get("min_observations", 2)
        if (not isinstance(name, str) or
            not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,48}", name) or
            name in result or protocol not in {"http", "socks4", "socks5"} or
            target != TARGET or
            type(min_successes) is not int or not 2 <= min_successes <= 100 or
            type(min_score) not in {int, float} or not 0 <= min_score <= 100 or
            type(max_age_hours) is not int or not 1 <= max_age_hours <= 168 or
            type(limit) is not int or not 1 <= limit <= 1000 or
            type(score_window_hours) is not int or not 1 <= score_window_hours <= 720 or
            type(min_observations) is not int or not 2 <= min_observations <= 100):
            raise ValueError("Invalid or unsupported project profile")
        candidates = []
        since = (now - timedelta(hours=score_window_hours)).isoformat()
        for ip, port, successes, failures, last_status, last_checked, latency in conn.execute("""
            SELECT c.ip,c.port,
                   SUM(CASE WHEN c.outcome='success' THEN 1 ELSE 0 END) good,
                   SUM(CASE WHEN c.outcome='proxy_error' THEN 1 ELSE 0 END) bad,
                   (SELECT recent.outcome FROM url_checks recent
                    WHERE recent.ip=c.ip AND recent.port=c.port
                      AND recent.protocol=c.protocol AND recent.target_url=c.target_url
                    ORDER BY recent.checked_at DESC,recent.rowid DESC LIMIT 1) last_status,
                   MAX(c.checked_at) last_checked,
                   MIN(CASE WHEN c.outcome='success' THEN c.latency_ms END) latency
            FROM url_checks c
            WHERE c.target_url=? AND c.protocol=? AND c.checked_at>=? AND c.checked_at<=?
            GROUP BY c.ip,c.port
        """, (TARGET, protocol, since, now.isoformat())):
            if (last_status != "success" or successes < min_successes
                    or successes + failures < min_observations):
                continue
            try:
                age = (now - datetime.fromisoformat(last_checked)).total_seconds()
            except (ValueError, TypeError):
                continue
            if age < 0 or age > max_age_hours * 3600:
                continue
            score = wilson_lower(successes, failures)
            if score is None or score < min_score:
                continue
            candidates.append({
                "ip": ip, "port": port, "protocol": protocol,
                "score": score, "successes": successes, "failures": failures,
                "last_checked": last_checked, "latency_ms": latency,
                "score_window_hours": score_window_hours,
                "observations": successes + failures,
            })
        candidates.sort(key=lambda x: (-x["score"], -x["successes"],
                                      x["latency_ms"] if x["latency_ms"] is not None else float("inf")))
        selected = candidates[:limit]
        folder = output_dir / name
        document = {
            "project": name, "reference_target": TARGET,
            "generated_at": now.isoformat(),
            "warning": "Reference-target verification only. Other destinations must be validated separately.",
            "policy": {"min_successes": min_successes, "min_score": min_score,
                       "max_age_hours": max_age_hours,
                       "score_window_hours": score_window_hours,
                       "min_observations": min_observations},
            "proxies": selected,
        }
        _atomic_write(folder / "proxies.json", json.dumps(document, indent=2, sort_keys=True) + "\n")
        _atomic_write(folder / "proxies.txt", "".join(
            f"{protocol}://{p['ip']}:{p['port']}\n" for p in selected
        ))
        result[name] = len(selected)
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Export recent and repeatedly verified proxies")
    p.add_argument("--db", type=Path, default=Path.home() / ".local/share/system-proxy/campaign.sqlite3")
    p.add_argument("--output-dir", type=Path)
    p.add_argument("--profiles", type=Path)
    a = p.parse_args(argv)
    if not a.db.is_file():
        p.error("No existing SQLite database; run the campaign first")
    with sqlite3.connect(a.db) as conn:
        if not conn.execute("SELECT name FROM sqlite_master WHERE name='url_checks'").fetchone():
            p.error("No campaign results yet")
        out = export_projects(conn, a.output_dir or a.db.parent / "exports",
                              profiles=a.profiles)
    print(json.dumps({"exports": out}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
