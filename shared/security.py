"""Movement advisory rule (Projects 15, 16, 17).

Documented rule, version 1:
  * Only verified incidents count. Unverified and discounted reports are ignored.
  * Look back 7 days from now, by occurred_at.
  * Score = sum of severity (1-5), with incidents in the last 48 hours weighted 1.0
    and older ones weighted 0.5.
  * Level 3 "No movement" if score >= 12; level 2 "Restricted" if >= 6;
    level 1 "Caution" if >= 3; otherwise level 0 "Normal".
  * The duty officer confirms or overrides the suggestion; an override needs a reason.
  * Areas whose current advisory is level 2 or higher block convoy stops (Project 16).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

from shared import clock

RULE_VERSION = 1
LOOKBACK_DAYS = 7
RECENT_HOURS = 48
THRESHOLDS: tuple[tuple[float, int], ...] = ((12.0, 3), (6.0, 2), (3.0, 1))
BLOCKING_LEVEL = 2

LEVEL_NAMES: dict[int, str] = {0: "Normal", 1: "Caution", 2: "Restricted", 3: "No movement"}


def incident_weight(occurred_at: datetime, now: datetime) -> float:
    age = now - occurred_at
    if age < timedelta(0) or age > timedelta(days=LOOKBACK_DAYS):
        return 0.0
    return 1.0 if age <= timedelta(hours=RECENT_HOURS) else 0.5


def advisory_score(incidents: Iterable[Mapping[str, Any]], now: datetime | None = None) -> float:
    """Weighted severity of verified incidents in the look-back window."""
    now = now or clock.now()
    total = 0.0
    for inc in incidents:
        if inc["verification_status"] != "verified":
            continue
        occurred = inc["occurred_at"]
        occurred_dt = occurred if isinstance(occurred, datetime) else datetime.fromisoformat(str(occurred))
        total += incident_weight(occurred_dt, now) * float(inc["severity"])
    return round(total, 2)


def level_for_score(score: float) -> int:
    for threshold, level in THRESHOLDS:
        if score >= threshold:
            return level
    return 0


def suggest_level(incidents: Iterable[Mapping[str, Any]], now: datetime | None = None) -> int:
    return level_for_score(advisory_score(incidents, now))


def current_advisories(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """area_id -> current advisory row (newest active decision)."""
    return {r["area_id"]: dict(r) for r in conn.execute("SELECT * FROM v_current_advisory")}


def blocked_areas(conn: sqlite3.Connection) -> set[str]:
    """Areas with an active advisory at or above the blocking level."""
    return {a for a, row in current_advisories(conn).items() if row["final_level"] >= BLOCKING_LEVEL}
