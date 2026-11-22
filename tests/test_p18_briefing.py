"""Project 18: daily emergency briefing."""

from __future__ import annotations

import json
import sqlite3

import p18_briefing as brf
import pytest

from shared import db
from shared.confirm import ConfirmationRequired

COORD = "CRD-01"


@pytest.fixture
def scenario(conn: sqlite3.Connection) -> dict:
    return json.loads(db.get_setting("demo.scenarios", conn=conn))["p18"]


def _draft(conn: sqlite3.Connection, day: str) -> str:
    with conn:
        return brf.create_draft(conn, day, COORD)


def _review_all(conn: sqlite3.Connection, bid: str, decision: str = "use_records") -> None:
    for c in brf.get_briefing(conn, bid)["conflicts"]:
        with conn:
            brf.review_conflict(conn, bid, c["update_id"], decision, "checked", COORD)


def _on_hand(conn: sqlite3.Connection, category: str) -> int:
    sql = "SELECT SUM(s.on_hand) FROM v_stock_on_hand s JOIN items i ON i.item_id = s.item_id WHERE i.category = ?"
    return conn.execute(sql, (category,)).fetchone()[0]


def test_figures_match_the_records(conn: sqlite3.Connection, scenario: dict) -> None:
    day = scenario["conflict_date"]
    figs = {f["key"]: f for f in brf.compute_figures(conn, day)}
    assert (
        figs["calls_received"]["value"] == conn.execute("SELECT COUNT(*) FROM calls WHERE substr(received_at,1,10)=?", (day,)).fetchone()[0]
    )
    assert (
        figs["calls_repeat"]["value"]
        == conn.execute("SELECT COUNT(*) FROM calls WHERE substr(received_at,1,10)=? AND is_repeat=1", (day,)).fetchone()[0]
    )
    households = conn.execute("SELECT COUNT(DISTINCT household_id) FROM distributions WHERE distribution_date=?", (day,)).fetchone()[0]
    assert figs["households_reached"]["value"] == households == scenario["distribution_record_households"]
    parcels, meals = conn.execute(
        "SELECT SUM(parcel_quantity), SUM(rte_meal_quantity) FROM distributions WHERE distribution_date=?", (day,)
    ).fetchone()
    assert figs["parcels_distributed"]["value"] == parcels and figs["meals_distributed"]["value"] == meals
    assert figs["stock_parcels"]["value"] == _on_hand(conn, "food_parcel")
    assert figs["stock_meals"]["value"] == _on_hand(conn, "rte_meal")
    assert figs["stock_hygiene_kits"]["value"] == _on_hand(conn, "hygiene")


def test_parcels_and_meals_are_separate_units(conn: sqlite3.Connection, scenario: dict) -> None:
    figs = {f["key"]: f for f in brf.compute_figures(conn, scenario["conflict_date"])}
    assert figs["parcels_distributed"]["unit"] == "parcels" and figs["meals_distributed"]["unit"] == "meals"
    combined = figs["parcels_distributed"]["value"] + figs["meals_distributed"]["value"]
    assert all(f["value"] != combined for f in figs.values())


def test_every_figure_has_a_source_link(conn: sqlite3.Connection, scenario: dict) -> None:
    bid = _draft(conn, scenario["conflict_date"])
    sources = brf.get_briefing(conn, bid)["sources"]
    by_key = {s["figure_key"]: s for s in sources}
    assert {f["key"] for f in brf.compute_figures(conn, scenario["conflict_date"])} <= set(by_key)
    assert all(s["source_table"] and s["unit"] for s in sources)
    assert by_key["calls_received"]["source_table"] == "calls"
    assert len(by_key["calls_received"]["source_record_ids"].split(",")) == by_key["calls_received"]["figure_value"]
    assert by_key["households_reached"]["source_table"] == "distributions"
    assert by_key["stock_parcels"]["source_table"] == "v_stock_on_hand"
    assert by_key[f"reported_households_reached_{scenario['conflict_site_id']}"]["source_table"] == "field_updates"
    assert all(s["source_record_ids"] for s in sources if s["figure_value"] > 0)


