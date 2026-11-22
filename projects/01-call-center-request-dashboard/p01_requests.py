"""Project 01 logic: service requests from intake to closure.

Workflow (each step is a function that takes a connection, writes a request_events
row and an audit_log row in the caller's transaction):

    intake (create_request)        status new, category confirmed by the operator
    confirm_category               for requests that arrived without one (seed, Project 02)
    assign                         new -> assigned (category must be confirmed first)
    schedule_callback              assigned -> callback_scheduled (due = assigned + SLA hours, or earlier)
    record_callback_done           callback_scheduled -> in_progress
    close                          in_progress -> closed (staff confirmation required)

Any other order raises RequestError. The category suggestion comes from the shared
classifier (shared.classifier, trained in Project 03); below the confidence
threshold the suggestion is stored as NULL and the operator must choose.

Next action (rule-based, see next_action): the first open step of the workflow,
checked in this order:

    1. closed                                  -> "None (closed)"
    2. no confirmed category                   -> "Confirm category"
    3. not assigned                            -> "Assign to a caseworker"
    4. no callback scheduled                   -> "Schedule callback"
    5. callback not done, due time passed      -> "Call back now: N h overdue"
    6. callback not done, due time not passed  -> "Call back by <time>"
    7. callback done                           -> "Close or record outcome"

A callback is overdue when the current time is strictly after its due time, it is
not done and the request is not closed. At exactly the due time it is not overdue.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import pandas as pd

from shared import classifier, clock, confirm, db
from shared.call_notes import CATEGORIES, CATEGORY_LABELS

CHANNEL = "app:01"
STATUSES: tuple[str, ...] = ("new", "assigned", "callback_scheduled", "in_progress", "closed")
PRIORITIES: tuple[str, ...] = ("low", "normal", "high", "urgent")
LANGUAGES: tuple[str, ...] = ("en", "ar")
OUTCOMES: tuple[str, ...] = ("assisted", "referred", "not_eligible", "unreachable", "duplicate", "withdrawn")
DEFAULT_SLA_HOURS = 48.0

INTAKE_ROLES: tuple[str, ...] = ("operator", "caseworker", "coordinator", "admin")
CASE_ROLES: tuple[str, ...] = ("caseworker", "coordinator", "admin")
CLOSE_ROLES: tuple[str, ...] = ("caseworker", "coordinator")


class RequestError(ValueError):
    """A workflow step was attempted out of order or with invalid input."""


# -- settings and model ----------------------------------------------------
def sla_hours(conn: sqlite3.Connection) -> float:
    """Hours after assignment within which the callback is due (setting callback_sla_hours)."""
    return db.get_setting_float("callback_sla_hours", DEFAULT_SLA_HOURS, conn)


def default_callback_due(assigned_at: datetime | str, hours: float) -> datetime:
    """Default callback due time: assigned time plus the SLA hours."""
    start = clock.parse(assigned_at) if isinstance(assigned_at, str) else assigned_at
    return start + timedelta(hours=hours)


def suggest(conn: sqlite3.Connection, model: Any, notes: str) -> classifier.Prediction:
    """The shared classifier's suggestion; category is None below the threshold setting."""
    return classifier.predict(model, notes, classifier.threshold_from_settings(conn))


def category_label(category: str | None) -> str:
    return CATEGORY_LABELS.get(category or "", category or "")


# -- rules -------------------------------------------------------------------
def _dt(value: Any) -> datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    return value if isinstance(value, datetime) else clock.parse(str(value))


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) or value == ""


def is_callback_overdue(row: Mapping[str, Any], now: datetime) -> bool:
    """Due time strictly before now, callback not done, request not closed."""
    due = _dt(row.get("callback_due_at"))
    if due is None or not _blank(row.get("callback_done_at")) or row.get("status") == "closed":
        return False
    return now > due


def hours_overdue(row: Mapping[str, Any], now: datetime) -> float:
    """Hours past the callback due time (0 when not overdue)."""
    if not is_callback_overdue(row, now):
        return 0.0
    due = _dt(row.get("callback_due_at"))
    assert due is not None
    return round((now - due).total_seconds() / 3600.0, 1)


def next_action(row: Mapping[str, Any], now: datetime) -> str:
    """Rule-based next action for one request (order documented in the module docstring)."""
    if row.get("status") == "closed":
        return "None (closed)"
    if _blank(row.get("category")):
        return "Confirm category"
    if _blank(row.get("assigned_to")):
        return "Assign to a caseworker"
    due = _dt(row.get("callback_due_at"))
    if due is None:
        return "Schedule callback"
    if _blank(row.get("callback_done_at")):
        if is_callback_overdue(row, now):
            return f"Call back now: {hours_overdue(row, now):g} h overdue"
        return f"Call back by {due:%a %d %b %H:%M}"
    return "Close or record outcome"


