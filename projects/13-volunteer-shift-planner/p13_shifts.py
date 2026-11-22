"""Volunteer shift planning: rule-based eligibility, ranking, flags and staff-confirmed changes.

Technique: rule-based scoring. For one unfilled role slot a volunteer is eligible when:
  1. active, and trained in the role's required training;
  2. has an availability window on the shift date that covers the whole shift;
  3. is not already assigned (suggested or confirmed) to an overlapping shift, or to this shift;
  4. is under max_shifts_per_week for the ISO week (Monday to Sunday) of the shift.
Eligible volunteers are ranked by (distance in km from home area centroid to the site,
shifts already that week, volunteer ID). Greedy filling, shift by shift, role by role.
Interpreter roles are excluded by design: only the four roles below exist.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from shared import clock, db, geo
from shared.confirm import require_confirmation

CHANNEL = "app:13"
ROLES = ("driver", "packing", "registration", "distribution")
ACTIVE = ("suggested", "confirmed")
CONFIRM_ROLES = ("coordinator",)


# -- small helpers -----------------------------------------------------------
def _week(day: str) -> tuple[int, int]:
    iso = date.fromisoformat(day).isocalendar()
    return (iso[0], iso[1])


def _overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return a_start < b_end and b_start < a_end


@dataclass(frozen=True)
class Booking:
    volunteer_id: str
    shift_id: str
    shift_date: str
    start: str
    end: str


def _bookings(conn: sqlite3.Connection) -> list[Booking]:
    rows = conn.execute(
        """SELECT a.volunteer_id, a.shift_id, s.shift_date, s.start_time, s.end_time
           FROM shift_assignments a JOIN shifts s USING (shift_id)
           WHERE a.status IN ('suggested','confirmed')"""
    ).fetchall()
    return [Booking(r[0], r[1], r[2], r[3], r[4]) for r in rows]


def _shift(conn: sqlite3.Connection, shift_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM shifts WHERE shift_id = ?", (shift_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown shift {shift_id}")
    return row


# -- eligibility --------------------------------------------------------------
def has_training(conn: sqlite3.Connection, volunteer_id: str, training: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM volunteer_trainings WHERE volunteer_id=? AND training=?", (volunteer_id, training)).fetchone()
        is not None
    )


def availability_window(conn: sqlite3.Connection, volunteer_id: str, shift: sqlite3.Row | dict[str, Any]) -> tuple[str, str] | None:
    """The availability window on the shift date that covers the whole shift, or None."""
    row = conn.execute(
        """SELECT start_time, end_time FROM volunteer_availability
           WHERE volunteer_id=? AND avail_date=? AND start_time<=? AND end_time>=?
           ORDER BY start_time LIMIT 1""",
        (volunteer_id, shift["shift_date"], shift["start_time"], shift["end_time"]),
    ).fetchone()
    return (row[0], row[1]) if row else None


def week_load(bookings: list[Booking], volunteer_id: str, shift_date: str) -> int:
    wk = _week(shift_date)
    return len({b.shift_id for b in bookings if b.volunteer_id == volunteer_id and _week(b.shift_date) == wk})


def has_overlap(bookings: list[Booking], volunteer_id: str, shift_id: str, shift: sqlite3.Row | dict[str, Any]) -> bool:
    for b in bookings:
        if b.volunteer_id != volunteer_id:
            continue
        if b.shift_id == shift_id:
            return True
        if b.shift_date == shift["shift_date"] and _overlap(b.start, b.end, shift["start_time"], shift["end_time"]):
            return True
    return False


def candidates(conn: sqlite3.Connection, shift_id: str, role: str, bookings: list[Booking] | None = None) -> list[dict[str, Any]]:
    """Eligible volunteers for one slot, ranked, each with the reasons shown to staff."""
    if role not in ROLES:
        raise ValueError(f"Role {role!r} is not offered (interpreter roles are excluded by design).")
    shift = _shift(conn, shift_id)
    req = conn.execute("SELECT required_training FROM shift_roles WHERE shift_id=? AND role=?", (shift_id, role)).fetchone()
    if req is None:
        raise ValueError(f"Shift {shift_id} has no {role} role.")
    training = req[0]
    site = conn.execute("SELECT x_km, y_km FROM sites WHERE site_id=?", (shift["site_id"],)).fetchone()
    bookings = _bookings(conn) if bookings is None else bookings
    out: list[dict[str, Any]] = []
    vols = conn.execute(
        """SELECT v.volunteer_id, v.max_shifts_per_week, a.x_km, a.y_km
           FROM volunteers v JOIN areas a ON a.area_id = v.home_area_id
           WHERE v.active = 1 ORDER BY v.volunteer_id"""
    ).fetchall()
    for v in vols:
        vid = v["volunteer_id"]
        if not has_training(conn, vid, training):
            continue
        window = availability_window(conn, vid, shift)
        if window is None:
            continue
        if has_overlap(bookings, vid, shift_id, shift):
            continue
        load = week_load(bookings, vid, shift["shift_date"])
        if load >= v["max_shifts_per_week"]:
            continue
        dist = geo.distance_km((v["x_km"], v["y_km"]), (site["x_km"], site["y_km"]))
        out.append(
            {
                "volunteer_id": vid,
                "distance_km": dist,
                "shifts_that_week": load,
                "max_per_week": v["max_shifts_per_week"],
                "why": f"{training} trained; available {window[0]}-{window[1]}; {dist} km from site; "
                f"{load} of {v['max_shifts_per_week']} shifts that week",
            }
        )
    out.sort(key=lambda c: (c["distance_km"], c["shifts_that_week"], c["volunteer_id"]))
    return out


# -- role status and flags ------------------------------------------------------
def role_status(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """One row per shift role: required, filled (not cancelled), confirmed, unfilled."""
    rows = conn.execute(
        """SELECT r.shift_id, s.shift_date, s.start_time, s.end_time, s.site_id, s.purpose, r.role,
                  r.required_count AS required, r.required_training,
                  (SELECT COUNT(*) FROM shift_assignments a WHERE a.shift_id=r.shift_id AND a.role=r.role
                     AND a.status IN ('suggested','confirmed')) AS filled,
                  (SELECT COUNT(*) FROM shift_assignments a WHERE a.shift_id=r.shift_id AND a.role=r.role
                     AND a.status = 'confirmed') AS confirmed
           FROM shift_roles r JOIN shifts s USING (shift_id)
           ORDER BY s.shift_date, s.start_time, r.shift_id, r.role"""
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["unfilled"] = max(0, d["required"] - d["filled"])
        out.append(d)
    return out


def unfilled_total(conn: sqlite3.Connection) -> int:
    return sum(r["unfilled"] for r in role_status(conn))


def overlaps(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Pairs of non-cancelled assignments of one volunteer whose times overlap on the same date."""
    rows = conn.execute(
        """SELECT a.assignment_id, a.volunteer_id, a.shift_id, a.role, a.status, s.shift_date, s.start_time, s.end_time
           FROM shift_assignments a JOIN shifts s USING (shift_id)
           WHERE a.status IN ('suggested','confirmed')
           ORDER BY a.volunteer_id, s.shift_date, s.start_time, a.assignment_id"""
    ).fetchall()
    out = []
    for i, x in enumerate(rows):
        for y in rows[i + 1 :]:
            if x["volunteer_id"] != y["volunteer_id"]:
                break
            if x["shift_date"] == y["shift_date"] and _overlap(x["start_time"], x["end_time"], y["start_time"], y["end_time"]):
                out.append(
                    {
                        "volunteer_id": x["volunteer_id"],
                        "shift_date": x["shift_date"],
                        "assignment_a": x["assignment_id"],
                        "shift_a": x["shift_id"],
                        "role_a": x["role"],
                        "status_a": x["status"],
                        "time_a": f"{x['start_time']}-{x['end_time']}",
                        "assignment_b": y["assignment_id"],
                        "shift_b": y["shift_id"],
                        "role_b": y["role"],
                        "status_b": y["status"],
                        "time_b": f"{y['start_time']}-{y['end_time']}",
                    }
                )
    return out