def test_seeded_conflict_is_flagged(conn: sqlite3.Connection, scenario: dict) -> None:
    conflicts = brf.find_conflicts(conn, scenario["conflict_date"])
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["site_id"] == scenario["conflict_site_id"]
    assert (c["reported"], c["computed"]) == (scenario["field_update_households"], scenario["distribution_record_households"])
    assert c["decision"] is None and len(c["record_ids"]) == c["computed"]
    stored = brf.get_briefing(conn, _draft(conn, scenario["conflict_date"]))["conflicts"]
    assert [x["update_id"] for x in stored] == [c["update_id"]]


def test_matching_field_figure_is_not_a_conflict(conn: sqlite3.Connection) -> None:
    site, day = conn.execute("SELECT site_id, distribution_date FROM distributions ORDER BY distribution_date DESC LIMIT 1").fetchone()
    count = conn.execute(
        "SELECT COUNT(DISTINCT household_id) FROM distributions WHERE site_id=? AND distribution_date=?", (site, day)
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO field_updates(update_id, reported_at, site_id, team, text, figure_key, figure_value) VALUES ('FU-9001', ?, ?, 'Test team', 'x', 'households_reached', ?)",
        (f"{day}T20:00:00", site, count),
    )
    assert not [c for c in brf.find_conflicts(conn, day) if c["update_id"] == "FU-9001"]
    conn.execute("UPDATE field_updates SET figure_value = ? WHERE update_id = 'FU-9001'", (count + 1,))
    assert [c["update_id"] for c in brf.find_conflicts(conn, day) if c["update_id"] == "FU-9001"] == ["FU-9001"]


def test_approval_without_confirmation_is_refused(conn: sqlite3.Connection, scenario: dict) -> None:
    bid = _draft(conn, scenario["conflict_date"])
    _review_all(conn, bid)
    for who in (None, "", "NOPE-99", "OPR-01"):
        with pytest.raises(ConfirmationRequired), conn:
            brf.approve(conn, bid, who)
    assert brf.get_briefing(conn, bid)["status"] == "draft"
    with pytest.raises(brf.BriefingError):
        brf.export_markdown(conn, bid)


def test_approval_requires_every_conflict_reviewed(conn: sqlite3.Connection, scenario: dict) -> None:
    bid = _draft(conn, scenario["conflict_date"])
    with pytest.raises(brf.BriefingError, match="Review every conflict"), conn:
        brf.approve(conn, bid, COORD)
    assert brf.get_briefing(conn, bid)["status"] == "draft"


def test_export_contains_approved_figures_and_unresolved_items(conn: sqlite3.Connection, scenario: dict) -> None:
    day = scenario["conflict_date"]
    bid = _draft(conn, day)
    _review_all(conn, bid, "follow_up")
    with conn:
        brf.approve(conn, bid, COORD)
    b = brf.get_briefing(conn, bid)
    assert b["status"] == "approved" and b["approved_by"] == COORD
    md = brf.export_markdown(conn, bid)
    figs = {f["key"]: f for f in brf.compute_figures(conn, day)}
    assert f"| Households reached (all sites) | {figs['households_reached']['value']} | households |" in md
    assert f"| Food parcels distributed | {figs['parcels_distributed']['value']} | parcels |" in md
    assert f"| Ready-to-eat meals distributed | {figs['meals_distributed']['value']} | meals |" in md
    assert scenario["conflict_site_id"] in md.split("## Unresolved items")[1]
    assert "DST-" in md and f"Approved by {COORD}" in md
    audit = conn.execute("SELECT action FROM audit_log WHERE entity_table='briefings' AND entity_id=? ORDER BY audit_id", (bid,)).fetchall()
    assert [a[0] for a in audit] == ["create", "resolve", "approve"]


def test_resolved_conflict_leaves_no_unresolved_items(conn: sqlite3.Connection, scenario: dict) -> None:
    bid = _draft(conn, scenario["conflict_date"])
    _review_all(conn, bid, "use_records")
    with conn:
        brf.approve(conn, bid, COORD)
    assert "- None." in brf.export_markdown(conn, bid)


