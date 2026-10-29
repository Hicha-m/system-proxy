"""Time-aware reliability and cautious outcome diagnostics (no network access).

The weighted Wilson bound keeps older successes from dominating a proxy's
current quality. Inconclusive outcomes and destination restrictions are not
evidence of a broken proxy. Raw history remains available for audit.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable

SECONDS_PER_DAY = 86400
DEFAULT_HALF_LIFE_DAYS = 7
DEFAULT_WINDOW_DAYS = 30

DIAGNOSES = {
    "success": "verified_success",
    "passed": "verified_success",
    "proxy_error": "attributed_proxy_failure",
    "failed": "attributed_proxy_failure",
    "target_denied": "destination_refusal_not_proxy_failure",
    "rate_limited": "destination_rate_limit_not_proxy_failure",
    "challenge": "destination_challenge_not_proxy_failure",
    "network_error": "ambiguous_network_error",
    "unknown": "inconclusive",
    "unavailable": "inconclusive",
}


def diagnose(outcome: str | None) -> str:
    """An HTTP refusal alone cannot prove IP banning or proxy failure."""
    return DIAGNOSES.get(outcome, "no_observation")


def temporal_wilson(
    observations: Iterable[tuple[str, str]],
    *,
    now: datetime | None = None,
    positive: frozenset[str] = frozenset({"success", "passed"}),
    negative: frozenset[str] = frozenset({"proxy_error", "failed"}),
    half_life_days: int = DEFAULT_HALF_LIFE_DAYS,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> float | None:
    """Wilson 95% lower bound for exponentially discounted evidence.

    Outside-window observations, future/invalid timestamps, rate limits,
    upstream denials and inconclusive errors contribute zero evidence.
    The effective sample size is the total weight: old evidence also becomes
    less convincing, rather than maintaining the certainty of a recent test.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("An aware clock is required")
    if not (1 <= half_life_days <= 365 and half_life_days <= window_days <= 3650):
        raise ValueError("Invalid scoring window")
    good = bad = 0.0
    for outcome, stamp in observations:
        if outcome not in positive and outcome not in negative:
            continue
        try:
            recorded = datetime.fromisoformat(stamp)
            if recorded.tzinfo is None or recorded.utcoffset() is None:
                continue
            age = (current - recorded).total_seconds()
        except (TypeError, ValueError, OverflowError):
            continue
        if not 0 <= age <= window_days * SECONDS_PER_DAY:
            continue
        weight = math.exp2(-age / (half_life_days * SECONDS_PER_DAY))
        if outcome in positive:
            good += weight
        else:
            bad += weight
    n = good + bad
    if n <= 0:
        return None
    z = 1.96
    p = good / n
    base = p + z * z / (2 * n)
    delta = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(100 * max(0.0, (base - delta) / (1 + z * z / n)), 1)
