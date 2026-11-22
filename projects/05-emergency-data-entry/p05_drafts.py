"""Draft intake forms: create from a pasted note, list, save (confirmed) and discard (confirmed)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import p05_extract as ex

from shared import clock, db
from shared.call_notes import CATEGORIES
from shared.confirm import require_confirmation
from shared.sync import next_record_id

CHANNEL = "app:05"
ALLOWED_ROLES: tuple[str, ...] = ("operator", "caseworker", "coordinator")
SNAPSHOT_KEYS: tuple[str, ...] = ("head_name", "area_id", "household_size", "primary_need", "urgency", "notes")


class DraftError(Exception):
    """A draft cannot be saved or changed (unknown, not open, or a required field is empty)."""


def load_areas(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT area_id, name_en, name_ar FROM areas ORDER BY area_id")]


def known_household_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT household_id FROM households")}


def extract_note(conn: sqlite3.Connection, note: str) -> ex.Extraction:
    """Run the extraction against the areas table and household register (writes nothing)."""
    return ex.extract(note, load_areas(conn), known_household_ids(conn))


def create_draft(conn: sqlite3.Connection, note: str, created_by: str) -> str:
    """Extract the note and store it as a draft (status 'draft'). Returns the DRF- ID."""
    if not note or not note.strip():
        raise DraftError("Paste or load a field note first.")
    if not conn.execute("SELECT 1 FROM staff WHERE staff_id = ? AND active = 1", (created_by,)).fetchone():
        raise DraftError(f"Unknown or inactive staff ID: {created_by}")
    result = extract_note(conn, note)
    draft_id = db.next_id(conn, "intake_drafts", "draft_id", "DRF-", 4)
    conn.execute(
        "INSERT INTO intake_drafts(draft_id, created_at, created_by, raw_note, language, extracted_json, flags_json, status) VALUES (?,?,?,?,?,?,?, 'draft')",
        (
            draft_id,
            clock.iso(),
            created_by,
            note,
            result.language,
            json.dumps(result.fields, ensure_ascii=False),
            json.dumps(result.flags(), ensure_ascii=False),
        ),
    )
    db.audit(
        conn,
        actor_id=created_by,
        channel=CHANNEL,
        action="draft_created",
        entity_table="intake_drafts",
        entity_id=draft_id,
        after={"extracted": result.fields, "missing": result.missing, "uncertain": result.uncertain},
    )
    return draft_id


def get_draft(conn: sqlite3.Connection, draft_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM intake_drafts WHERE draft_id = ?", (draft_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["extracted"] = json.loads(out["extracted_json"])
    out["flags"] = json.loads(out["flags_json"])
    return out


def open_drafts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT draft_id FROM intake_drafts WHERE status = 'draft' ORDER BY draft_id DESC").fetchall()
    return [d for r in rows if (d := get_draft(conn, r[0])) is not None]


def validate_fields(conn: sqlite3.Connection, fields: dict[str, Any]) -> dict[str, Any]:
    """Clean the staff-corrected fields; raise DraftError naming every problem."""
    problems: list[str] = []
    out: dict[str, Any] = {}
    name = str(fields.get("head_name") or "").strip()
    if not name:
        problems.append("head_name is empty")
    out["head_name"] = name
    area_id = str(fields.get("area_id") or "").strip()
    if not area_id:
        problems.append("area_id is empty")
    elif not conn.execute("SELECT 1 FROM areas WHERE area_id = ?", (area_id,)).fetchone():
        problems.append(f"area_id {area_id} is not in the area list")
    out["area_id"] = area_id
    try:
        size = int(fields.get("household_size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size < 1:
        problems.append("household_size is empty")
    out["household_size"] = size
    need = str(fields.get("primary_need") or "").strip()
    if not need:
        problems.append("primary_need is empty")
    elif need not in CATEGORIES:
        problems.append(f"primary_need {need} is not one of {', '.join(CATEGORIES)}")
    out["primary_need"] = need
    urgency = str(fields.get("urgency") or "normal").strip()
    if urgency not in ex.URGENCY_LEVELS:
        problems.append(f"urgency {urgency} is not one of {', '.join(ex.URGENCY_LEVELS)}")
    out["urgency"] = urgency
    out["notes"] = str(fields.get("notes") or "").strip() or None
    hh = str(fields.get("household_id") or "").strip().upper()
    if hh and not conn.execute("SELECT 1 FROM households WHERE household_id = ?", (hh,)).fetchone():
        problems.append(f"household_id {hh} is not in the household register (correct it or clear it)")
    out["household_id"] = hh or None
    if problems:
        raise DraftError("Cannot save: " + "; ".join(problems) + ".")
    return out


def save_draft(conn: sqlite3.Connection, draft_id: str, fields: dict[str, Any], confirmed_by: str | None) -> str:
    """Save the staff-corrected fields as intake record version 1. Returns the INT- record ID."""
    staff = require_confirmation(conn, confirmed_by, ALLOWED_ROLES)
    draft = get_draft(conn, draft_id)
    if draft is None:
        raise DraftError(f"Draft {draft_id} does not exist.")
    if draft["status"] != "draft":
        raise DraftError(f"Draft {draft_id} is already {draft['status']}.")
    clean = validate_fields(conn, fields)
    record_id = next_record_id(conn)
    now = clock.iso()
    conn.execute(
        """INSERT INTO intake_records(record_id, source, household_id, head_name, area_id, household_size, primary_need,
               urgency, notes, raw_note, language, version, created_at, created_by, updated_at, updated_by)
           VALUES (?, 'data_entry', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
        (
            record_id,
            clean["household_id"],
            clean["head_name"],
            clean["area_id"],
            clean["household_size"],
            clean["primary_need"],
            clean["urgency"],
            clean["notes"],
            draft["raw_note"],
            draft["language"],
            now,
            staff,
            now,
            staff,
        ),
    )
    snapshot = {k: clean[k] for k in SNAPSHOT_KEYS}
    conn.execute(
        "INSERT INTO record_versions(record_id, version, data_json, origin, device_id, saved_at, saved_by) VALUES (?, 1, ?, 'data_entry', NULL, ?, ?)",
        (record_id, json.dumps(snapshot, ensure_ascii=False), now, staff),
    )
    conn.execute("UPDATE intake_drafts SET status = 'saved', saved_record_id = ? WHERE draft_id = ?", (record_id, draft_id))
    db.audit(
        conn,
        actor_id=staff,
        channel=CHANNEL,
        action="draft_saved",
        entity_table="intake_records",
        entity_id=record_id,
        before={"draft_id": draft_id, "extracted": draft["extracted"]},
        after={**snapshot, "household_id": clean["household_id"], "version": 1},
        reason=f"Saved from draft {draft_id} after staff review.",
    )
    return record_id


def discard_draft(conn: sqlite3.Connection, draft_id: str, confirmed_by: str | None) -> None:
    staff = require_confirmation(conn, confirmed_by, ALLOWED_ROLES)
    draft = get_draft(conn, draft_id)
    if draft is None or draft["status"] != "draft":
        raise DraftError(f"Draft {draft_id} is not an open draft.")
    conn.execute("UPDATE intake_drafts SET status = 'discarded' WHERE draft_id = ?", (draft_id,))
    db.audit(
        conn,
        actor_id=staff,
        channel=CHANNEL,
        action="draft_discarded",
        entity_table="intake_drafts",
        entity_id=draft_id,
        before={"status": "draft"},
        after={"status": "discarded"},
    )


def recent_drafts(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT draft_id, created_at, created_by, language, status, saved_record_id FROM intake_drafts ORDER BY draft_id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def recent_records(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT record_id, head_name, area_id, household_size, primary_need, urgency, language, version, created_by
           FROM intake_records WHERE source = 'data_entry' ORDER BY record_id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]
