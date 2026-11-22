"""Project 02: log every incoming call, link calls to complaints, follow up and close.

Counting rule: complaints are counted only from `complaints` rows and calls only from
`calls` rows. A repeat call adds a call row (`is_repeat = 1`) and never a complaint row.
All functions take a connection so tests can call them directly.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Any

import pandas as pd

from shared import clock, confirm, db
from shared.seed_content import COMPLAINT_TOPICS

CHANNEL = "app:02"
CALL_TYPES = ("information", "service_request", "complaint", "follow_up")
TOPICS = tuple(COMPLAINT_TOPICS.keys())
OWNER_ROLES = ("caseworker", "coordinator")
CLOSE_ROLES = ("caseworker", "coordinator")
FOLLOWUP_ACTIONS = ("contacted_family", "escalated", "site_visit", "note")
PERIODS: dict[str, int] = {"Today": 1, "Last 7 days": 7, "Last 30 days": 30}
RECURRING_MIN_COMPLAINTS = 2  # a topic is "recurring" with this many complaints opened in the period


class CallError(ValueError):
    """The call or complaint action is not valid; nothing was written."""


# -- periods --------------------------------------------------------------
def period_bounds(days: int) -> tuple[str, str]:
    """First and last calendar day (ISO dates, both included) of the last `days` days up to today."""
    today = clock.today()
    return (today - timedelta(days=days - 1)).isoformat(), today.isoformat()


# -- logging a call -------------------------------------------------------
def _require_operator(conn: sqlite3.Connection, operator_id: str) -> None:
    role = db.scalar("SELECT role FROM staff WHERE staff_id = ? AND active = 1", [operator_id], conn=conn)
    if role not in ("operator", "admin"):
        raise CallError(f"{operator_id} is not an active operator.")


def log_call(
    conn: sqlite3.Connection,
    *,
    operator_id: str,
    call_type: str,
    language: str,
    area_id: str,
    notes: str,
    household_id: str | None = None,
    duration_sec: int | None = None,
    complaint_id: str | None = None,
    new_complaint_topic: str | None = None,
    new_complaint_description: str | None = None,
    request_id: str | None = None,
    new_request: bool = False,
) -> dict[str, Any]:
    """Log one incoming call and its link. Returns {call_id, complaint_id, request_id, is_repeat}.

    - information: no link.
    - complaint: either an existing open complaint (`complaint_id`, call is a repeat) or a
      new one (`new_complaint_topic` + description, creates one complaint, call is not a repeat).
    - service_request: an existing open request (`request_id`, repeat) or `new_request=True`.
    - follow_up: an existing request or complaint, always a repeat.
    """
    _require_operator(conn, operator_id)
    if call_type not in CALL_TYPES:
        raise CallError(f"Unknown call type: {call_type}")
    if language not in ("ar", "en"):
        raise CallError("Language must be ar or en.")
    if not notes or not notes.strip():
        raise CallError("Call notes are required.")
    if db.scalar("SELECT 1 FROM areas WHERE area_id = ?", [area_id], conn=conn) is None:
        raise CallError(f"Unknown area: {area_id}")
    household_id = (household_id or "").strip() or None
    if household_id and db.scalar("SELECT 1 FROM households WHERE household_id = ?", [household_id], conn=conn) is None:
        raise CallError(f"Unknown household: {household_id}")

    now = clock.iso()
    new_cid: str | None = None
    link_complaint = complaint_id
    link_request = request_id
    is_repeat = 0

    if call_type == "information":
        if complaint_id or request_id or new_complaint_topic or new_request:
            raise CallError("An information call has no link.")
        link_complaint = link_request = None
    elif call_type == "complaint":
        if request_id or new_request:
            raise CallError("A complaint call links to a complaint, not a request.")
        if complaint_id and new_complaint_topic:
            raise CallError("Link to an existing complaint or create a new one, not both.")
        if complaint_id:
            _open_complaint(conn, complaint_id)
            is_repeat = 1
        elif new_complaint_topic:
            if new_complaint_topic not in TOPICS:
                raise CallError(f"Unknown complaint topic: {new_complaint_topic}")
            description = (new_complaint_description or "").strip() or notes.strip()
            new_cid = db.next_id(conn, "complaints", "complaint_id", "CMP-", 4)
            conn.execute(
                "INSERT INTO complaints (complaint_id, opened_at, household_id, area_id, topic, description, status, created_by)"
                " VALUES (?,?,?,?,?,?, 'open', ?)",
                (new_cid, now, household_id, area_id, new_complaint_topic, description, operator_id),
            )
            link_complaint = new_cid
            db.audit(
                conn,
                actor_id=operator_id,
                channel=CHANNEL,
                action="complaint_opened",
                entity_table="complaints",
                entity_id=new_cid,
                after={"topic": new_complaint_topic, "area_id": area_id, "household_id": household_id},
            )
        else:
            raise CallError("A complaint call needs an existing complaint or a new complaint topic.")
    elif call_type == "service_request":
        if complaint_id or new_complaint_topic:
            raise CallError("A service request call links to a request, not a complaint.")
        if request_id and new_request:
            raise CallError("Link to an existing request or create a new one, not both.")
        if request_id:
            _open_request(conn, request_id)
            is_repeat = 1
        elif new_request:
            link_request = db.next_id(conn, "service_requests", "request_id", "REQ-", 5)
            conn.execute(
                "INSERT INTO service_requests (request_id, created_at, household_id, area_id, notes, language, category, status, created_by)"
                " VALUES (?,?,?,?,?,?, NULL, 'new', ?)",
                (link_request, now, household_id, area_id, notes.strip(), language, operator_id),
            )
            conn.execute(
                "INSERT INTO request_events (request_id, at, staff_id, event_type, detail) VALUES (?,?,?, 'created', ?)",
                (link_request, now, operator_id, "Created from an incoming call"),
            )
            db.audit(
                conn,
                actor_id=operator_id,
                channel=CHANNEL,
                action="request_created",
                entity_table="service_requests",
                entity_id=link_request,
                after={"status": "new", "area_id": area_id},
            )
        else:
            raise CallError("A service request call needs an existing request or a new request.")
    else:  # follow_up
        if bool(complaint_id) == bool(request_id):
            raise CallError("A follow-up call links to exactly one existing complaint or request.")
        if complaint_id:
            _open_complaint(conn, complaint_id)
        else:
            _open_request(conn, request_id or "")
        if new_complaint_topic or new_request:
            raise CallError("A follow-up call creates nothing new.")
        is_repeat = 1

    call_id = db.next_id(conn, "calls", "call_id", "CALL-", 6)
    conn.execute(
        "INSERT INTO calls (call_id, received_at, operator_id, household_id, area_id, call_type, language, notes,"
        " duration_sec, complaint_id, request_id, is_repeat) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            call_id,
            now,
            operator_id,
            household_id,
            area_id,
            call_type,
            language,
            notes.strip(),
            duration_sec,
            link_complaint,
            link_request,
            is_repeat,
        ),
    )
    db.audit(
        conn,
        actor_id=operator_id,
        channel=CHANNEL,
        action="call_logged",
        entity_table="calls",
        entity_id=call_id,
        after={"call_type": call_type, "complaint_id": link_complaint, "request_id": link_request, "is_repeat": is_repeat},
    )
    return {"call_id": call_id, "complaint_id": link_complaint, "request_id": link_request, "is_repeat": is_repeat}


def _open_complaint(conn: sqlite3.Connection, complaint_id: str) -> None:
    status = db.scalar("SELECT status FROM complaints WHERE complaint_id = ?", [complaint_id], conn=conn)
    if status is None:
        raise CallError(f"Unknown complaint: {complaint_id}")
    if status == "closed":
        raise CallError(f"{complaint_id} is closed; open a new complaint instead.")


def _open_request(conn: sqlite3.Connection, request_id: str) -> None:
    status = db.scalar("SELECT status FROM service_requests WHERE request_id = ?", [request_id], conn=conn)
    if status is None:
        raise CallError(f"Unknown request: {request_id}")
    if status == "closed":
        raise CallError(f"{request_id} is closed; create a new request instead.")


# -- complaint handling ---------------------------------------------------
def _complaint(conn: sqlite3.Connection, complaint_id: str) -> dict[str, Any]:
    row = db.row_dict(conn, "complaints", "complaint_id", complaint_id)
    if row is None:
        raise CallError(f"Unknown complaint: {complaint_id}")
    if row["status"] == "closed":
        raise CallError(f"{complaint_id} is already closed.")
    return row


def _followup(conn: sqlite3.Connection, complaint_id: str, staff_id: str, action: str, note: str) -> None:
    conn.execute(
        "INSERT INTO complaint_followups (complaint_id, at, staff_id, action, note) VALUES (?,?,?,?,?)",
        (complaint_id, clock.iso(), staff_id, action, note),
    )


def assign_owner(conn: sqlite3.Connection, complaint_id: str, owner_id: str, assigned_by: str) -> None:
    """Assign a caseworker or coordinator as owner: `owner_assigned` follow-up, status in_progress."""
    before = _complaint(conn, complaint_id)
    if db.scalar("SELECT role FROM staff WHERE staff_id = ? AND active = 1", [owner_id], conn=conn) not in OWNER_ROLES:
        raise CallError("The owner must be an active caseworker or coordinator.")
    if db.scalar("SELECT 1 FROM staff WHERE staff_id = ? AND active = 1", [assigned_by], conn=conn) is None:
        raise CallError(f"Unknown staff: {assigned_by}")
    conn.execute("UPDATE complaints SET owner_id = ?, status = 'in_progress' WHERE complaint_id = ?", (owner_id, complaint_id))
    _followup(conn, complaint_id, assigned_by, "owner_assigned", f"Owner assigned: {owner_id}")
    db.audit(
        conn,
        actor_id=assigned_by,
        channel=CHANNEL,
        action="complaint_owner_assigned",
        entity_table="complaints",
        entity_id=complaint_id,
        before={"owner_id": before["owner_id"], "status": before["status"]},
        after={"owner_id": owner_id, "status": "in_progress"},
    )


def record_followup(conn: sqlite3.Connection, complaint_id: str, staff_id: str, action: str, note: str) -> None:
    """Record contacted_family, escalated, site_visit or note on an open complaint."""
    _complaint(conn, complaint_id)
    if action not in FOLLOWUP_ACTIONS:
        raise CallError(f"Unknown follow-up action: {action}")
    if not note or not note.strip():
        raise CallError("A follow-up note is required.")
    if db.scalar("SELECT 1 FROM staff WHERE staff_id = ? AND active = 1", [staff_id], conn=conn) is None:
        raise CallError(f"Unknown staff: {staff_id}")
    _followup(conn, complaint_id, staff_id, action, note.strip())
    db.audit(
        conn,
        actor_id=staff_id,
        channel=CHANNEL,
        action=f"complaint_{action}",
        entity_table="complaints",
        entity_id=complaint_id,
        after={"note": note.strip()},
    )


def close_complaint(conn: sqlite3.Connection, complaint_id: str, resolution: str, confirmed_by: str | None) -> None:
    """Close a complaint with a resolution. Consequential: needs staff confirmation."""
    staff = confirm.require_confirmation(conn, confirmed_by, CLOSE_ROLES)
    before = _complaint(conn, complaint_id)
    if not resolution or not resolution.strip():
        raise CallError("A resolution is required to close a complaint.")
    now = clock.iso()
    conn.execute(
        "UPDATE complaints SET status = 'closed', closed_at = ?, resolution = ? WHERE complaint_id = ?",
        (now, resolution.strip(), complaint_id),
    )
    _followup(conn, complaint_id, staff, "closed", resolution.strip())
    db.audit(
        conn,
        actor_id=staff,
        channel=CHANNEL,
        action="complaint_closed",
        entity_table="complaints",
        entity_id=complaint_id,
        before={"status": before["status"]},
        after={"status": "closed", "closed_at": now, "resolution": resolution.strip()},
        reason=resolution.strip(),
    )


# -- reading --------------------------------------------------------------
def open_complaints(conn: sqlite3.Connection) -> pd.DataFrame:
    return db.query_df(
        "SELECT v.complaint_id, v.topic, v.status, v.call_count AS calls, c.owner_id, c.opened_at, c.area_id"
        " FROM v_complaint_call_counts v JOIN complaints c ON c.complaint_id = v.complaint_id"
        " WHERE c.status != 'closed' ORDER BY c.opened_at DESC",
        conn=conn,
    )


def open_requests(conn: sqlite3.Connection) -> pd.DataFrame:
    return db.query_df(
        "SELECT request_id, created_at, area_id, status, substr(notes, 1, 60) AS notes FROM service_requests"
        " WHERE status != 'closed' ORDER BY created_at DESC LIMIT 100",
        conn=conn,
    )


def complaint_call_count(conn: sqlite3.Connection, complaint_id: str) -> int:
    return int(db.scalar("SELECT call_count FROM v_complaint_call_counts WHERE complaint_id = ?", [complaint_id], conn=conn) or 0)


def timeline(conn: sqlite3.Connection, complaint_id: str) -> pd.DataFrame:
    """Follow-ups and every linked call for one complaint, oldest first."""
    df = db.query_df(
        "SELECT at, 'follow-up: ' || action AS event, staff_id AS by_staff, note AS detail FROM complaint_followups"
        " WHERE complaint_id = ?"
        " UNION ALL"
        " SELECT received_at, 'call (' || call_type || CASE WHEN is_repeat = 1 THEN ', repeat' ELSE '' END || ')',"
        " operator_id, notes FROM calls WHERE complaint_id = ?"
        " ORDER BY 1",
        [complaint_id, complaint_id],
        conn=conn,
    )
    return df.rename(columns={"at": "when"})


# -- figures --------------------------------------------------------------
def period_figures(conn: sqlite3.Connection, days: int) -> dict[str, Any]:
    """Calls received (all types), new complaints (opened in the period), open complaints (now)."""
    start, end = period_bounds(days)
    by_type = {t: 0 for t in CALL_TYPES}
    for row in db.query_all(
        "SELECT call_type, COUNT(*) AS n FROM calls WHERE substr(received_at, 1, 10) BETWEEN ? AND ? GROUP BY call_type",
        [start, end],
        conn=conn,
    ):
        by_type[row["call_type"]] = row["n"]
    new_complaints = db.scalar("SELECT COUNT(*) FROM complaints WHERE substr(opened_at, 1, 10) BETWEEN ? AND ?", [start, end], conn=conn)
    open_now = db.scalar("SELECT COUNT(*) FROM complaints WHERE status != 'closed'", conn=conn)
    return {
        "start": start,
        "end": end,
        "calls_received": sum(by_type.values()),
        "calls_by_type": by_type,
        "new_complaints": int(new_complaints or 0),
        "open_complaints": int(open_now or 0),
    }


def recurring_topics(conn: sqlite3.Connection, days: int) -> pd.DataFrame:
    """Per topic: complaints opened in the period, calls in the period about complaints of that topic,
    and repeat calls among them. A topic is *recurring* when it has `RECURRING_MIN_COMPLAINTS` or more
    complaints opened in the period. Complaints come only from `complaints`, calls only from `calls`
    (the join is used only to read a call's complaint topic)."""
    start, end = period_bounds(days)
    complaints = {
        r["topic"]: r["n"]
        for r in db.query_all(
            "SELECT topic, COUNT(*) AS n FROM complaints WHERE substr(opened_at, 1, 10) BETWEEN ? AND ? GROUP BY topic",
            [start, end],
            conn=conn,
        )
    }
    calls: dict[str, int] = {}
    repeats: dict[str, int] = {}
    for r in db.query_all(
        "SELECT c.topic, COUNT(*) AS n, SUM(k.is_repeat) AS rep FROM calls k JOIN complaints c ON c.complaint_id = k.complaint_id"
        " WHERE substr(k.received_at, 1, 10) BETWEEN ? AND ? GROUP BY c.topic",
        [start, end],
        conn=conn,
    ):
        calls[r["topic"]] = r["n"]
        repeats[r["topic"]] = int(r["rep"] or 0)
    topics = list(dict.fromkeys([*TOPICS, *complaints, *calls]))
    rows = [
        {
            "topic": t,
            "complaints": complaints.get(t, 0),
            "calls": calls.get(t, 0),
            "repeat_calls": repeats.get(t, 0),
            "recurring": "yes" if complaints.get(t, 0) >= RECURRING_MIN_COMPLAINTS else "no",
        }
        for t in topics
    ]
    return pd.DataFrame(rows).sort_values(["complaints", "calls"], ascending=False, ignore_index=True)


def recent_calls(conn: sqlite3.Connection, limit: int = 15) -> pd.DataFrame:
    return db.query_df(
        "SELECT call_id, received_at, operator_id, call_type, language, area_id, complaint_id, request_id, is_repeat, notes"
        " FROM calls ORDER BY received_at DESC, call_id DESC LIMIT ?",
        [limit],
        conn=conn,
    )
