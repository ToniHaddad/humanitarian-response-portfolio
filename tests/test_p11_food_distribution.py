"""Project 11: food distribution tracker.

Covers parcel coverage end dates (household size, edited assumptions; meals never extend
them), the separation of parcels and ready-to-eat meals, the due-window rule, site
assignment, the shared parcel rule and meals for new arrivals, saving assumptions and plans
with staff confirmation, and the walkthrough. Tests that write use the `tx` fixture
(db.transaction, which holds the write lock that next_id needs).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import p11_food as p11
import pytest

from shared import clock, confirm, db, food

TODAY = date(2026, 11, 16)  # conftest.ANCHOR
NEXT_WEEK = (TODAY + timedelta(days=1), TODAY + timedelta(days=7))


@pytest.fixture
def tx(db_path: Path) -> Iterator[sqlite3.Connection]:
    """A write transaction on this test's database copy (committed at the end of the test)."""
    with db.transaction(db_path) as c:
        yield c


def scenario(conn: sqlite3.Connection) -> dict:
    return json.loads(db.get_setting("demo.scenarios", conn=conn))["p11"]


def add_household(conn: sqlite3.Connection, hid: str, size: int, area: str, registered: date) -> None:
    conn.execute(
        """INSERT INTO households(household_id, head_name, name_script, household_size, area_id, registered_at, status)
           VALUES (?, 'Test Household', 'latin', ?, ?, ?, 'active')""",
        (hid, size, area, f"{registered.isoformat()}T10:00:00"),
    )


def record(conn: sqlite3.Connection, hid: str, site: str, size: int, item: str | None = p11.STANDARD_PARCEL, qty: int = 1, meals: int = 0):
    return p11.record_distribution(
        conn,
        distribution_date=TODAY,
        site_id=site,
        household_id=hid,
        household_size=size,
        parcel_item_id=item,
        parcel_quantity=qty,
        rte_meal_quantity=meals,
        recorded_by="FW-01",
    )


def due_ids(fc: p11.Forecast) -> set[str]:
    return {d.household.household_id for d in fc.due}


# -- parcel coverage end dates -----------------------------------------------------------
@pytest.mark.parametrize(
    ("size", "item", "parcels", "days"),
    [(5, p11.STANDARD_PARCEL, 1, 30), (3, p11.STANDARD_PARCEL, 1, 50), (6, p11.STANDARD_PARCEL, 1, 25), (2, p11.SMALL_PARCEL, 1, 30)],
)
def test_parcel_coverage_depends_on_household_size(size: int, item: str, parcels: int, days: int) -> None:
    preview = p11.preview_distribution(TODAY, size, item, parcels, 0, food.default_assumptions())
    assert preview["parcel_days"] == days
    assert preview["expected_coverage_days"] == days
    assert preview["meal_days"] == 0 and preview["meal_coverage_end"] is None
    assert preview["parcel_coverage_end"] == TODAY + timedelta(days=days - 1)


def test_meals_never_extend_parcel_coverage() -> None:
    a = food.default_assumptions()
    # 5 people: 1 standard parcel = 30 parcel days; 30 meals / (5 x 3 meals a day) = 2 meal days, shown separately.
    preview = p11.preview_distribution(TODAY, 5, p11.STANDARD_PARCEL, 1, 30, a)
    assert (preview["parcel_days"], preview["meal_days"], preview["expected_coverage_days"]) == (30, 2, 30)
    assert preview["parcel_coverage_end"] == TODAY + timedelta(days=29)
    assert preview["meal_coverage_end"] == TODAY + timedelta(days=1)
    meals_only = p11.preview_distribution(TODAY, 5, p11.STANDARD_PARCEL, 0, 30, a)
    assert (meals_only["parcel_days"], meals_only["parcel_coverage_end"], meals_only["meal_days"]) == (0, None, 2)


