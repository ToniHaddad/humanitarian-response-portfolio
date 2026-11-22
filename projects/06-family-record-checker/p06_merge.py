"""Project 06: staff decisions on potential duplicates (merge or not a duplicate) and their history.

A merge never deletes a household. The other record keeps its row with
status 'merged' and merged_into = the kept household; every child row that
points at it (found from the schema's foreign keys) is re-pointed to the kept
household and listed in merge_history.moved_records_json, together with full
snapshots of both rows, the field choices, the reason and the confirming staff.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import p06_matching as matching

from shared import clock, confirm, db

ROLES = ("caseworker", "coordinator")
CHANNEL = "app:06"


class MergeError(ValueError):
    """The request cannot be carried out (bad IDs, missing reason, invalid field choice)."""


def household_child_tables(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """(table, primary key column, household column) for every table with a household_id
    foreign key to households, except households and merge_history themselves."""
    out = []
    for table in db.table_names(conn):
        if table in ("households", "merge_history"):
            continue
        for fk in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall():
            if fk["table"] == "households" and fk["from"] == "household_id":
                pk = [c["name"] for c in conn.execute(f"PRAGMA table_info({table})").fetchall() if c["pk"]]
                out.append((table, pk[0] if pk else "rowid", "household_id"))
    return sorted(out)


def child_counts(conn: sqlite3.Connection, household_id: str) -> dict[str, int]:
    """Number of linked rows per child table for one household."""
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (household_id,)).fetchone()[0])
        for table, _pk, col in household_child_tables(conn)
    }


def _household(conn: sqlite3.Connection, household_id: str) -> dict[str, Any]:
    row = db.row_dict(conn, "households", "household_id", household_id)
    if row is None:
        raise MergeError(f"Unknown household {household_id}.")
    return row


def _check_pair(conn: sqlite3.Connection, a_id: str, b_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if a_id == b_id:
        raise MergeError("Choose two different households.")
    a, b = _household(conn, a_id), _household(conn, b_id)
    for h in (a, b):
        if h["status"] != "active":
            raise MergeError(f"{h['household_id']} is not active (status {h['status']}); it cannot be decided again.")
    if frozenset((a_id, b_id)) in matching.decided_pairs(conn):
        raise MergeError(f"A decision for {a_id} and {b_id} is already recorded.")
    return a, b


def _reason(reason: str | None) -> str:
    text = (reason or "").strip()
    if not text:
        raise MergeError("A reason is required.")
    return text


def merge_households(
    conn: sqlite3.Connection,
    kept_id: str,
    other_id: str,
    *,
    confirmed_by: str | None,
    field_choices: dict[str, str] | None,
    reason: str | None,
    match_score: float | None = None,
) -> dict[str, Any]:
    """Merge other_id into kept_id after staff confirmation. Returns the merge_history row.

    field_choices maps a field in matching.CHOOSABLE_FIELDS to the household ID whose
    value is kept (kept_id or other_id). Fields not listed keep the kept record's value.
    The chosen head_name is written in its cleaned form and name_script follows it.
    """
    staff = confirm.require_confirmation(conn, confirmed_by, ROLES)
    text = _reason(reason)
    kept, other = _check_pair(conn, kept_id, other_id)
    choices = dict(field_choices or {})
    for field, source in choices.items():
        if field not in matching.CHOOSABLE_FIELDS:
            raise MergeError(f"{field} cannot be chosen in a merge.")
        if source not in (kept_id, other_id):
            raise MergeError(f"The value for {field} must come from {kept_id} or {other_id}.")

    rows = {kept_id: kept, other_id: other}
    new_values: dict[str, Any] = {}
    for field in matching.CHOOSABLE_FIELDS:
        new_values[field] = rows[choices.get(field, kept_id)][field]
    new_values["head_name"] = matching.clean_name(new_values["head_name"])
    new_values["name_script"] = rows[choices.get("head_name", kept_id)]["name_script"]
    changed = {k: v for k, v in new_values.items() if kept[k] != v}
    if changed:
        sets = ", ".join(f"{k} = ?" for k in changed)
        conn.execute(f"UPDATE households SET {sets} WHERE household_id = ?", [*changed.values(), kept_id])

    moved: dict[str, list[Any]] = {}
    for table, pk, col in household_child_tables(conn):
        ids = [r[0] for r in conn.execute(f"SELECT {pk} FROM {table} WHERE {col} = ? ORDER BY {pk}", (other_id,))]
        if ids:
            conn.execute(f"UPDATE {table} SET {col} = ? WHERE {col} = ?", (kept_id, other_id))
            moved[table] = ids
    earlier = [r[0] for r in conn.execute("SELECT household_id FROM households WHERE merged_into = ? ORDER BY household_id", (other_id,))]
    if earlier:
        conn.execute("UPDATE households SET merged_into = ? WHERE merged_into = ?", (kept_id, other_id))
        moved["households.merged_into"] = earlier
    conn.execute("UPDATE households SET status = 'merged', merged_into = ? WHERE household_id = ?", (kept_id, other_id))

    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise RuntimeError(f"Merge would break {len(problems)} foreign key link(s); nothing was saved.")

    merge_id = db.next_id(conn, "merge_history", "merge_id", "MRG-", 4)
    now = clock.iso()
    record = {
        "merge_id": merge_id,
        "kept_household_id": kept_id,
        "other_household_id": other_id,
        "decision": "merged",
        "match_score": match_score,
        "kept_snapshot_json": json.dumps(kept, ensure_ascii=False),
        "other_snapshot_json": json.dumps(other, ensure_ascii=False),
        "field_choices_json": json.dumps(choices, ensure_ascii=False),
        "moved_records_json": json.dumps(moved, ensure_ascii=False),
        "reason": text,
        "decided_by": staff,
        "decided_at": now,
    }
    conn.execute(f"INSERT INTO merge_history({', '.join(record)}) VALUES ({', '.join('?' for _ in record)})", list(record.values()))
    moved_counts = {t: len(v) for t, v in moved.items()}
    db.audit(
        conn,
        actor_id=staff,
        channel=CHANNEL,
        action="merge",
        entity_table="households",
        entity_id=other_id,
        before={"status": other["status"], "merged_into": other["merged_into"]},
        after={"status": "merged", "merged_into": kept_id, "merge_id": merge_id, "moved_records": moved_counts},
        reason=text,
    )
    if changed:
        db.audit(
            conn,
            actor_id=staff,
            channel=CHANNEL,
            action="update",
            entity_table="households",
            entity_id=kept_id,
            before={k: kept[k] for k in changed},
            after=changed,
            reason=f"{merge_id}: values chosen in merge",
        )
    return record


def record_not_duplicate(
    conn: sqlite3.Connection,
    household_a: str,
    household_b: str,
    *,
    confirmed_by: str | None,
    reason: str | None,
    match_score: float | None = None,
) -> dict[str, Any]:
    """Record, after staff confirmation, that two flagged households are different families.
    Changes no household; the pair is never suggested again."""
    staff = confirm.require_confirmation(conn, confirmed_by, ROLES)
    text = _reason(reason)
    a, b = _check_pair(conn, household_a, household_b)
    merge_id = db.next_id(conn, "merge_history", "merge_id", "MRG-", 4)
    record = {
        "merge_id": merge_id,
        "kept_household_id": household_a,
        "other_household_id": household_b,
        "decision": "not_duplicate",
        "match_score": match_score,
        "kept_snapshot_json": json.dumps(a, ensure_ascii=False),
        "other_snapshot_json": json.dumps(b, ensure_ascii=False),
        "field_choices_json": None,
        "moved_records_json": None,
        "reason": text,
        "decided_by": staff,
        "decided_at": clock.iso(),
    }
    conn.execute(f"INSERT INTO merge_history({', '.join(record)}) VALUES ({', '.join('?' for _ in record)})", list(record.values()))
    db.audit(
        conn,
        actor_id=staff,
        channel=CHANNEL,
        action="not_duplicate",
        entity_table="merge_history",
        entity_id=merge_id,
        after={"household_a": household_a, "household_b": household_b},
        reason=text,
    )
    return record


def history(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Past decisions, newest first, with who, when, reason and how many records moved."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM merge_history ORDER BY decided_at DESC, merge_id DESC")]
    out = []
    for r in rows:
        moved = json.loads(r["moved_records_json"]) if r["moved_records_json"] else {}
        out.append(
            {
                "merge_id": r["merge_id"],
                "decided_at": r["decided_at"],
                "decision": "Merged" if r["decision"] == "merged" else "Not a duplicate",
                "kept_household_id": r["kept_household_id"],
                "other_household_id": r["other_household_id"],
                "match_score": r["match_score"],
                "decided_by": r["decided_by"],
                "reason": r["reason"],
                "records_moved": sum(len(v) for v in moved.values()),
                "moved_detail": ", ".join(f"{t}: {len(v)}" for t, v in moved.items()) or "-",
            }
        )
    return out
