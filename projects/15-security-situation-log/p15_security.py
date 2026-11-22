"""Security situation log: incidents, area status board and advisory decisions (Project 15).

The advisory rule itself lives in shared.security and is not redefined here.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from shared import clock, confirm, db, security
from shared.seed_content import INCIDENT_TYPES

CHANNEL = "app:15"
INCIDENT_TYPE_LIST: tuple[str, ...] = tuple(INCIDENT_TYPES)
SOURCES: tuple[str, ...] = ("field_team", "community_contact", "media", "hotline", "other_agency")
STATUSES: tuple[str, ...] = ("unverified", "verified", "discounted")
DUTY_ROLES: tuple[str, ...] = ("duty_officer",)


class IncidentError(ValueError):
    """An incident could not be logged or changed."""


class OverrideReasonRequired(ValueError):
    """An override was attempted without a written reason."""


def _fmt(value: datetime | str) -> str:
    return clock.iso(value) if isinstance(value, datetime) else clock.iso(clock.parse(str(value)))


def log_incident(
    conn: sqlite3.Connection,
    *,
    area_id: str,
    incident_type: str,
    severity: int,
    source: str,
    occurred_at: datetime | str,
    description: str,
    reported_by: str,
) -> str:
    """Log a new incident as unverified. Returns the incident ID."""
    if incident_type not in INCIDENT_TYPE_LIST:
        raise IncidentError(f"Unknown incident type: {incident_type}")
    if source not in SOURCES:
        raise IncidentError(f"Unknown source: {source}")
    if not 1 <= int(severity) <= 5:
        raise IncidentError("Severity must be between 1 and 5.")
    if not (description or "").strip():
        raise IncidentError("A description is required.")
    if conn.execute("SELECT 1 FROM areas WHERE area_id = ?", (area_id,)).fetchone() is None:
        raise IncidentError(f"Unknown area: {area_id}")
    if conn.execute("SELECT 1 FROM staff WHERE staff_id = ? AND active = 1", (reported_by,)).fetchone() is None:
        raise IncidentError(f"Unknown or inactive staff ID: {reported_by}")
    occurred = _fmt(occurred_at)
    if clock.parse(occurred) > clock.now():
        raise IncidentError("The incident cannot have occurred in the future.")
    incident_id = db.next_id(conn, "incidents", "incident_id", "INC-", 4)
    row = {
        "incident_id": incident_id,
        "reported_at": clock.iso(),
        "occurred_at": occurred,
        "area_id": area_id,
        "incident_type": incident_type,
        "severity": int(severity),
        "source": source,
        "verification_status": "unverified",
        "description": description.strip(),
        "reported_by": reported_by,
    }
    conn.execute(
        """INSERT INTO incidents(incident_id, reported_at, occurred_at, area_id, incident_type, severity,
                                 source, verification_status, description, reported_by)
           VALUES (:incident_id, :reported_at, :occurred_at, :area_id, :incident_type, :severity,
                   :source, :verification_status, :description, :reported_by)""",
        row,
    )
    db.audit(conn, actor_id=reported_by, channel=CHANNEL, action="log_incident", entity_table="incidents", entity_id=incident_id, after=row)
    return incident_id


def set_verification(conn: sqlite3.Connection, incident_id: str, status: str, confirmed_by: str, reason: str | None = None) -> None:
    """Verify or discount an incident (duty officers only). Records verified_by and verified_at."""
    confirm.require_confirmation(conn, confirmed_by, DUTY_ROLES)
    if status not in ("verified", "discounted"):
        raise IncidentError("Status must be 'verified' or 'discounted'.")
    before = conn.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
    if before is None:
        raise IncidentError(f"Unknown incident: {incident_id}")
    if before["verification_status"] == status:
        raise IncidentError(f"{incident_id} is already {status}.")
    conn.execute(
        "UPDATE incidents SET verification_status = ?, verified_by = ?, verified_at = ? WHERE incident_id = ?",
        (status, confirmed_by, clock.iso(), incident_id),
    )
    db.audit(
        conn,
        actor_id=confirmed_by,
        channel=CHANNEL,
        action=f"incident_{status}",
        entity_table="incidents",
        entity_id=incident_id,
        before={"verification_status": before["verification_status"]},
        after={"verification_status": status},
        reason=reason,
    )


def area_incidents(conn: sqlite3.Connection, area_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM incidents WHERE area_id = ? ORDER BY occurred_at", (area_id,))]


def rule_basis(incidents: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Score and the verified incident IDs inside the window, as stored with each decision."""
    used = [
        i["incident_id"]
        for i in incidents
        if i["verification_status"] == "verified" and security.incident_weight(clock.parse(i["occurred_at"]), now) > 0
    ]
    return {"rule_version": security.RULE_VERSION, "score": security.advisory_score(incidents, now), "incidents": used}