def test_recorded_distribution_stores_parcel_days_only(tx: sqlite3.Connection) -> None:
    sc = scenario(tx)
    before_disp = db.scalar("SELECT COUNT(*) FROM dispatches", conn=tx)
    before_moves = db.scalar("SELECT COUNT(*) FROM stock_movements", conn=tx)
    res = record(tx, sc["household_id"], sc["site_id"], 5, qty=1, meals=30)
    row = db.row_dict(tx, "distributions", "distribution_id", res["distribution_id"])
    assert row is not None
    assert (row["parcel_quantity"], row["rte_meal_quantity"], row["expected_coverage_days"]) == (1, 30, 30)
    assert row["recorded_by"] == "FW-01" and row["recorded_at"] == clock.iso()
    assert res["coverage_end"] == TODAY + timedelta(days=29) and res["meal_days"] == 2
    assert p11.household_coverage(tx, food.load_assumptions(tx))[sc["household_id"]].coverage_end == TODAY + timedelta(days=29)
    audit = tx.execute("SELECT channel, action FROM audit_log WHERE entity_id = ?", (res["distribution_id"],)).fetchone()
    assert tuple(audit) == ("app:11", "record_distribution")
    # Recording never creates dispatches or stock movements (stock is Project 10's ledger).
    assert db.scalar("SELECT COUNT(*) FROM dispatches", conn=tx) == before_disp
    assert db.scalar("SELECT COUNT(*) FROM stock_movements", conn=tx) == before_moves


def test_meals_only_distribution_gives_no_parcel_coverage(tx: sqlite3.Connection) -> None:
    add_household(tx, "HH-90010", 4, "AR-12", TODAY - timedelta(days=200))
    res = record(tx, "HH-90010", "SITE-08", 4, item=p11.STANDARD_PARCEL, qty=0, meals=60)
    row = db.row_dict(tx, "distributions", "distribution_id", res["distribution_id"])
    assert row is not None
    assert (row["parcel_item_id"], row["parcel_quantity"], row["expected_coverage_days"], row["rte_meal_quantity"]) == (None, 0, 0, 60)
    hc = p11.household_coverage(tx, food.load_assumptions(tx))["HH-90010"]
    assert hc.coverage_end is None and hc.last_meal_days == 5  # 60 / (4 x 3)
    assert "HH-90010" in due_ids(p11.forecast(tx, "SITE-08", *NEXT_WEEK, food.load_assumptions(tx)))


def test_edited_assumptions_change_coverage_end_and_forecast(tx: sqlite3.Connection) -> None:
    sc = scenario(tx)
    hid, site = sc["household_id"], sc["site_id"]
    record(tx, hid, site, 5)
    saved = food.load_assumptions(tx)
    edited = dict(saved, **{"person_days.ITM-FP-STD": 120.0})
    change = p11.household_coverage_change(tx, hid, saved, edited)
    assert change["saved_end"] == TODAY + timedelta(days=29)  # 150 / 5 = 30 days
    assert change["edited_end"] == TODAY + timedelta(days=23)  # 120 / 5 = 24 days
    assert change["shift_days"] == -6
    window = (TODAY + timedelta(days=1), TODAY + timedelta(days=25))
    fc_saved = p11.forecast(tx, site, *window, saved)
    fc_edited = p11.forecast(tx, site, *window, edited)
    assert hid not in due_ids(fc_saved)
    assert hid in due_ids(fc_edited)
    assert fc_edited.households_due > fc_saved.households_due
    rows = {r["measure"]: r for r in p11.compare_forecasts(fc_saved, fc_edited)}
    assert rows["Households due"]["change"] == fc_edited.households_due - fc_saved.households_due


def test_saving_assumptions_needs_confirmation_and_is_audited(tx: sqlite3.Connection) -> None:
    new = {"person_days.ITM-FP-STD": 120.0}
    with pytest.raises(confirm.ConfirmationRequired):
        p11.save_assumptions(tx, new, confirmed_by=None)
    with pytest.raises(confirm.ConfirmationRequired):
        p11.save_assumptions(tx, new, confirmed_by="FW-01")  # field workers cannot change forecasts
    assert food.load_assumptions(tx)["person_days.ITM-FP-STD"] == 150.0
    with pytest.raises(ValueError):
        p11.save_assumptions(tx, {"meals_per_person_per_day": 0.0}, confirmed_by="CRD-01")
    with pytest.raises(ValueError):
        p11.save_assumptions(tx, {"small_parcel_max_household_size": -1.0}, confirmed_by="CRD-01")
    assert p11.save_assumptions(tx, new, confirmed_by="CRD-01") == ["person_days.ITM-FP-STD"]
    row = tx.execute(
        "SELECT value, updated_by, updated_at FROM forecast_assumptions WHERE assumption_key = 'person_days.ITM-FP-STD'"
    ).fetchone()
    assert (row["value"], row["updated_by"], row["updated_at"]) == (120.0, "CRD-01", clock.iso())
    audit = tx.execute(
        "SELECT before_json, after_json FROM audit_log WHERE entity_table = 'forecast_assumptions' AND channel = 'app:11'"
    ).fetchall()
    assert len(audit) == 1
    assert json.loads(audit[0]["before_json"]) == {"value": 150.0} and json.loads(audit[0]["after_json"]) == {"value": 120.0}
    # New recordings use the saved value: 1 standard parcel for 5 people = 120 / 5 = 24 days.
    sc = scenario(tx)
    assert record(tx, sc["household_id"], sc["site_id"], 5)["expected_coverage_days"] == 24
    assert p11.save_assumptions(tx, new, confirmed_by="CRD-01") == []  # unchanged values are not re-saved


