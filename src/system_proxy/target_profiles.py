"""Strict, networkless configuration for public/private destination profiles.

Profiles describe *where a client has actually made requests*. Loading a
profile NEVER sends traffic. Active target probing is intentionally not
supported in this module.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .destination import domain_key

ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
PROTOCOLS = frozenset({"http", "socks4", "socks5"})
POLICY_FIELDS = frozenset({"min_successes", "min_score", "max_age_hours"})


@dataclass(frozen=True)
class TargetProfile:
    project_id: str
    target_id: str
    url: str
    hostname: str
    protocols: frozenset[str]
    min_successes: int
    min_score: float
    max_age_hours: int
    mode: str = "passive"


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError(f"Invalid {label}")
    return value


def load_profiles(path: Path) -> dict[tuple[str, str], TargetProfile]:
    """Parse only the documented schema; fail closed for unsupported actions."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "description", "projects"
    } or payload["schema_version"] != 1:
        raise ValueError("Unsupported target profile schema")
    if not isinstance(payload["description"], str) or len(payload["description"]) > 500:
        raise ValueError("Invalid profile description")
    projects = payload["projects"]
    if not isinstance(projects, list) or not 1 <= len(projects) <= 30:
        raise ValueError("Invalid projects list")
    result = {}
    project_ids = set()
    for project in projects:
        if not isinstance(project, dict) or set(project) != {"id", "targets"}:
            raise ValueError("Invalid project entry")
        project_id = _identifier(project["id"], "project ID")
        if project_id in project_ids:
            raise ValueError("Duplicate project ID")
        project_ids.add(project_id)
        targets = project["targets"]
        if not isinstance(targets, list) or not 1 <= len(targets) <= 20:
            raise ValueError("Invalid targets list")
        for target in targets:
            if not isinstance(target, dict) or set(target) != {
                "id", "url", "mode", "protocols", "policy"
            }:
                raise ValueError("Invalid target entry")
            target_id = _identifier(target["id"], "target ID")
            key = project_id, target_id
            if key in result:
                raise ValueError("Duplicate target ID")
            url = target["url"]
            if not isinstance(url, str) or len(url) > 2048:
                raise ValueError("Invalid target URL")
            try:
                parsed = urlsplit(url)
                if (parsed.scheme != "https" or not parsed.hostname
                        or parsed.port not in (None, 443)
                        or parsed.username is not None
                        or parsed.password is not None
                        or parsed.query or parsed.fragment
                        or parsed.netloc.endswith(":")
                        or any(ch.isspace() for ch in url)):
                    raise ValueError("Only credential-free HTTPS URLs are allowed")
                host = domain_key(parsed.hostname)
            except (ValueError, UnicodeError) as exc:
                raise ValueError("Invalid or unsafe HTTPS target URL") from exc
            if target["mode"] != "passive":
                raise ValueError("Only passive observations are supported")
            protocols = target["protocols"]
            if (not isinstance(protocols, list) or not protocols
                    or len(protocols) != len(set(map(str, protocols)))
                    or any(not isinstance(p, str) or p not in PROTOCOLS
                           for p in protocols)):
                raise ValueError("Unsupported target protocols")
            policy = target["policy"]
            if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS:
                raise ValueError("Invalid reliability policy")
            successes = policy["min_successes"]
            score = policy["min_score"]
            age = policy["max_age_hours"]
            if (type(successes) is not int or not 2 <= successes <= 100
                    or type(score) not in (int, float) or not 0 <= score <= 100
                    or type(age) is not int or not 1 <= age <= 168):
                raise ValueError("Invalid reliability thresholds")
            result[key] = TargetProfile(
                project_id=project_id, target_id=target_id,
                url=url, hostname=host,
                protocols=frozenset(protocols),
                min_successes=successes,
                min_score=float(score),
                max_age_hours=age,
            )
    return result