def suggestion_for_area(conn: sqlite3.Connection, area_id: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or clock.now()
    basis = rule_basis(area_incidents(conn, area_id), now)
    return {"level": security.level_for_score(basis["score"]), **basis}


def status_board(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict[str, Any]]:
    """One row per area: current advisory, rule suggestion now, score and incident counts."""
    now = now or clock.now()
    current = security.current_advisories(conn)
    window_start = now - timedelta(days=security.LOOKBACK_DAYS)
    rows: list[dict[str, Any]] = []
    for area in conn.execute("SELECT area_id, name_en FROM areas ORDER BY area_id").fetchall():
        aid = area["area_id"]
        sug = suggestion_for_area(conn, aid, now)
        in_window = [i for i in area_incidents(conn, aid) if window_start <= clock.parse(i["occurred_at"]) <= now]
        adv = current.get(aid)
        final = adv["final_level"] if adv else None
        rows.append(
            {
                "area_id": aid,
                "area": area["name_en"],
                "current_level": final,
                "current_name": security.LEVEL_NAMES[final] if final is not None else "No decision",
                "is_override": bool(adv["is_override"]) if adv else False,
                "override_reason": adv["override_reason"] if adv else None,
                "decided_by": adv["decided_by"] if adv else None,
                "decided_at": adv["decided_at"] if adv else None,
                "suggested_level": sug["level"],
                "suggested_name": security.LEVEL_NAMES[sug["level"]],
                "score": sug["score"],
                "verified_in_window": sum(1 for i in in_window if i["verification_status"] == "verified"),
                "unverified_in_window": sum(1 for i in in_window if i["verification_status"] == "unverified"),
                "differs": final is None or final != sug["level"],
            }
        )
    return rows


def decide_advisory(
    conn: sqlite3.Connection,
    area_id: str,
    final_level: int,
    confirmed_by: str,
    override_reason: str | None = None,
) -> str:
    """Confirm the suggested level (final == suggested) or override it with a reason.

    Duty officers only. The previous active row for the area is made inactive in the same transaction.
    """
    confirm.require_confirmation(conn, confirmed_by, DUTY_ROLES)
    if final_level not in security.LEVEL_NAMES:
        raise IncidentError(f"Level must be one of {sorted(security.LEVEL_NAMES)}.")
    if conn.execute("SELECT 1 FROM areas WHERE area_id = ?", (area_id,)).fetchone() is None:
        raise IncidentError(f"Unknown area: {area_id}")
    now = clock.now()
    sug = suggestion_for_area(conn, area_id, now)
    is_override = int(final_level != sug["level"])
    reason = (override_reason or "").strip() or None
    if is_override and reason is None:
        raise OverrideReasonRequired("An override needs a written reason. Nothing was saved.")
    previous = conn.execute("SELECT * FROM v_current_advisory WHERE area_id = ?", (area_id,)).fetchone()
    conn.execute("UPDATE advisories SET active = 0 WHERE area_id = ? AND active = 1", (area_id,))
    advisory_id = db.next_id(conn, "advisories", "advisory_id", "ADV-", 4)
    conn.execute(
        """INSERT INTO advisories(advisory_id, area_id, suggested_level, final_level, is_override, override_reason,
                                  rule_basis_json, decided_by, decided_at, active)
           VALUES (?,?,?,?,?,?,?,?,?,1)""",
        (
            advisory_id,
            area_id,
            sug["level"],
            final_level,
            is_override,
            reason if is_override else None,
            json.dumps({"rule_version": sug["rule_version"], "score": sug["score"], "incidents": sug["incidents"]}),
            confirmed_by,
            clock.iso(now),
        ),
    )
    db.audit(
        conn,
        actor_id=confirmed_by,
        channel=CHANNEL,
        action="override_advisory" if is_override else "confirm_advisory",
        entity_table="advisories",
        entity_id=advisory_id,
        before={"final_level": previous["final_level"]} if previous else None,
        after={"suggested_level": sug["level"], "final_level": final_level, "score": sug["score"]},
        reason=reason if is_override else None,
    )
    return advisory_id