def test_coverage_end_is_latest_parcel_end_and_keeps_stored_days(tx: sqlite3.Connection) -> None:
    sc = scenario(tx)
    saved = food.load_assumptions(tx)
    hc = p11.household_coverage(tx, saved)[sc["household_id"]]
    assert hc.coverage_end == date.fromisoformat(sc["current_coverage_end"])
    assert hc.last_stored_days == hc.last_recomputed_days  # the seed stores parcel days
    edited = dict(saved, **{"person_days.ITM-FP-STD": 120.0, "person_days.ITM-FP-SML": 48.0})
    hc_edit = p11.household_coverage(tx, edited)[sc["household_id"]]
    assert hc_edit.last_stored_days == hc.last_stored_days  # the stored value never changes
    assert hc_edit.last_recomputed_days < hc.last_recomputed_days
    assert hc_edit.coverage_end < hc.coverage_end
    # Many meals do not extend the parcel coverage end.
    record(tx, sc["household_id"], sc["site_id"], 5, qty=1, meals=300)
    hc_after = p11.household_coverage(tx, saved)[sc["household_id"]]
    assert hc_after.coverage_end == TODAY + timedelta(days=29)
    assert hc_after.last_meal_days == 20  # 300 / (5 x 3), a separate figure


# -- parcels and meals are never summed ----------------------------------------------
def test_parcels_and_meals_never_summed(conn: sqlite3.Connection) -> None:
    saved = food.load_assumptions(conn)
    found_meals = False
    for site in p11.food_sites(conn):
        fc = p11.forecast(conn, site["site_id"], *NEXT_WEEK, saved)
        summary = fc.summary()
        assert set(summary) == {"households_due", "standard_parcels", "small_parcels", "parcels_total", "rte_meals"}
        assert {k: p11.SUMMARY_UNITS[k] for k in summary} == {
            "households_due": "households",
            "standard_parcels": "parcels",
            "small_parcels": "parcels",
            "parcels_total": "parcels",
            "rte_meals": "meals",
        }
        assert summary["parcels_total"] == summary["standard_parcels"] + summary["small_parcels"]
        assert summary["rte_meals"] == sum(d.rte_meals for d in fc.due)
        assert summary["standard_parcels"] == sum(d.parcels for d in fc.due if d.parcel_item_id == p11.STANDARD_PARCEL)
        if summary["rte_meals"]:
            found_meals = True
            assert summary["parcels_total"] != summary["standard_parcels"] + summary["small_parcels"] + summary["rte_meals"]
        assert all(r["unit"] in {"households", "parcels", "meals"} for r in p11.compare_forecasts(fc, fc))
        assert {r["measure"] for r in p11.compare_forecasts(fc, fc)} == {
            "Households due",
            "Standard parcels",
            "Small parcels",
            "Ready-to-eat meals",
        }
        units = {ln["item_id"]: ln["unit"] for ln in p11.plan_lines(fc)}
        assert all(units[i] == "parcels" for i in units if i in p11.PARCEL_ITEMS)
        assert units.get(p11.RTE_MEAL, "meals") == "meals"
        assert fc.item_quantities() == {
            p11.STANDARD_PARCEL: summary["standard_parcels"],
            p11.SMALL_PARCEL: summary["small_parcels"],
            p11.RTE_MEAL: summary["rte_meals"],
        }
    assert found_meals, "the seed should give at least one site new arrivals with meals"