def invalid_assignments(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Non-cancelled assignments whose volunteer lacks the training or a covering availability window."""
    out = []
    rows = conn.execute(
        """SELECT a.assignment_id, a.volunteer_id, a.shift_id, a.role, a.status, r.required_training,
                  s.shift_date, s.start_time, s.end_time
           FROM shift_assignments a
           JOIN shift_roles r ON r.shift_id=a.shift_id AND r.role=a.role
           JOIN shifts s ON s.shift_id=a.shift_id
           WHERE a.status IN ('suggested','confirmed') ORDER BY a.assignment_id"""
    ).fetchall()
    for r in rows:
        problems = []
        if not has_training(conn, r["volunteer_id"], r["required_training"]):
            problems.append(f"no {r['required_training']} training")
        if availability_window(conn, r["volunteer_id"], r) is None:
            problems.append("no availability window covering the shift")
        if problems:
            out.append(
                {
                    "assignment_id": r["assignment_id"],
                    "volunteer_id": r["volunteer_id"],
                    "shift_id": r["shift_id"],
                    "role": r["role"],
                    "problem": "; ".join(problems),
                }
            )
    return out


# -- suggestions -----------------------------------------------------------------
def suggest(conn: sqlite3.Connection, shift_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Greedy suggestions for every unfilled slot. Does not write anything."""
    bookings = _bookings(conn)
    proposals: list[dict[str, Any]] = []
    for slot in role_status(conn):
        if shift_ids is not None and slot["shift_id"] not in shift_ids:
            continue
        shift = _shift(conn, slot["shift_id"])
        for _ in range(slot["unfilled"]):
            ranked = candidates(conn, slot["shift_id"], slot["role"], bookings)
            if not ranked:
                break
            best = ranked[0]
            proposals.append({"shift_id": slot["shift_id"], "role": slot["role"], **best})
            bookings.append(Booking(best["volunteer_id"], slot["shift_id"], shift["shift_date"], shift["start_time"], shift["end_time"]))
    return proposals


# -- changes (staff confirmed, audited) -------------------------------------------
def accept_suggestion(
    conn: sqlite3.Connection, shift_id: str, role: str, volunteer_id: str, confirmed_by: str | None, status: str = "suggested"
) -> int:
    """Record one assignment. Re-checks eligibility and the required count; returns the assignment ID."""
    staff = require_confirmation(conn, confirmed_by, CONFIRM_ROLES)
    if role not in ROLES:
        raise ValueError(f"Role {role!r} is not offered (interpreter roles are excluded by design).")
    if status not in ACTIVE:
        raise ValueError("New assignments are 'suggested' or 'confirmed'.")
    slot = next((s for s in role_status(conn) if s["shift_id"] == shift_id and s["role"] == role), None)
    if slot is None:
        raise ValueError(f"Shift {shift_id} has no {role} role.")
    if slot["unfilled"] <= 0:
        raise ValueError(f"{shift_id} {role} is already fully assigned.")
    if volunteer_id not in {c["volunteer_id"] for c in candidates(conn, shift_id, role)}:
        raise ValueError(f"{volunteer_id} is not eligible for {shift_id} {role}.")
    cur = conn.execute(
        "INSERT INTO shift_assignments(shift_id, role, volunteer_id, status, assigned_by, assigned_at) VALUES (?,?,?,?,?,?)",
        (shift_id, role, volunteer_id, status, staff, clock.iso()),
    )
    aid = int(cur.lastrowid or 0)
    db.audit(
        conn,
        actor_id=staff,
        channel=CHANNEL,
        action="assignment_added",
        entity_table="shift_assignments",
        entity_id=str(aid),
        before=None,
        after={"shift_id": shift_id, "role": role, "volunteer_id": volunteer_id, "status": status},
        reason="Accepted suggested assignment",
    )
    return aid


def _set_status(conn: sqlite3.Connection, assignment_id: int, new_status: str, confirmed_by: str | None, reason: str | None) -> None:
    staff = require_confirmation(conn, confirmed_by, CONFIRM_ROLES)
    before = db.row_dict(conn, "shift_assignments", "assignment_id", assignment_id)
    if before is None:
        raise ValueError(f"Unknown assignment {assignment_id}")
    if before["status"] == "cancelled":
        raise ValueError("A cancelled assignment cannot be changed.")
    if new_status == "confirmed" and before["status"] != "suggested":
        raise ValueError("Only a suggested assignment can be confirmed.")
    conn.execute("UPDATE shift_assignments SET status=?, assigned_by=? WHERE assignment_id=?", (new_status, staff, assignment_id))
    db.audit(
        conn,
        actor_id=staff,
        channel=CHANNEL,
        action=f"assignment_{new_status}",
        entity_table="shift_assignments",
        entity_id=str(assignment_id),
        before=before,
        after={**before, "status": new_status, "assigned_by": staff},
        reason=reason,
    )


def confirm_assignment(conn: sqlite3.Connection, assignment_id: int, confirmed_by: str | None, reason: str | None = None) -> None:
    _set_status(conn, assignment_id, "confirmed", confirmed_by, reason or "Coordinator confirmed assignment")


def cancel_assignment(conn: sqlite3.Connection, assignment_id: int, confirmed_by: str | None, reason: str | None = None) -> None:
    _set_status(conn, assignment_id, "cancelled", confirmed_by, reason or "Coordinator cancelled assignment")


def assignments(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT a.assignment_id, a.shift_id, s.shift_date, s.start_time, s.end_time, a.role, a.volunteer_id, a.status
           FROM shift_assignments a JOIN shifts s USING (shift_id)
           WHERE a.status IN ('suggested','confirmed')
           ORDER BY s.shift_date, s.start_time, a.shift_id, a.role, a.volunteer_id"""
    ).fetchall()
    return [dict(r) for r in rows]