# -- views ---------------------------------------------------------------------
_LIST_SQL = """
SELECT r.request_id, r.created_at, r.area_id, a.name_en AS area, r.household_id, r.language, r.priority,
       r.status, r.category, r.suggested_category, r.suggested_confidence, r.assigned_to, r.assigned_at,
       r.callback_due_at, r.callback_done_at, r.closed_at, r.closure_outcome,
       (SELECT COUNT(*) FROM calls c WHERE c.request_id = r.request_id) AS calls
FROM service_requests r JOIN areas a ON a.area_id = r.area_id
"""


def open_requests(conn: sqlite3.Connection, now: datetime | None = None) -> pd.DataFrame:
    """Every open request with its linked calls count, overdue hours and next action."""
    now = now or clock.now()
    df = db.query_df(_LIST_SQL + " WHERE r.status != 'closed' ORDER BY r.created_at, r.request_id", conn=conn)
    rows = df.to_dict("records")
    df["hours_overdue"] = [hours_overdue(r, now) for r in rows]
    df["next_action"] = [next_action(r, now) for r in rows]
    return df


def unassigned_requests(conn: sqlite3.Connection, now: datetime | None = None) -> pd.DataFrame:
    """Open requests with no caseworker assigned."""
    df = open_requests(conn, now)
    return df[df["assigned_to"].isna()].reset_index(drop=True)


def overdue_callbacks(conn: sqlite3.Connection, now: datetime | None = None) -> pd.DataFrame:
    """Open requests whose callback is due before now and not done, most overdue first."""
    now = now or clock.now()
    df = db.query_df(
        _LIST_SQL + " WHERE r.status != 'closed' AND r.callback_done_at IS NULL AND r.callback_due_at < ? ORDER BY r.callback_due_at",
        (clock.iso(now),),
        conn=conn,
    )
    rows = df.to_dict("records")
    df["hours_overdue"] = [hours_overdue(r, now) for r in rows]
    df["next_action"] = [next_action(r, now) for r in rows]
    return df


def metrics(conn: sqlite3.Connection, now: datetime | None = None) -> dict[str, int]:
    """Open requests, unassigned, overdue callbacks, closed in the last 7 days."""
    now = now or clock.now()
    week_ago = clock.iso(now - timedelta(days=7))
    return {
        "open": int(db.scalar("SELECT COUNT(*) FROM service_requests WHERE status != 'closed'", conn=conn)),
        "unassigned": int(len(unassigned_requests(conn, now))),
        "overdue": int(len(overdue_callbacks(conn, now))),
        "closed_7d": int(
            db.scalar(
                "SELECT COUNT(*) FROM service_requests WHERE status = 'closed' AND closed_at >= ? AND closed_at <= ?",
                (week_ago, clock.iso(now)),
                conn=conn,
            )
        ),
    }


def get_request(conn: sqlite3.Connection, request_id: str) -> dict[str, Any]:
    row = db.row_dict(conn, "service_requests", "request_id", request_id)
    if row is None:
        raise RequestError(f"Unknown request {request_id}.")
    return row


def calls_count(conn: sqlite3.Connection, request_id: str) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM calls WHERE request_id = ?", (request_id,), conn=conn))


def timeline(conn: sqlite3.Connection, request_id: str) -> pd.DataFrame:
    return db.query_df(
        "SELECT at, event_type, staff_id, detail FROM request_events WHERE request_id = ? ORDER BY at, event_id",
        (request_id,),
        conn=conn,
    )


def stored_or_computed_suggestion(conn: sqlite3.Connection, model: Any, row: Mapping[str, Any]) -> tuple[classifier.Prediction, bool]:
    """The stored suggestion, or a fresh one from the classifier when none was stored. Returns (prediction, was_stored)."""
    if row.get("suggested_confidence") is not None:
        cat = row.get("suggested_category")
        return classifier.Prediction(cat, float(row["suggested_confidence"]), cat or ""), True
    return suggest(conn, model, str(row.get("notes") or "")), False


# -- workflow ----------------------------------------------------------------
def _require_role(conn: sqlite3.Connection, staff_id: str | None, roles: tuple[str, ...], what: str) -> str:
    if not staff_id:
        raise RequestError(f"Choose the staff member who {what}.")
    row = conn.execute("SELECT role, active FROM staff WHERE staff_id = ?", (staff_id,)).fetchone()
    if row is None or not row["active"]:
        raise RequestError(f"Unknown or inactive staff ID: {staff_id}.")
    if row["role"] not in roles:
        raise RequestError(f"{staff_id} ({row['role']}) cannot do this; it needs one of: {', '.join(r for r in roles if r != 'admin')}.")
    return staff_id