# -- the walkthrough -----------------------------------------------------------------
def test_walkthrough_record_parcel_and_leave_next_weeks_forecast(tx: sqlite3.Connection) -> None:
    sc = scenario(tx)
    hid, site = sc["household_id"], sc["site_id"]
    saved = food.load_assumptions(tx)
    # 1. The household at its site, with its parcel coverage end.
    at_site = {hc.household_id: hc for hc in p11.households_at_site(tx, site, saved)}
    assert at_site[hid].coverage_end == date.fromisoformat(sc["current_coverage_end"])
    assert 2 <= at_site[hid].days_left(TODAY) <= 6
    # 2. Next week's forecast lists it as due.
    before = p11.forecast(tx, site, *NEXT_WEEK, saved)
    due_before = {d.household.household_id: d for d in before.due}
    assert hid in due_before
    assert due_before[hid].parcel_item_id == p11.STANDARD_PARCEL and due_before[hid].parcels == 1
    # 3. Record 1 standard parcel today: 150 / 5 = 30 days, parcel coverage ends today + 29.
    res = record(tx, hid, site, sc["household_size"])
    assert res["expected_coverage_days"] == 30
    assert res["coverage_end"] == TODAY + timedelta(days=29)
    # 4. The new end date shows and the household leaves next week's forecast.
    after_hc = p11.household_coverage(tx, saved)[hid]
    assert after_hc.coverage_end == TODAY + timedelta(days=29)
    assert after_hc.days_left(TODAY) == 29
    after = p11.forecast(tx, site, *NEXT_WEEK, saved)
    assert hid not in due_ids(after)
    assert after.households_due == before.households_due - 1
    assert after.standard_parcels == before.standard_parcels - 1
    assert after.small_parcels == before.small_parcels
    assert after.rte_meals == before.rte_meals - due_before[hid].rte_meals


# -- due window, site assignment, merged households ------------------------------------
def test_due_window_edges(tx: sqlite3.Connection) -> None:
    end = TODAY + timedelta(days=7)
    assert p11.is_due(end - timedelta(days=1), end)  # runs out on the last day
    assert not p11.is_due(end, end)  # food lasts the whole window
    assert p11.is_due(TODAY - timedelta(days=20), end)  # already ended
    assert p11.is_due(None, end)  # no parcels recorded
    sc = scenario(tx)
    record(tx, sc["household_id"], sc["site_id"], 5)  # parcel coverage ends today + 29
    saved = food.load_assumptions(tx)
    start = TODAY + timedelta(days=1)
    assert sc["household_id"] not in due_ids(p11.forecast(tx, sc["site_id"], start, TODAY + timedelta(days=29), saved))
    assert sc["household_id"] in due_ids(p11.forecast(tx, sc["site_id"], start, TODAY + timedelta(days=30), saved))
    with pytest.raises(ValueError):
        p11.forecast(tx, sc["site_id"], start, start - timedelta(days=1), saved)


def test_site_assignment_last_distribution_then_catchment(tx: sqlite3.Connection) -> None:
    add_household(tx, "HH-90001", 4, "AR-12", TODAY - timedelta(days=200))
    saved = food.load_assumptions(tx)
    hc = p11.household_coverage(tx, saved)["HH-90001"]
    assert (hc.forecast_site_id, hc.site_source, hc.coverage_end) == ("SITE-08", "catchment", None)
    assert "HH-90001" in due_ids(p11.forecast(tx, "SITE-08", *NEXT_WEEK, saved))
    # Served at another site: it now belongs to that site's forecast.
    record(tx, "HH-90001", "SITE-01", 4)
    hc = p11.household_coverage(tx, saved)["HH-90001"]
    assert (hc.forecast_site_id, hc.site_source) == ("SITE-01", "last distribution")
    assert "HH-90001" not in {h.household_id for h in p11.households_at_site(tx, "SITE-08", saved)}


