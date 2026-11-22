"""Project 12: parcel reconciliation for one site, one item and one period.

Four figures are read directly from their own tables (never from stock_movements,
which is the ledger of Project 10 and also records a return as a receipt):

    planned      distribution_plans.planned_quantity (not cancelled), planned_date in period
    dispatched   dispatches.quantity to the site for the item, date(dispatched_at) in period
    distributed  distributions.parcel_quantity at the site for the item, distribution_date in period
    returned     distribution_returns.quantity at the site for the item, date(returned_at) in period

    unaccounted = dispatched - distributed - returned

Parcels only. Ready-to-eat meals are a different unit and are never part of this.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from shared import clock, confirm, db

CHANNEL = "app:12"
PARCEL_CATEGORY = "food_parcel"
TYPE_BY_KIND = {"unaccounted": "unaccounted", "duplicate": "duplicate_household", "unmatched": "unmatched_dispatch"}
APPROVER_ROLES = ("coordinator",)


# -- reference lists ---------------------------------------------------------
def parcel_items(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Parcel items (unit 'parcel'), standard first. Meals and kits are excluded."""
    rows = conn.execute(
        "SELECT item_id, name, unit FROM items WHERE category = ? AND unit = 'parcel' ORDER BY item_id DESC", (PARCEL_CATEGORY,)
    ).fetchall()
    return [dict(r) for r in rows]


