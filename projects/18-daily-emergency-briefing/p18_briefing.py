"""Project 18: daily emergency briefing.

Every figure is a plain calculation over the records of one day (or the current stock),
and is stored in `briefing_sources` with its source table and record IDs. A conflict is a
`households_reached` figure in a field update that differs from the number of distinct
households in `distributions` for the same site and day. The coordinator reviews every
conflict before the briefing can be approved; approval needs a staff confirmation.

Parcels, ready-to-eat meals and hygiene kits are separate figures in separate units and
are never added together.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

from shared import clock, confirm, db

CHANNEL = "app:18"
APPROVER_ROLES = ("coordinator",)
EDITOR_ROLES = ("coordinator",)
DECISIONS = {
    "use_records": "Records are right; use the figure computed from the records",
    "records_to_correct": "Field report is right; the records need correcting (follow-up)",
    "follow_up": "Cannot decide yet; leave open as a follow-up",
}
RESOLVED_DECISIONS = ("use_records",)
COMPARABLE_KEY = "households_reached"
LIST_LIMIT = 6  # IDs shown inline in the briefing body; the appendix lists them all
UPDATE_LIMIT = 5  # most recent field updates listed in the body; the rest stay in field_updates
UPDATE_TEXT_CHARS = 160  # longer update text is cut in the body (the full text stays in the record)


class BriefingError(Exception):
    """A briefing action that is not allowed in the current state."""


# -- figures -----------------------------------------------------------------
def _fig(key: str, label: str, value: int, unit: str, table: str, ids: list[str] | str) -> dict[str, Any]:
    text = ids if isinstance(ids, str) else ",".join(ids)
    return {"key": key, "label": label, "value": value, "unit": unit, "source_table": table, "source_record_ids": text}


def _day(conn: sqlite3.Connection, sql: str, day: str, *extra: Any) -> list[sqlite3.Row]:
    return conn.execute(sql, (day, *extra)).fetchall()


def compute_figures(conn: sqlite3.Connection, day: str) -> list[dict[str, Any]]:
    """The briefing figures for one day, each with its source table and record IDs."""
    calls = _day(conn, "SELECT call_id, is_repeat FROM calls WHERE date(received_at) = ? ORDER BY call_id", day)
    complaints = _day(conn, "SELECT complaint_id FROM complaints WHERE date(opened_at) = ? ORDER BY complaint_id", day)
    requests = _day(conn, "SELECT request_id FROM service_requests WHERE date(created_at) = ? ORDER BY request_id", day)
    dist = _day(
        conn,
        "SELECT distribution_id, household_id, parcel_quantity, rte_meal_quantity FROM distributions WHERE distribution_date = ? ORDER BY distribution_id",
        day,
    )
    plans = _day(
        conn,
        """SELECT p.plan_id, p.planned_quantity FROM distribution_plans p JOIN items i ON i.item_id = p.item_id
           WHERE p.planned_date = ? AND p.status <> 'cancelled' AND i.category = 'food_parcel' ORDER BY p.plan_id""",
        day,
    )
    stock = {
        cat: conn.execute(
            """SELECT s.warehouse_id, s.item_id, s.on_hand FROM v_stock_on_hand s JOIN items i ON i.item_id = s.item_id
               WHERE i.category = ? ORDER BY s.warehouse_id, s.item_id""",
            (cat,),
        ).fetchall()
        for cat in ("food_parcel", "rte_meal", "hygiene")
    }

    def stock_ids(rows: list[sqlite3.Row]) -> str:
        return "v_stock_on_hand: " + ",".join(f"{r['warehouse_id']}/{r['item_id']}" for r in rows)

    return [
        _fig("calls_received", "Calls received", len(calls), "calls", "calls", [r["call_id"] for r in calls]),
        _fig("calls_repeat", "of which repeat calls (already logged)", sum(r["is_repeat"] for r in calls), "calls", "calls", [r["call_id"] for r in calls if r["is_repeat"]]),
        _fig("complaints_opened", "Complaints opened", len(complaints), "complaints", "complaints", [r["complaint_id"] for r in complaints]),
        _fig("requests_created", "Service requests created", len(requests), "requests", "service_requests", [r["request_id"] for r in requests]),
        _fig("households_reached", "Households reached (all sites)", len({r["household_id"] for r in dist}), "households", "distributions", [r["distribution_id"] for r in dist]),
        _fig("parcels_distributed", "Food parcels distributed", sum(r["parcel_quantity"] for r in dist), "parcels", "distributions", [r["distribution_id"] for r in dist if r["parcel_quantity"]]),
        _fig("meals_distributed", "Ready-to-eat meals distributed", sum(r["rte_meal_quantity"] for r in dist), "meals", "distributions", [r["distribution_id"] for r in dist if r["rte_meal_quantity"]]),
        _fig("parcels_planned", "Food parcels planned for the day", sum(r["planned_quantity"] for r in plans), "parcels", "distribution_plans", [r["plan_id"] for r in plans]),
        _fig("stock_parcels", "Food parcels on hand (all warehouses, now)", sum(r["on_hand"] for r in stock["food_parcel"]), "parcels", "v_stock_on_hand", stock_ids(stock["food_parcel"])),
        _fig("stock_meals", "Ready-to-eat meals on hand (all warehouses, now)", sum(r["on_hand"] for r in stock["rte_meal"]), "meals", "v_stock_on_hand", stock_ids(stock["rte_meal"])),
        _fig("stock_hygiene_kits", "Hygiene kits on hand (all warehouses, now)", sum(r["on_hand"] for r in stock["hygiene"]), "kits", "v_stock_on_hand", stock_ids(stock["hygiene"])),
    ]  # fmt: skip


def find_conflicts(conn: sqlite3.Connection, day: str) -> list[dict[str, Any]]:
    """Field-reported households_reached figures that differ from the records for the same site and day."""
    conflicts: list[dict[str, Any]] = []
    updates = conn.execute(
        "SELECT * FROM field_updates WHERE figure_key = ? AND site_id IS NOT NULL AND date(reported_at) = ? ORDER BY update_id",
        (COMPARABLE_KEY, day),
    ).fetchall()
    for u in updates:
        recs = conn.execute(
            "SELECT distribution_id, household_id FROM distributions WHERE site_id = ? AND distribution_date = ? ORDER BY distribution_id",
            (u["site_id"], day),
        ).fetchall()
        computed = len({r["household_id"] for r in recs})
        if int(u["figure_value"]) != computed:
            conflicts.append(
                {
                    "update_id": u["update_id"],
                    "site_id": u["site_id"],
                    "team": u["team"],
                    "figure_key": COMPARABLE_KEY,
                    "unit": "households",
                    "reported": int(u["figure_value"]),
                    "computed": computed,
                    "difference": int(u["figure_value"]) - computed,
                    "update_text": u["text"],
                    "record_ids": [r["distribution_id"] for r in recs],
                    "decision": None,
                    "note": "",
                    "reviewed_by": None,
                    "reviewed_at": None,
                }
            )
    return conflicts


def field_updates_for(conn: sqlite3.Connection, day: str) -> list[dict[str, Any]]:
    return db.query_all(
        "SELECT update_id, reported_at, team, site_id, area_id, text, figure_key, figure_value FROM field_updates WHERE date(reported_at) = ? ORDER BY reported_at",
        [day],
    )


# -- rendering ---------------------------------------------------------------
def _ids_short(fig: dict[str, Any]) -> str:
    ids = [i for i in fig["source_record_ids"].removeprefix("v_stock_on_hand: ").split(",") if i]
    if len(ids) <= LIST_LIMIT:
        text = ", ".join(ids) if ids else "no records that day"
        return text
    return ", ".join(ids[:LIST_LIMIT]) + f" ... (+{len(ids) - LIST_LIMIT} more, see appendix)"


def render_body(day: str, figures: list[dict[str, Any]], updates: list[dict[str, Any]], conflicts: list[dict[str, Any]]) -> str:
    lines = [f"# Daily emergency briefing, {day}", "", "## Figures", "", "| Figure | Value | Unit | Source records |", "|---|---:|---|---|"]
    for f in figures:
        lines.append(f"| {f['label']} | {f['value']} | {f['unit']} | `{f['source_table']}`: {_ids_short(f)} |")
    lines += [
        "",
        "Parcels, meals and kits are separate figures in separate units and are never added together.",
        "",
        "## Field updates",
        "",
    ]
    if updates:
        shown = sorted(updates, key=lambda u: u["reported_at"], reverse=True)[:UPDATE_LIMIT]  # newest first
        for u in shown:
            fig = f" [{u['figure_key']} = {int(u['figure_value'])}]" if u["figure_key"] and u["figure_value"] is not None else ""
            text = u["text"] if len(u["text"]) <= UPDATE_TEXT_CHARS else u["text"][: UPDATE_TEXT_CHARS - 3].rstrip() + "..."
            lines.append(f"- {u['update_id']} ({u['team']}, {u['reported_at'][11:16]}): {text}{fig}")
        if len(updates) > len(shown):
            lines.append(f"- ... and {len(updates) - len(shown)} more in the source records (`field_updates`).")
    else:
        lines.append("- No field updates for this day.")
    lines += ["", "## Coordinator notes", ""]
    if conflicts:
        lines.append(f"{len(conflicts)} conflicting figure(s) between field updates and records; see the review section below.")
    else:
        lines.append("No conflicting totals.")
    return "\n".join(lines) + "\n"


def _conflict_lines(conflicts: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for c in conflicts:
        out.append(
            f"- {c['update_id']} at {c['site_id']}: field update says {c['reported']} {c['unit']}, "
            f"records give {c['computed']} (difference {c['difference']:+d}). "
            f"Decision: {DECISIONS.get(c['decision'], 'NOT REVIEWED')}."
            + (f" Note: {c['note']}" if c.get("note") else "")
            + f" Records: {', '.join(c['record_ids']) or 'none'}."
        )
    return out


def unresolved_items(briefing: dict[str, Any]) -> list[str]:
    return [
        line
        for c, line in zip(briefing["conflicts"], _conflict_lines(briefing["conflicts"]), strict=True)
        if c["decision"] not in RESOLVED_DECISIONS
    ]


# -- read --------------------------------------------------------------------
def get_briefing(conn: sqlite3.Connection, briefing_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM briefings WHERE briefing_id = ?", (briefing_id,)).fetchone()
    if row is None:
        raise BriefingError(f"No such briefing: {briefing_id}")
    b = dict(row)
    b["conflicts"] = json.loads(b["conflicts_json"])
    b["sources"] = [dict(r) for r in conn.execute("SELECT * FROM briefing_sources WHERE briefing_id = ? ORDER BY rowid", (briefing_id,))]
    return b


def list_briefings(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT briefing_id, briefing_date, status, created_by, approved_by, approved_at FROM briefings ORDER BY briefing_id DESC"
        )
    ]


def _needs_review(b: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in b["conflicts"] if c["decision"] is None]


# -- write -------------------------------------------------------------------
def create_draft(conn: sqlite3.Connection, day: str, created_by: str) -> str:
    """Compute the figures for `day`, flag conflicts, store a draft and its sources. Call inside db.transaction()."""
    date.fromisoformat(day)
    if conn.execute("SELECT 1 FROM staff WHERE staff_id = ? AND active = 1", (created_by,)).fetchone() is None:
        raise BriefingError(f"Unknown or inactive staff ID: {created_by}")
    figures = compute_figures(conn, day)
    conflicts = find_conflicts(conn, day)
    body = render_body(day, figures, field_updates_for(conn, day), conflicts)
    briefing_id = db.next_id(conn, "briefings", "briefing_id", "BRF-", 4)
    now = clock.iso()
    conn.execute(
        "INSERT INTO briefings(briefing_id, briefing_date, status, content_md, conflicts_json, created_at, created_by) VALUES (?,?,?,?,?,?,?)",
        (briefing_id, day, "draft", body, json.dumps(conflicts, ensure_ascii=False), now, created_by),
    )
    for f in figures:
        conn.execute(
            "INSERT INTO briefing_sources(briefing_id, figure_key, figure_value, unit, source_table, source_record_ids) VALUES (?,?,?,?,?,?)",
            (briefing_id, f["key"], f["value"], f["unit"], f["source_table"], f["source_record_ids"]),
        )
    for c in conflicts:  # the field-reported figure is a source too, so both sides of a conflict are linked
        conn.execute(
            "INSERT INTO briefing_sources(briefing_id, figure_key, figure_value, unit, source_table, source_record_ids) VALUES (?,?,?,?,?,?)",
            (briefing_id, f"reported_{c['figure_key']}_{c['site_id']}", c["reported"], c["unit"], "field_updates", c["update_id"]),
        )
    db.audit(
        conn,
        actor_id=created_by,
        channel=CHANNEL,
        action="create",
        entity_table="briefings",
        entity_id=briefing_id,
        after={"briefing_date": day, "figures": len(figures), "conflicts": len(conflicts)},
    )
    return briefing_id


def _draft(conn: sqlite3.Connection, briefing_id: str) -> dict[str, Any]:
    b = get_briefing(conn, briefing_id)
    if b["status"] != "draft":
        raise BriefingError(f"{briefing_id} is {b['status']} and can no longer be changed.")
    return b


def save_edit(conn: sqlite3.Connection, briefing_id: str, content_md: str, edited_by: str) -> None:
    b = _draft(conn, briefing_id)
    if not content_md.strip():
        raise BriefingError("The briefing text cannot be empty.")
    confirm.require_confirmation(conn, edited_by, EDITOR_ROLES)
    conn.execute("UPDATE briefings SET content_md = ? WHERE briefing_id = ?", (content_md, briefing_id))
    db.audit(
        conn,
        actor_id=edited_by,
        channel=CHANNEL,
        action="update",
        entity_table="briefings",
        entity_id=briefing_id,
        before={"chars": len(b["content_md"])},
        after={"chars": len(content_md)},
        reason="text edited",
    )


def review_conflict(conn: sqlite3.Connection, briefing_id: str, update_id: str, decision: str, note: str, reviewed_by: str) -> None:
    """Record the coordinator's decision on one conflict (re-reviewing replaces the earlier decision)."""
    b = _draft(conn, briefing_id)
    if decision not in DECISIONS:
        raise BriefingError(f"Unknown decision: {decision}")
    confirm.require_confirmation(conn, reviewed_by, EDITOR_ROLES)
    target = next((c for c in b["conflicts"] if c["update_id"] == update_id), None)
    if target is None:
        raise BriefingError(f"{update_id} is not a conflict in {briefing_id}")
    before = {k: target[k] for k in ("decision", "note")}
    target.update(decision=decision, note=note.strip(), reviewed_by=reviewed_by, reviewed_at=clock.iso())
    conn.execute(
        "UPDATE briefings SET conflicts_json = ? WHERE briefing_id = ?", (json.dumps(b["conflicts"], ensure_ascii=False), briefing_id)
    )
    db.audit(
        conn,
        actor_id=reviewed_by,
        channel=CHANNEL,
        action="resolve",
        entity_table="briefings",
        entity_id=briefing_id,
        before=before,
        after={"update_id": update_id, "decision": decision},
        reason=note.strip() or None,
    )