def test_merged_households_are_excluded(tx: sqlite3.Connection) -> None:
    merged = tx.execute("SELECT household_id FROM households WHERE status = 'merged'").fetchone()[0]
    tx.execute(
        """INSERT INTO distributions(distribution_id, distribution_date, site_id, household_id, household_size, parcel_item_id,
                                     parcel_quantity, rte_meal_quantity, expected_coverage_days, recorded_at)
           VALUES ('DST-99999', ?, 'SITE-01', ?, 4, 'ITM-FP-STD', 1, 0, 37, ?)""",
        ((TODAY - timedelta(days=60)).isoformat(), merged, clock.iso()),
    )
    saved = food.load_assumptions(tx)
    assert merged not in p11.household_coverage(tx, saved)
    for site in p11.food_sites(tx):
        assert merged not in due_ids(p11.forecast(tx, site["site_id"], *NEXT_WEEK, saved))
    with pytest.raises(ValueError, match="not an active household"):
        record(tx, merged, "SITE-01", 4)


# -- parcels and meals required ------------------------------------------------------
SIZES = {"HH-90021": 1, "HH-90022": 2, "HH-90023": 3, "HH-90025": 5, "HH-90026": 6, "HH-90030": 10}


def test_parcels_required_rule_in_forecast(tx: sqlite3.Connection) -> None:
    for hid, size in SIZES.items():
        add_household(tx, hid, size, "AR-12", TODAY - timedelta(days=300))  # no distributions: due at SITE-08
    saved = food.load_assumptions(tx)
    due = {d.household.household_id: (d.parcel_item_id, d.parcels) for d in p11.forecast(tx, "SITE-08", *NEXT_WEEK, saved).due}
    assert {hid: due[hid] for hid in SIZES} == {
        "HH-90021": (p11.SMALL_PARCEL, 1),
        "HH-90022": (p11.SMALL_PARCEL, 1),
        "HH-90023": (p11.STANDARD_PARCEL, 1),
        "HH-90025": (p11.STANDARD_PARCEL, 1),
        "HH-90026": (p11.STANDARD_PARCEL, 2),
        "HH-90030": (p11.STANDARD_PARCEL, 2),
    }
    for hid, size in SIZES.items():
        assert due[hid] == food.parcel_plan(size, saved)  # the one shared rule


def test_editing_parcel_rule_assumptions_changes_parcels_required(tx: sqlite3.Connection) -> None:
    for hid, size in SIZES.items():
        add_household(tx, hid, size, "AR-12", TODAY - timedelta(days=300))
    saved = food.load_assumptions(tx)
    base = p11.forecast(tx, "SITE-08", *NEXT_WEEK, saved)
    # Small parcels up to 3 people: the 3-person household moves from 1 standard to 1 small parcel.
    small3 = p11.forecast(tx, "SITE-08", *NEXT_WEEK, dict(saved, small_parcel_max_household_size=3.0))
    plan3 = {d.household.household_id: (d.parcel_item_id, d.parcels) for d in small3.due}
    assert plan3["HH-90023"] == (p11.SMALL_PARCEL, 1)
    assert small3.small_parcels > base.small_parcels and small3.standard_parcels < base.standard_parcels
    assert small3.households_due == base.households_due  # due-ness depends on parcel coverage, not the parcel rule
    # One standard parcel per 4 people: the 5-person household needs 2.
    per4 = p11.forecast(tx, "SITE-08", *NEXT_WEEK, dict(saved, persons_per_standard_parcel=4.0))
    plan4 = {d.household.household_id: (d.parcel_item_id, d.parcels) for d in per4.due}
    assert plan4["HH-90025"] == (p11.STANDARD_PARCEL, 2) and plan4["HH-90030"] == (p11.STANDARD_PARCEL, 3)
    assert per4.standard_parcels > base.standard_parcels and per4.small_parcels == base.small_parcels
    assert per4.rte_meals == base.rte_meals  # the parcel rule never changes meals
    rows = {r["measure"]: r for r in p11.compare_forecasts(base, per4)}
    assert rows["Standard parcels"]["change"] == per4.standard_parcels - base.standard_parcels