def _require_status(row: Mapping[str, Any], expected: str, step: str) -> None:
    if row["status"] != expected:
        raise RequestError(
            f"Cannot {step} {row['request_id']}: its status is '{row['status']}', and this step needs '{expected}'. "
            "Steps go new, assigned, callback scheduled, callback done (in progress), closed."
        )


def _check_category(category: str | None) -> str:
    if not category:
        raise RequestError("Choose the category. The operator must confirm it before saving.")
    if category not in CATEGORIES:
        raise RequestError(f"Unknown category {category!r}; choose one of {', '.join(CATEGORIES)}.")
    return category


def _event(conn: sqlite3.Connection, request_id: str, at: str, staff_id: str, event_type: str, detail: str | None) -> None:
    conn.execute(
        "INSERT INTO request_events(request_id, at, staff_id, event_type, detail) VALUES (?,?,?,?,?)",
        (request_id, at, staff_id, event_type, detail),
    )


def _update(conn: sqlite3.Connection, request_id: str, changes: dict[str, Any]) -> None:
    sets = ", ".join(f"{k} = ?" for k in changes)
    conn.execute(f"UPDATE service_requests SET {sets} WHERE request_id = ?", (*changes.values(), request_id))


def create_request(
    conn: sqlite3.Connection,
    model: Any,
    *,
    area_id: str,
    notes: str,
    language: str,
    category: str | None,
    created_by: str,
    priority: str = "normal",
    household_id: str | None = None,
) -> str:
    """Intake: store the classifier's suggestion and the operator's confirmed category, status new."""
    _require_role(conn, created_by, INTAKE_ROLES, "takes the request")
    if not notes or not notes.strip():
        raise RequestError("Enter the operator's notes.")
    if language not in LANGUAGES:
        raise RequestError(f"Language must be one of {', '.join(LANGUAGES)}.")
    if priority not in PRIORITIES:
        raise RequestError(f"Priority must be one of {', '.join(PRIORITIES)}.")
    if conn.execute("SELECT 1 FROM areas WHERE area_id = ?", (area_id,)).fetchone() is None:
        raise RequestError(f"Unknown area {area_id}.")
    if household_id and conn.execute("SELECT 1 FROM households WHERE household_id = ?", (household_id,)).fetchone() is None:
        raise RequestError(f"Unknown household {household_id}.")
    category = _check_category(category)
    prediction = suggest(conn, model, notes)
    now = clock.iso()
    request_id = db.next_id(conn, "service_requests", "request_id", "REQ-", 5)
    row = {
        "request_id": request_id,
        "created_at": now,
        "household_id": household_id or None,
        "area_id": area_id,
        "notes": notes.strip(),
        "language": language,
        "category": category,
        "suggested_category": prediction.category,
        "suggested_confidence": prediction.confidence,
        "status": "new",
        "priority": priority,
        "created_by": created_by,
    }
    cols = ", ".join(row)
    conn.execute(f"INSERT INTO service_requests({cols}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))
    _event(conn, request_id, now, created_by, "created", "Logged at intake")
    _event(conn, request_id, now, created_by, "category_confirmed", _confirm_detail(category, prediction))
    db.audit(conn, actor_id=created_by, channel=CHANNEL, action="create", entity_table="service_requests", entity_id=request_id, after=row)
    return request_id


def _confirm_detail(category: str, prediction: classifier.Prediction) -> str:
    if prediction.category is None:
        return f"{category} (model below threshold, chosen by operator)"
    if prediction.category == category:
        return f"{category} (accepted model suggestion)"
    return f"{category} (changed from suggestion {prediction.category})"


def confirm_category(conn: sqlite3.Connection, model: Any, request_id: str, category: str | None, staff_id: str) -> classifier.Prediction:
    """Confirm the category of a request that has none; stores the suggestion if it was never computed."""
    _require_role(conn, staff_id, INTAKE_ROLES, "confirms the category")
    row = get_request(conn, request_id)
    if row["status"] == "closed":
        raise RequestError(f"{request_id} is closed.")
    if row["category"]:
        raise RequestError(f"{request_id} already has the confirmed category {row['category']}.")
    category = _check_category(category)
    prediction, stored = stored_or_computed_suggestion(conn, model, row)
    changes: dict[str, Any] = {"category": category}
    if not stored:
        changes.update(suggested_category=prediction.category, suggested_confidence=prediction.confidence)
    _update(conn, request_id, changes)
    _event(conn, request_id, clock.iso(), staff_id, "category_confirmed", _confirm_detail(category, prediction))
    db.audit(
        conn,
        actor_id=staff_id,
        channel=CHANNEL,
        action="confirm_category",
        entity_table="service_requests",
        entity_id=request_id,
        before={k: row[k] for k in changes},
        after=changes,
    )
    return prediction


def assign(conn: sqlite3.Connection, request_id: str, caseworker_id: str, staff_id: str) -> None:
    """new -> assigned. The category must be confirmed first; the assignee must be a caseworker."""
    _require_role(conn, staff_id, CASE_ROLES, "assigns the request")
    row = get_request(conn, request_id)
    _require_status(row, "new", "assign")
    if not row["category"]:
        raise RequestError(f"Confirm the category of {request_id} before assigning it.")
    _require_role(conn, caseworker_id, ("caseworker",), "is assigned")
    now = clock.iso()
    changes = {"status": "assigned", "assigned_to": caseworker_id, "assigned_at": now}
    _update(conn, request_id, changes)
    _event(conn, request_id, now, staff_id, "assigned", caseworker_id)
    db.audit(
        conn,
        actor_id=staff_id,
        channel=CHANNEL,
        action="assign",
        entity_table="service_requests",
        entity_id=request_id,
        before={k: row[k] for k in changes},
        after=changes,
    )


def schedule_callback(conn: sqlite3.Connection, request_id: str, staff_id: str, due_at: datetime | None = None) -> datetime:
    """assigned -> callback_scheduled. Due defaults to assigned + SLA hours; an earlier time may be chosen."""
    _require_role(conn, staff_id, CASE_ROLES, "schedules the callback")
    row = get_request(conn, request_id)
    _require_status(row, "assigned", "schedule a callback for")
    assigned_at = clock.parse(row["assigned_at"])
    latest = default_callback_due(assigned_at, sla_hours(conn))
    due = (due_at or latest).replace(microsecond=0)
    if due > latest:
        raise RequestError(f"The callback is due by {clock.iso(latest)} (assigned + {sla_hours(conn):g} h); choose that time or earlier.")
    if due < assigned_at:
        raise RequestError("The callback due time cannot be before the assignment time.")
    changes = {"status": "callback_scheduled", "callback_due_at": clock.iso(due)}
    _update(conn, request_id, changes)
    _event(conn, request_id, clock.iso(), staff_id, "callback_scheduled", f"Callback due {clock.iso(due)}")
    db.audit(
        conn,
        actor_id=staff_id,
        channel=CHANNEL,
        action="schedule_callback",
        entity_table="service_requests",
        entity_id=request_id,
        before={k: row[k] for k in changes},
        after=changes,
    )
    return due


def record_callback_done(conn: sqlite3.Connection, request_id: str, staff_id: str, note: str | None = None) -> None:
    """callback_scheduled -> in_progress, callback_done_at = now."""
    _require_role(conn, staff_id, CASE_ROLES, "made the callback")
    row = get_request(conn, request_id)
    _require_status(row, "callback_scheduled", "record the callback for")
    now = clock.iso()
    changes = {"status": "in_progress", "callback_done_at": now}
    _update(conn, request_id, changes)
    _event(conn, request_id, now, staff_id, "callback_done", (note or "").strip() or "Callback completed")
    db.audit(
        conn,
        actor_id=staff_id,
        channel=CHANNEL,
        action="callback_done",
        entity_table="service_requests",
        entity_id=request_id,
        before={k: row[k] for k in changes},
        after=changes,
        reason=note or None,
    )


def close(conn: sqlite3.Connection, request_id: str, outcome: str, confirmed_by: str | None) -> None:
    """in_progress -> closed with an outcome. Consequential: needs a caseworker or coordinator confirmation."""
    staff_id = confirm.require_confirmation(conn, confirmed_by, CLOSE_ROLES)
    row = get_request(conn, request_id)
    _require_status(row, "in_progress", "close")
    if outcome not in OUTCOMES:
        raise RequestError(f"Closure outcome must be one of {', '.join(OUTCOMES)}.")
    now = clock.iso()
    changes = {"status": "closed", "closed_at": now, "closure_outcome": outcome}
    _update(conn, request_id, changes)
    _event(conn, request_id, now, staff_id, "closed", outcome)
    db.audit(
        conn,
        actor_id=staff_id,
        channel=CHANNEL,
        action="close",
        entity_table="service_requests",
        entity_id=request_id,
        before={k: row[k] for k in changes},
        after=changes,
    )