def approve(conn: sqlite3.Connection, briefing_id: str, confirmed_by: str | None) -> None:
    """Approve a draft. Needs staff confirmation and a reviewed decision for every conflict. Call inside db.transaction()."""
    confirm.require_confirmation(conn, confirmed_by, APPROVER_ROLES)
    b = _draft(conn, briefing_id)
    pending = _needs_review(b)
    if pending:
        raise BriefingError("Review every conflict before approval. Not reviewed: " + ", ".join(c["update_id"] for c in pending))
    now = clock.iso()
    conn.execute(
        "UPDATE briefings SET status = 'approved', approved_by = ?, approved_at = ? WHERE briefing_id = ?", (confirmed_by, now, briefing_id)
    )
    db.audit(
        conn,
        actor_id=str(confirmed_by),
        channel=CHANNEL,
        action="approve",
        entity_table="briefings",
        entity_id=briefing_id,
        before={"status": "draft"},
        after={"status": "approved", "unresolved": len(unresolved_items(b))},
    )


# -- export ------------------------------------------------------------------
def export_markdown(conn: sqlite3.Connection, briefing_id: str) -> str:
    """The approved briefing as Markdown: body, conflict review, unresolved items, source appendix."""
    b = get_briefing(conn, briefing_id)
    if b["status"] != "approved":
        raise BriefingError(f"{briefing_id} is not approved; only an approved briefing can be exported.")
    out = [b["content_md"].rstrip(), "", "## Conflict review", ""]
    out += _conflict_lines(b["conflicts"]) or ["- No conflicts were found."]
    out += ["", "## Unresolved items", ""]
    out += unresolved_items(b) or ["- None."]
    out += ["", "## Source records (appendix)", "", "| Figure | Value | Unit | Source table | Record IDs |", "|---|---:|---|---|---|"]
    for s in b["sources"]:
        value = int(s["figure_value"]) if float(s["figure_value"]).is_integer() else s["figure_value"]
        out.append(f"| {s['figure_key']} | {value} | {s['unit']} | `{s['source_table']}` | {s['source_record_ids'] or 'none'} |")
    out += ["", f"Approved by {b['approved_by']} at {b['approved_at']}. Briefing {briefing_id}.", ""]
    return "\n".join(out)


def default_day(conn: sqlite3.Connection) -> str:
    raw = db.get_setting("demo.scenarios", conn=conn)
    if raw:
        scenario = json.loads(raw).get("p18", {})
        if scenario.get("conflict_date"):
            return scenario["conflict_date"]
    return (clock.today() - timedelta(days=1)).isoformat()