def test_meals_only_for_new_arrivals(tx: sqlite3.Connection) -> None:
    add_household(tx, "HH-90002", 4, "AR-12", TODAY - timedelta(days=10))  # new arrival
    add_household(tx, "HH-90003", 3, "AR-12", TODAY - timedelta(days=60))  # exactly 60 days: still new
    add_household(tx, "HH-90004", 6, "AR-12", TODAY - timedelta(days=61))  # not new
    saved = food.load_assumptions(tx)
    fc = p11.forecast(tx, "SITE-08", *NEXT_WEEK, saved)
    due = {d.household.household_id: d for d in fc.due}
    assert (due["HH-90002"].new_arrival, due["HH-90002"].rte_meals) == (True, 24)  # 4 people x 6 meals
    assert (due["HH-90003"].new_arrival, due["HH-90003"].rte_meals) == (True, 18)
    assert (due["HH-90004"].new_arrival, due["HH-90004"].rte_meals) == (False, 0)
    assert due["HH-90004"].parcels == 2
    for d in fc.due:
        expected = d.household.household_size * 6 if d.household.registered_on >= TODAY - timedelta(days=60) else 0
        assert d.rte_meals == expected
    fc_edit = p11.forecast(tx, "SITE-08", *NEXT_WEEK, dict(saved, rte_meals_per_person_per_visit=9.0))
    assert {d.household.household_id: d.rte_meals for d in fc_edit.due}["HH-90002"] == 36
    assert fc_edit.parcels_total == fc.parcels_total  # meal assumptions never change parcels


# -- recording guards ------------------------------------------------------------------
def test_record_refuses_invalid_and_double_entries(tx: sqlite3.Connection) -> None:
    sc = scenario(tx)
    hid, site = sc["household_id"], sc["site_id"]
    base = dict(site_id=site, household_id=hid, household_size=5, parcel_item_id=p11.STANDARD_PARCEL, rte_meal_quantity=0)
    with pytest.raises(ValueError):
        record(tx, hid, site, 5, qty=0, meals=0)
    with pytest.raises(ValueError):
        record(tx, hid, "SITE-07", 5)  # a clinic, not a food distribution site
    with pytest.raises(ValueError):
        p11.record_distribution(tx, distribution_date=TODAY + timedelta(days=1), parcel_quantity=1, recorded_by="FW-01", **base)
    with pytest.raises(ValueError):
        # DSP-0001 is not a dispatch of standard parcels to this site for today.
        p11.record_distribution(tx, distribution_date=TODAY, parcel_quantity=1, recorded_by="FW-01", dispatch_id="DSP-0001", **base)
    with pytest.raises(confirm.ConfirmationRequired):
        p11.record_distribution(tx, distribution_date=TODAY, parcel_quantity=1, recorded_by="", **base)
    with pytest.raises(confirm.ConfirmationRequired):
        p11.record_distribution(tx, distribution_date=TODAY, parcel_quantity=1, recorded_by="DO-01", **base)
    record(tx, hid, site, 5)
    with pytest.raises(ValueError, match="twice"):
        record(tx, hid, site, 5)


def test_dispatch_link_from_todays_dispatches(tx: sqlite3.Connection) -> None:
    options = p11.dispatch_options(tx, "SITE-01", p11.STANDARD_PARCEL, TODAY)
    assert options, "the seed dispatches standard parcels to SITE-01 today"
    hid = tx.execute(
        """SELECT household_id FROM households WHERE status = 'active' AND area_id = 'AR-08' AND household_size BETWEEN 3 AND 5
           AND household_id NOT IN (SELECT household_id FROM distributions WHERE distribution_date = ?) ORDER BY household_id""",
        (TODAY.isoformat(),),
    ).fetchone()[0]
    res = p11.record_distribution(
        tx,
        distribution_date=TODAY,
        site_id="SITE-01",
        household_id=hid,
        household_size=4,
        parcel_item_id=p11.STANDARD_PARCEL,
        parcel_quantity=1,
        rte_meal_quantity=0,
        recorded_by="FW-02",
        dispatch_id=options[0]["dispatch_id"],
    )
    assert res["dispatch_id"] == options[0]["dispatch_id"]