def sites(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT site_id, name FROM sites ORDER BY site_id").fetchall()]


def default_scenario(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """The seeded walkthrough scenario (site, item, period), or {} if absent."""
    raw = db.get_setting("demo.scenarios", conn=conn)
    return json.loads(raw).get("p12", {}) if raw else {}


# -- the four figures --------------------------------------------------------
def _check(conn: sqlite3.Connection, item_id: str, start: str, end: str) -> None:
    if start > end:
        raise ValueError("The period start must not be after the period end.")
    row = conn.execute("SELECT category, unit FROM items WHERE item_id = ?", (item_id,)).fetchone()
    if row is None or row["unit"] != "parcel":
        raise ValueError(f"{item_id} is not a parcel item; only parcels are reconciled.")


def figures(conn: sqlite3.Connection, site_id: str, item_id: str, start: str, end: str) -> dict[str, int]:
    """Planned, dispatched, distributed, returned (parcels) and the derived gaps."""
    _check(conn, item_id, start, end)
    planned = conn.execute(
        """SELECT COALESCE(SUM(planned_quantity), 0) FROM distribution_plans
           WHERE site_id = ? AND item_id = ? AND planned_date BETWEEN ? AND ? AND status != 'cancelled'""",
        (site_id, item_id, start, end),
    ).fetchone()[0]
    dispatched = conn.execute(
        """SELECT COALESCE(SUM(quantity), 0) FROM dispatches
           WHERE site_id = ? AND item_id = ? AND date(dispatched_at) BETWEEN ? AND ?""",
        (site_id, item_id, start, end),
    ).fetchone()[0]
    distributed = conn.execute(
        """SELECT COALESCE(SUM(parcel_quantity), 0) FROM distributions
           WHERE site_id = ? AND parcel_item_id = ? AND distribution_date BETWEEN ? AND ?""",
        (site_id, item_id, start, end),
    ).fetchone()[0]
    returned = conn.execute(
        """SELECT COALESCE(SUM(quantity), 0) FROM distribution_returns
           WHERE site_id = ? AND item_id = ? AND date(returned_at) BETWEEN ? AND ?""",
        (site_id, item_id, start, end),
    ).fetchone()[0]
    return {
        "planned": int(planned),
        "dispatched": int(dispatched),
        "distributed": int(distributed),
        "returned": int(returned),
        "unaccounted": int(dispatched - distributed - returned),
        "not_dispatched_against_plan": int(planned - dispatched),
    }


def explain(item_id: str, fig: dict[str, int]) -> str:
    """The calculation in words, with the actual numbers."""
    u = fig["unaccounted"]
    formula = f"unaccounted = dispatched - distributed - returned = {fig['dispatched']} - {fig['distributed']} - {fig['returned']} = {u}"
    if u > 0:
        meaning = f"{u} parcel(s) left the warehouse and are not recorded as handed out or returned."
    elif u < 0:
        meaning = f"{-u} more parcel(s) were recorded as handed out or returned than were dispatched (negative means over-recorded)."
    else:
        meaning = "Every dispatched parcel is accounted for."
    gap = fig["not_dispatched_against_plan"]
    plan = f"Planned {fig['planned']} minus dispatched {fig['dispatched']} = {gap} parcel(s) not dispatched against plan (shown separately; not part of unaccounted)."
    return f"{formula} ({item_id}, parcels). {meaning} {plan}"


# -- discrepancies -----------------------------------------------------------
def duplicate_households(conn: sqlite3.Connection, site_id: str, item_id: str, start: str, end: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT household_id, COUNT(*) AS n, SUM(parcel_quantity) AS parcels,
                  GROUP_CONCAT(distribution_id, ', ') AS distribution_ids
           FROM (SELECT * FROM distributions
                 WHERE site_id = ? AND parcel_item_id = ? AND distribution_date BETWEEN ? AND ?
                 ORDER BY distribution_id)
           GROUP BY household_id HAVING COUNT(*) > 1 ORDER BY household_id""",
        (site_id, item_id, start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def unmatched_records(conn: sqlite3.Connection, site_id: str, item_id: str, start: str, end: str) -> list[dict[str, Any]]:
    """Distribution rows with no dispatch, or a dispatch for another site or item."""
    rows = conn.execute(
        """SELECT d.distribution_id, d.household_id, d.parcel_quantity, d.distribution_date, d.dispatch_id,
                  x.site_id AS dispatch_site, x.item_id AS dispatch_item
           FROM distributions d LEFT JOIN dispatches x ON x.dispatch_id = d.dispatch_id
           WHERE d.site_id = ? AND d.parcel_item_id = ? AND d.distribution_date BETWEEN ? AND ?
             AND (d.dispatch_id IS NULL OR x.site_id != d.site_id OR x.item_id != d.parcel_item_id)
           ORDER BY d.distribution_id""",
        (site_id, item_id, start, end),
    ).fetchall()
    out = []
    for r in rows:
        rec = dict(r)
        rec["reason"] = (
            "no dispatch recorded"
            if rec["dispatch_id"] is None
            else f"dispatch {rec['dispatch_id']} is for {rec['dispatch_site']} / {rec['dispatch_item']}"
        )
        out.append(rec)
    return out


def latest_notes(conn: sqlite3.Connection, site_id: str, start: str, end: str) -> dict[str, dict[str, Any]]:
    """Latest note per discrepancy key (the latest decides whether it is resolved)."""
    rows = conn.execute(
        "SELECT * FROM reconciliation_notes WHERE site_id = ? AND period_start = ? AND period_end = ? ORDER BY note_id",
        (site_id, start, end),
    ).fetchall()
    return {r["discrepancy_key"]: dict(r) for r in rows}


def discrepancies(conn: sqlite3.Connection, site_id: str, item_id: str, start: str, end: str) -> list[dict[str, Any]]:
    """Every discrepancy with its description and latest note."""
    fig = figures(conn, site_id, item_id, start, end)
    notes = latest_notes(conn, site_id, start, end)
    items: list[dict[str, Any]] = []
    if fig["unaccounted"] != 0:
        items.append(
            {
                "key": f"unaccounted:{item_id}",
                "kind": "unaccounted",
                "description": f"{fig['unaccounted']} parcel(s) unaccounted for ({item_id}).",
            }
        )
    for d in duplicate_households(conn, site_id, item_id, start, end):
        items.append(
            {
                "key": f"duplicate:{d['household_id']}",
                "kind": "duplicate",
                "description": f"Household {d['household_id']} recorded {d['n']} times ({d['distribution_ids']}), {d['parcels']} parcel(s) in total.",
            }
        )
    for u in unmatched_records(conn, site_id, item_id, start, end):
        items.append(
            {
                "key": f"unmatched:{u['distribution_id']}",
                "kind": "unmatched",
                "description": f"Distribution {u['distribution_id']} (household {u['household_id']}, {u['parcel_quantity']} parcel(s)) does not match a dispatch: {u['reason']}.",
            }
        )
    for it in items:
        note = notes.get(it["key"])
        it["type"] = TYPE_BY_KIND[it["kind"]]
        it["note"] = note["note"] if note else None
        it["note_staff"] = note["staff_id"] if note else None
        it["note_at"] = note["at"] if note else None
        it["resolved"] = bool(note and note["resolved"])
    return items


def add_note(
    conn: sqlite3.Connection, site_id: str, start: str, end: str, key: str, kind: str, note: str, resolved: bool, staff_id: str
) -> int:
    """Record a resolution note for one discrepancy (audited). The latest note per key decides resolved."""
    if kind not in TYPE_BY_KIND.values():
        raise ValueError(f"Unknown discrepancy type: {kind}")
    text = (note or "").strip()
    if not text:
        raise ValueError("A resolution note cannot be empty.")
    confirm.require_confirmation(conn, staff_id)  # any active staff member; marking an item resolved is a recorded decision
    cur = conn.execute(
        """INSERT INTO reconciliation_notes(site_id, period_start, period_end, discrepancy_key, discrepancy_type,
                                            note, resolved, staff_id, at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (site_id, start, end, key, kind, text, 1 if resolved else 0, staff_id, clock.iso()),
    )
    note_id = int(cur.lastrowid or 0)
    db.audit(
        conn,
        actor_id=staff_id,
        channel=CHANNEL,
        action="reconciliation_note_added",
        entity_table="reconciliation_notes",
        entity_id=str(note_id),
        after={"site_id": site_id, "period": [start, end], "key": key, "type": kind, "resolved": bool(resolved), "note": text},
        reason=f"Resolution note for {key}",
    )
    return note_id


# -- report ------------------------------------------------------------------
def build_markdown(conn: sqlite3.Connection, site_id: str, item_id: str, start: str, end: str, approved_by: str, approved_at: str) -> str:
    fig = figures(conn, site_id, item_id, start, end)
    site = conn.execute("SELECT name FROM sites WHERE site_id = ?", (site_id,)).fetchone()
    item = conn.execute("SELECT name FROM items WHERE item_id = ?", (item_id,)).fetchone()
    disc = discrepancies(conn, site_id, item_id, start, end)
    lines = [
        f"# Distribution reconciliation: {site_id}",
        "",
        f"- Site: {site_id}" + (f" ({site['name']})" if site else ""),
        f"- Period: {start} to {end} (inclusive)",
        f"- Item: {item_id}" + (f" ({item['name']})" if item else "") + ", counted in parcels",
        f"- Approved by: {approved_by} at {approved_at}",
        "",
        "Parcels only. Ready-to-eat meals are a different unit and are not part of this reconciliation.",
        "",
        "## Figures (parcels)",
        "",
        "| Figure | Parcels |",
        "|---|---:|",
        f"| Planned | {fig['planned']} |",
        f"| Dispatched from warehouse | {fig['dispatched']} |",
        f"| Distributed to households | {fig['distributed']} |",
        f"| Returned | {fig['returned']} |",
        f"| **Unaccounted for** | **{fig['unaccounted']}** |",
        f"| Not dispatched against plan (planned - dispatched; separate) | {fig['not_dispatched_against_plan']} |",
        "",
        "## Calculation",
        "",
        explain(item_id, fig),
        "",
        "## Discrepancies",
        "",
    ]
    if not disc:
        lines.append("None.")
    for d in disc:
        status = "resolved" if d["resolved"] else "unresolved"
        lines.append(f"- `{d['key']}` ({d['type']}, {status}): {d['description']}")
        if d["note"]:
            lines.append(f"  - Latest note ({d['note_staff']}, {d['note_at']}): {d['note']}")
        else:
            lines.append("  - No note recorded.")
    lines += ["", "## Unresolved items", ""]
    unresolved = [d for d in disc if not d["resolved"]]
    if not unresolved:
        lines.append("None")
    for d in unresolved:
        lines.append(f"- `{d['key']}`: {d['description']}")
    return "\n".join(lines) + "\n"


def approve_report(conn: sqlite3.Connection, site_id: str, item_id: str, start: str, end: str, confirmed_by: str | None) -> str:
    """Sign off the report (coordinator confirmation required). Returns the report ID."""
    staff_id = confirm.require_confirmation(conn, confirmed_by, allowed_roles=APPROVER_ROLES)
    fig = figures(conn, site_id, item_id, start, end)
    disc = discrepancies(conn, site_id, item_id, start, end)
    at = clock.iso()
    markdown = build_markdown(conn, site_id, item_id, start, end, staff_id, at)
    summary = {
        "site_id": site_id,
        "item_id": item_id,
        "period_start": start,
        "period_end": end,
        **fig,
        "discrepancies": [{"key": d["key"], "type": d["type"], "resolved": d["resolved"], "note": d["note"]} for d in disc],
        "unresolved_keys": [d["key"] for d in disc if not d["resolved"]],
    }
    report_id = db.next_id(conn, "reconciliation_reports", "report_id", "RCN-", 4)
    conn.execute(
        """INSERT INTO reconciliation_reports(report_id, site_id, period_start, period_end, summary_json, markdown,
                                              approved_by, approved_at) VALUES (?,?,?,?,?,?,?,?)""",
        (report_id, site_id, start, end, json.dumps(summary, ensure_ascii=False), markdown, staff_id, at),
    )
    db.audit(
        conn,
        actor_id=staff_id,
        channel=CHANNEL,
        action="reconciliation_report_approved",
        entity_table="reconciliation_reports",
        entity_id=report_id,
        after={
            "site_id": site_id,
            "item_id": item_id,
            "period": [start, end],
            "unaccounted": fig["unaccounted"],
            "unresolved": summary["unresolved_keys"],
        },
        reason="Reconciliation sign-off",
    )
    return report_id


def earlier_reports(conn: sqlite3.Connection, site_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT report_id, period_start, period_end, approved_by, approved_at, summary_json, markdown "
        "FROM reconciliation_reports WHERE site_id = ? ORDER BY report_id DESC",
        (site_id,),
    ).fetchall()
    return [dict(r) for r in rows]