def test_edit_and_approved_briefing_is_locked(conn: sqlite3.Connection, scenario: dict) -> None:
    bid = _draft(conn, scenario["conflict_date"])
    with conn:
        brf.save_edit(conn, bid, "# Edited briefing\nMy text", COORD)
    _review_all(conn, bid)
    with conn:
        brf.approve(conn, bid, COORD)
    assert "# Edited briefing" in brf.export_markdown(conn, bid)
    with pytest.raises(brf.BriefingError), conn:
        brf.save_edit(conn, bid, "later change", COORD)


def test_day_without_conflicts_can_be_approved(conn: sqlite3.Connection) -> None:
    bid = _draft(conn, "2030-01-01")
    assert brf.get_briefing(conn, bid)["conflicts"] == []
    with conn:
        brf.approve(conn, bid, COORD)
    assert "No conflicts were found." in brf.export_markdown(conn, bid)


def test_app_starts(app_test) -> None:  # noqa: ANN001
    at = app_test(18).run()
    assert not at.exception


def test_draft_body_fits_one_page_with_parcels_and_meals_apart(conn: sqlite3.Connection, scenario: dict) -> None:
    bid = _draft(conn, scenario["conflict_date"])
    body = brf.get_briefing(conn, bid)["content_md"]
    assert len(body.splitlines()) <= 50 and len(body) <= 4000
    rows = {line.split("|")[1].strip(): line for line in body.splitlines() if line.startswith("| ") and "|" in line[2:]}
    parcels, meals = rows["Food parcels distributed"], rows["Ready-to-eat meals distributed"]
    assert "| parcels |" in parcels and "| meals |" in meals and "| meals |" not in parcels and "| parcels |" not in meals


def test_app_coordinator_edits_text_before_approval(app_test, conn: sqlite3.Connection, scenario: dict) -> None:  # noqa: ANN001
    at = app_test(18).run()
    at.button(key="p18_generate").click().run()
    assert not at.exception
    bid = at.selectbox(key="p18_current").value
    area = at.text_area(key=f"p18_text_{bid}")
    area.set_value(area.value + "\n## Coordinator note\nChecked by the coordinator.\n")
    at.button(key="p18_save").click().run()
    assert not at.exception and [s.value for s in at.success][-1] == "Edits saved."
    assert "Checked by the coordinator." in brf.get_briefing(conn, bid)["content_md"]
    for rv in [b for b in at.button if b.label == "Save review"]:
        rv.click().run()
    at.checkbox(key=f"confirm_tick_p18_approve_{bid}").check()
    [b for b in at.button if b.label == "Approve briefing"][0].click().run()
    assert not at.exception
    assert "Checked by the coordinator." in brf.export_markdown(conn, bid)


def test_day_with_many_field_updates_still_fits_one_page(conn: sqlite3.Connection, scenario: dict) -> None:
    day = scenario["conflict_date"]
    with conn:
        for n in range(60):
            conn.execute(
                "INSERT INTO field_updates(update_id, reported_at, area_id, site_id, team, text, figure_key, figure_value) VALUES (?,?,?,?,?,?,?,?)",
                (f"FU-9{n:03d}", f"{day}T{8 + n // 6:02d}:{(n % 6) * 10:02d}:00", None, None, "Team X", "Long field update text. " * 30, None, None),
            )
    total = len(brf.field_updates_for(conn, day))
    assert total >= 60
    bid = _draft(conn, day)
    b = brf.get_briefing(conn, bid)
    body = b["content_md"]
    assert len(body.splitlines()) <= 50 and len(body) <= 4000
    assert body.count("- FU-") == brf.UPDATE_LIMIT
    assert f"and {total - brf.UPDATE_LIMIT} more in the source records" in body
    # Every figure is still linked to all its records.
    for s in b["sources"]:
        if s["figure_key"] == "calls_received":
            assert s["figure_value"] == len(s["source_record_ids"].split(",")) if s["source_record_ids"] else s["figure_value"] == 0