# -- planned distributions -------------------------------------------------------------
def test_save_plan_writes_rows_per_item_and_refuses_duplicates(tx: sqlite3.Connection) -> None:
    saved = food.load_assumptions(tx)
    site = "SITE-03"
    fc = p11.forecast(tx, site, *NEXT_WEEK, saved)
    assert fc.standard_parcels > 0 and fc.rte_meals > 0
    planned_date = TODAY + timedelta(days=2)
    with pytest.raises(confirm.ConfirmationRequired):
        p11.save_plan(tx, fc, planned_date, confirmed_by=None)
    with pytest.raises(confirm.ConfirmationRequired):
        p11.save_plan(tx, fc, planned_date, confirmed_by="FW-01")
    ids = p11.save_plan(tx, fc, planned_date, confirmed_by="CRD-01")
    rows = {
        r["item_id"]: r
        for r in db.query_all(f"SELECT * FROM distribution_plans WHERE plan_id IN ({','.join('?' for _ in ids)})", ids, conn=tx)
    }
    expected = {k: v for k, v in fc.item_quantities().items() if v > 0}
    assert {k: r["planned_quantity"] for k, r in rows.items()} == expected
    assert rows[p11.RTE_MEAL]["planned_quantity"] == fc.rte_meals  # meals, in meals
    assert rows[p11.STANDARD_PARCEL]["planned_quantity"] == fc.standard_parcels  # parcels, in parcels
    assert rows[p11.STANDARD_PARCEL]["planned_households"] == sum(1 for d in fc.due if d.parcel_item_id == p11.STANDARD_PARCEL)
    assert rows[p11.RTE_MEAL]["planned_households"] == sum(1 for d in fc.due if d.rte_meals > 0)
    for r in rows.values():
        assert (r["site_id"], r["warehouse_id"], r["planned_date"], r["status"], r["created_by"]) == (
            site,
            "WH-01",
            planned_date.isoformat(),
            "planned",
            "CRD-01",
        )
    assert db.scalar("SELECT COUNT(*) FROM audit_log WHERE entity_table = 'distribution_plans' AND channel = 'app:11'", conn=tx) == len(ids)
    count = db.scalar("SELECT COUNT(*) FROM distribution_plans", conn=tx)
    with pytest.raises(ValueError, match="already plans"):
        p11.save_plan(tx, fc, planned_date, confirmed_by="CRD-01")
    assert db.scalar("SELECT COUNT(*) FROM distribution_plans", conn=tx) == count


def test_save_plan_refuses_a_seeded_plan_and_past_dates(tx: sqlite3.Connection) -> None:
    existing = tx.execute(
        "SELECT site_id, planned_date FROM distribution_plans WHERE status = 'planned' AND item_id = 'ITM-FP-STD' ORDER BY planned_date"
    ).fetchone()
    saved = food.load_assumptions(tx)
    planned = date.fromisoformat(existing["planned_date"])
    fc = p11.forecast(tx, existing["site_id"], TODAY + timedelta(days=1), planned + timedelta(days=7), saved)
    assert fc.standard_parcels > 0
    with pytest.raises(ValueError, match="already plans"):
        p11.save_plan(tx, fc, planned, confirmed_by="CRD-01")
    with pytest.raises(ValueError, match="past"):
        p11.save_plan(tx, fc, TODAY - timedelta(days=1), confirmed_by="CRD-01")
    assert p11.existing_plans(tx, existing["site_id"], TODAY, planned)


# -- the app ---------------------------------------------------------------------------
def test_app_starts(app_test) -> None:  # noqa: ANN001 - fixture factory
    at = app_test(11).run()
    assert not at.exception
    assert any("Forecast for a date range" in s.value for s in at.subheader)


def test_app_records_the_walkthrough_parcel(app_test, db_path) -> None:  # noqa: ANN001 - fixtures
    with db.transaction(db_path) as c:
        sc = scenario(c)
    at = app_test(11).run()
    at.sidebar.selectbox(key="p11_site").set_value(sc["site_id"]).run()
    at.selectbox(key="p11_rec_household").set_value(sc["household_id"]).run()
    assert not at.exception
    at.checkbox(key="confirm_tick_p11_record").check()
    next(b for b in at.button if b.label == "Record distribution").click().run()
    assert not at.exception
    assert any("Parcel coverage 30 days" in s.value for s in at.success)
    with db.transaction(db_path) as c:
        row = c.execute(
            "SELECT parcel_item_id, parcel_quantity, rte_meal_quantity, expected_coverage_days, recorded_by FROM distributions "
            "WHERE household_id = ? AND distribution_date = ?",
            (sc["household_id"], TODAY.isoformat()),
        ).fetchone()
    assert tuple(row) == ("ITM-FP-STD", 1, 0, 30, "CRD-01")
