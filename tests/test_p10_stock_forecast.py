"""Project 10: warehouse stock forecast. Clock frozen at 2026-11-16 10:00 (conftest.ANCHOR)."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import p10_forecast as fc
import pytest

from shared import clock, db, stock
from shared.confirm import ConfirmationRequired

TODAY = date(2026, 11, 16)
STAFF = "WHO-01"


def _pairs(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [(r[0], r[1]) for r in conn.execute("SELECT warehouse_id, item_id FROM v_stock_on_hand ORDER BY 1, 2")]


def _count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _move(conn: sqlite3.Connection, wh: str, item: str, when: str, mtype: str, reason: str, qty: int) -> None:
    conn.execute(
        "INSERT INTO stock_movements(warehouse_id, item_id, moved_at, movement_type, reason, quantity) VALUES (?,?,?,?,?,?)",
        (wh, item, when, mtype, reason, qty),
    )


def _plan(
    conn: sqlite3.Connection, plan_id: str, wh: str, item: str, when: date, qty: int, status: str = "planned", site: str = "SITE-01"
) -> None:
    conn.execute(
        "INSERT INTO distribution_plans(plan_id, site_id, warehouse_id, item_id, planned_date, planned_quantity, status) VALUES (?,?,?,?,?,?,?)",
        (plan_id, site, wh, item, when.isoformat(), qty, status),
    )


# -- stock balances ------------------------------------------------------------
def test_stock_balances_by_warehouse_and_item(conn: sqlite3.Connection) -> None:
    for wh, item in _pairs(conn):
        received = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM stock_movements WHERE warehouse_id=? AND item_id=? AND movement_type='received'",
            (wh, item),
        ).fetchone()[0]
        dispatched = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM stock_movements WHERE warehouse_id=? AND item_id=? AND movement_type='dispatched'",
            (wh, item),
        ).fetchone()[0]
        assert received - dispatched == stock.on_hand_qty(conn, wh, item)
        row = fc.stock_table(conn, wh).set_index("item_id").loc[item]
        assert (row["received"], row["dispatched"], row["on_hand"]) == (received, dispatched, received - dispatched)
        dispatch_total = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM dispatches WHERE warehouse_id=? AND item_id=?", (wh, item)
        ).fetchone()[0]
        ledger_dispatch = conn.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM stock_movements WHERE warehouse_id=? AND item_id=? AND movement_type='dispatched' AND reason='dispatch'",
            (wh, item),
        ).fetchone()[0]
        assert dispatch_total == ledger_dispatch


def test_stock_table_has_units(conn: sqlite3.Connection) -> None:
    table = fc.stock_table(conn, "WH-01").set_index("item_id")
    assert table.loc["ITM-FP-STD", "unit"] == "parcel"
    assert table.loc["ITM-RTE", "unit"] == "meal"
    assert table.loc["ITM-HYG", "unit"] == "kit"
    assert fc.axis_title("Standard food parcel", "parcel") == "Standard food parcels (parcels)"


# -- recent demand ----------------------------------------------------------------
def test_recent_demand_net_of_returns_and_window(conn: sqlite3.Connection) -> None:
    wh, item = "WH-03", "ITM-MED"  # no movements in the window in the seed
    assert fc.recent_daily_demand(conn, wh, item, 28, TODAY) == 0
    _move(conn, wh, item, "2026-11-15T12:00:00", "dispatched", "dispatch", 56)  # inside
    _move(conn, wh, item, "2026-10-19T12:00:00", "dispatched", "dispatch", 28)  # first day of the window (16 Nov - 28 days)
    _move(conn, wh, item, "2026-10-18T23:00:00", "dispatched", "dispatch", 100)  # just outside
    _move(conn, wh, item, "2026-11-10T09:00:00", "received", "distribution_return", 14)  # reduces demand
    _move(conn, wh, item, "2026-11-10T09:00:00", "received", "supplier_delivery", 500)  # not demand
    _move(conn, wh, item, "2026-11-16T09:00:00", "dispatched", "dispatch", 40)  # today: not "before today"
    assert fc.recent_net_demand(conn, 28, TODAY)[(wh, item)] == 70
    assert fc.recent_daily_demand(conn, wh, item, 28, TODAY) == pytest.approx(70 / 28)
    # a shorter window drops the 28-day-old dispatch: 56 - 14 = 42 over 14 days
    assert fc.recent_daily_demand(conn, wh, item, 14, TODAY) == pytest.approx(42 / 14)


def test_recent_demand_never_negative(conn: sqlite3.Connection) -> None:
    _move(conn, "WH-03", "ITM-MED", "2026-11-10T09:00:00", "received", "distribution_return", 30)
    assert fc.recent_daily_demand(conn, "WH-03", "ITM-MED", 28, TODAY) == 0


def test_window_must_be_positive(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        fc.recent_net_demand(conn, 0, TODAY)


# -- plan selection --------------------------------------------------------------
def test_open_plans_selection(conn: sqlite3.Connection) -> None:
    wh, item = "WH-03", "ITM-MED"
    _plan(conn, "PLN-T001", wh, item, TODAY, 5)  # today counts
    _plan(conn, "PLN-T002", wh, item, TODAY + timedelta(days=3), 6)
    _plan(conn, "PLN-T003", wh, item, TODAY - timedelta(days=1), 7)  # past
    _plan(conn, "PLN-T004", wh, item, TODAY + timedelta(days=2), 8, status="cancelled")
    _plan(conn, "PLN-T005", wh, item, TODAY + timedelta(days=2), 9, status="completed")
    _plan(conn, "PLN-T006", wh, item, TODAY + timedelta(days=4), 4)  # will have a dispatch row
    conn.execute(
        "INSERT INTO dispatches(dispatch_id, plan_id, warehouse_id, site_id, item_id, quantity, dispatched_at) "
        "VALUES ('DSP-T001','PLN-T006',?,?,?,?,?)",
        (wh, "SITE-01", item, 4, "2026-11-16T09:00:00"),
    )
    got = fc.open_plans(conn, wh, item, TODAY)
    assert list(got["plan_id"]) == ["PLN-T001", "PLN-T002"]
    assert got["planned_quantity"].sum() == 11


# -- the rules, pure ---------------------------------------------------------------
def test_round_up_to_ten() -> None:
    assert [fc.round_up_to(v) for v in (-5, 0, 0.0000001, 1, 9.99, 10, 10.0001, 55, 60.0)] == [0, 0, 0, 10, 10, 10, 20, 60, 60]


def test_no_double_counting_series() -> None:
    plans = {TODAY + timedelta(days=1): 10, TODAY + timedelta(days=3): 5}
    # up to and including the last planned date: planned only; after: the recent rate
    assert fc.daily_demand_series(2.0, plans, TODAY, 7) == [0, 10, 0, 5, 2, 2, 2]
    # no plans at all: recent rate every day
    assert fc.daily_demand_series(2.0, {}, TODAY, 3) == [2, 2, 2]


def _hand_built(
    on_hand: int, lead: int = 4, safety: int = 2, horizon: int = 90, plans: dict[date, int] | None = None, threshold: int = 3
) -> fc.PairForecast:
    return fc.forecast_pair(
        warehouse_id="WH-01",
        item_id="ITM-X",
        item="Test item",
        unit="kit",
        on_hand=on_hand,
        rate=2.0,
        plan_by_date=plans if plans is not None else {TODAY + timedelta(days=1): 10, TODAY + timedelta(days=3): 5},
        lead_time_days=lead,
        safety_days=safety,
        reorder_threshold=threshold,
        horizon_days=horizon,
        today=TODAY,
    )


def test_stockout_date_known_answer() -> None:
    # balances: d0 20, d1 10, d2 10, d3 5, d4 3, d5 1, d6 -1  -> first below zero on day 6
    f = _hand_built(20)
    assert fc.projected_balances(20, f.series[:7]) == [20, 10, 10, 5, 3, 1, -1]
    assert f.stockout_date == TODAY + timedelta(days=6)
    assert f.reorder_by == TODAY + timedelta(days=2)  # lead time 4
    assert not f.order_now
    assert f.planned_in_horizon == 15


def test_stockout_exactly_zero_is_not_a_stockout() -> None:
    assert _hand_built(10, plans={TODAY: 10}).stockout_date == TODAY + timedelta(days=1)  # 0 left after day 0, rate 2 on day 1
    f = fc.forecast_pair(
        warehouse_id="WH-01", item_id="ITM-X", item="t", unit="kit", on_hand=10, rate=0.0, plan_by_date={TODAY: 10},
        lead_time_days=1, safety_days=1, reorder_threshold=0, horizon_days=30, today=TODAY,
    )  # fmt: skip
    assert f.stockout_date is None
    f2 = fc.forecast_pair(
        warehouse_id="WH-01", item_id="ITM-X", item="t", unit="kit", on_hand=10, rate=0.0, plan_by_date={TODAY: 11},
        lead_time_days=1, safety_days=1, reorder_threshold=0, horizon_days=30, today=TODAY,
    )  # fmt: skip
    assert f2.stockout_date == TODAY  # today's demand cannot be fully met


def test_no_stockout_within_horizon() -> None:
    f = _hand_built(1000, horizon=30)
    assert f.stockout_date is None and f.reorder_by is None and not f.order_now
    # stockout after the horizon ends is not reported
    assert _hand_built(20, horizon=6).stockout_date is None
    assert _hand_built(20, horizon=7).stockout_date == TODAY + timedelta(days=6)


def test_reorder_by_flags_order_now() -> None:
    assert _hand_built(20, lead=6).reorder_by == TODAY  # today -> order now
    assert _hand_built(20, lead=6).order_now
    assert _hand_built(20, lead=9).order_now  # overdue (before today)
    assert not _hand_built(20, lead=5).order_now


def test_suggested_replenishment_and_rounding() -> None:
    # cover = 4 + 2 + 28 = 34 days: 15 planned (days 0-3) + 30 days x 2 = 75 units
    assert _hand_built(20).suggested_replenishment == 60  # 55 -> next 10
    assert _hand_built(5).suggested_replenishment == 70  # exactly 70 stays 70
    assert _hand_built(4).suggested_replenishment == 80  # 71 -> 80
    assert _hand_built(500).suggested_replenishment == 0  # never negative


def test_below_threshold_flag() -> None:
    assert _hand_built(2, threshold=3).below_threshold
    assert not _hand_built(3, threshold=3).below_threshold  # at the threshold is not below
    assert not _hand_built(4, threshold=3).below_threshold


def _first_plan(conn: sqlite3.Connection, wh: str = "WH-01", item: str = "ITM-FP-STD") -> dict:
    """First open planned plan for a warehouse-item, read from the database (no hard-coded IDs)."""
    plans = fc.open_plans(conn, wh, item, TODAY)
    assert len(plans) > 0, "seed has no open plan for this pair"
    return db.row_dict(conn, "distribution_plans", "plan_id", str(plans.iloc[0]["plan_id"]))  # type: ignore[return-value]


def _other_site(conn: sqlite3.Connection, site: str) -> str:
    return str(conn.execute("SELECT site_id FROM sites WHERE site_id <> ? ORDER BY site_id LIMIT 1", (site,)).fetchone()[0])


def _plan_status(conn: sqlite3.Connection, plan_id: str) -> str:
    return str(conn.execute("SELECT status FROM distribution_plans WHERE plan_id=?", (plan_id,)).fetchone()[0])


def _ledger_on_hand(conn: sqlite3.Connection, wh: str, item: str) -> int:
    return int(
        conn.execute(
            "SELECT COALESCE(SUM(CASE movement_type WHEN 'received' THEN quantity ELSE -quantity END),0) "
            "FROM stock_movements WHERE warehouse_id=? AND item_id=?",
            (wh, item),
        ).fetchone()[0]
    )


# -- the forecast on seed data ----------------------------------------------------------
def test_pair_with_plans_uses_only_plans_until_last_plan_date(conn: sqlite3.Connection) -> None:
    plans = fc.open_plans(conn, "WH-01", "ITM-FP-STD", TODAY)
    assert len(plans) > 0
    f = fc.forecast_pairs(conn, "WH-01", "ITM-FP-STD", 28, 90, TODAY)[0]
    last = date.fromisoformat(str(plans["planned_date"].max()))
    span = (last - TODAY).days + 1
    total = int(plans[plans["planned_date"] < (TODAY + timedelta(days=90)).isoformat()]["planned_quantity"].sum())
    assert sum(f.series[:span]) == f.planned_in_horizon == total  # planned quantities only, no recent demand added
    assert f.series[span] == pytest.approx(f.daily_demand)  # after the last plan: recent rate
    # a pair with no plans uses the recent rate on every day
    g = fc.forecast_pairs(conn, "WH-01", "ITM-RTE", 28, 90, TODAY)[0]
    assert g.planned_in_horizon == 0
    assert all(x == pytest.approx(g.daily_demand) for x in g.series)


def test_seed_forecast_consistent_with_ledger(conn: sqlite3.Connection) -> None:
    f = fc.forecast_pairs(conn, "WH-01", "ITM-FP-STD", 28, 90, TODAY)[0]
    assert f.unit == "parcel"
    assert f.on_hand == stock.on_hand_qty(conn, "WH-01", "ITM-FP-STD") == _ledger_on_hand(conn, "WH-01", "ITM-FP-STD")
    assert f.daily_demand == pytest.approx(fc.recent_net_demand(conn, 28, TODAY).get(("WH-01", "ITM-FP-STD"), 0) / 28)
    # rebuild the projection from the plan rows and compare
    plans = fc.open_plans(conn, "WH-01", "ITM-FP-STD", TODAY)
    by_date: dict[date, int] = {}
    for r in plans.itertuples():
        d = date.fromisoformat(r.planned_date)
        by_date[d] = by_date.get(d, 0) + int(r.planned_quantity)
    series = fc.daily_demand_series(f.daily_demand, by_date, TODAY, 90)
    idx = fc.first_stockout_index(f.on_hand, series)
    assert f.stockout_date == (TODAY + timedelta(days=idx) if idx is not None else None)
    if f.stockout_date:
        assert f.reorder_by == f.stockout_date - timedelta(days=f.lead_time_days)
    assert f.suggested_replenishment % 10 == 0 and f.suggested_replenishment >= 0
    assert f.below_threshold == (f.on_hand < f.reorder_threshold)
    table = fc.forecast_table(conn, "WH-03", today=TODAY).set_index("item_id")
    for item_id, row in table.iterrows():
        oh = _ledger_on_hand(conn, "WH-03", str(item_id))
        assert row["on_hand"] == oh
        assert bool(row["below_threshold"]) == (oh < row["reorder_threshold"])
    assert (table["unit"] != "").all()


def test_plans_are_per_item_in_their_own_unit(conn: sqlite3.Connection) -> None:
    """A meal plan (as Project 11 may write) only affects ITM-RTE, in meals, not parcel items."""
    parcels_before = fc.forecast_pairs(conn, "WH-01", "ITM-FP-STD", 28, 90, TODAY)[0]
    meals_before = fc.forecast_pairs(conn, "WH-01", "ITM-RTE", 28, 90, TODAY)[0]
    _plan(conn, "PLN-T200", "WH-01", "ITM-RTE", TODAY + timedelta(days=2), 120)
    meals = fc.forecast_pairs(conn, "WH-01", "ITM-RTE", 28, 90, TODAY)[0]
    parcels = fc.forecast_pairs(conn, "WH-01", "ITM-FP-STD", 28, 90, TODAY)[0]
    assert meals.unit == "meal" and meals.planned_in_horizon == meals_before.planned_in_horizon + 120
    assert parcels.planned_in_horizon == parcels_before.planned_in_horizon and parcels.unit == "parcel"


def test_future_plan_changes_forecast(conn: sqlite3.Connection) -> None:
    before = fc.forecast_pairs(conn, "WH-02", "ITM-RTE", 28, 90, TODAY)[0]
    assert before.planned_in_horizon == 0
    _plan(conn, "PLN-T100", "WH-02", "ITM-RTE", TODAY + timedelta(days=5), 400, site="SITE-02")  # as Project 11 would write it
    after = fc.forecast_pairs(conn, "WH-02", "ITM-RTE", 28, 90, TODAY)[0]
    assert after.planned_in_horizon == 400
    assert before.stockout_date is not None and after.stockout_date is not None
    assert after.stockout_date < before.stockout_date  # 414 on hand, 400 leaves on day 5, little left after
    # a cancelled plan does not count
    conn.execute("UPDATE distribution_plans SET status='cancelled' WHERE plan_id='PLN-T100'")
    assert fc.forecast_pairs(conn, "WH-02", "ITM-RTE", 28, 90, TODAY)[0].planned_in_horizon == 0


# -- receipts and dispatches -----------------------------------------------------------------
def test_receipt_raises_on_hand_and_moves_stockout_later(db_path: Path) -> None:
    wh, item = "WH-01", "ITM-FP-STD"
    with db.transaction() as c:
        before = fc.forecast_pairs(c, wh, item, 28, 90, TODAY)[0]
        audits = _count(c, "audit_log")
    assert before.stockout_date is not None  # seed: the pair runs out within 90 days
    with db.transaction() as c:
        movement_id = fc.receive_stock(c, wh, item, 100, confirmed_by=STAFF, reference="DN-TEST")
    with db.transaction() as c:
        assert stock.on_hand_qty(c, wh, item) == before.on_hand + 100
        row = c.execute("SELECT * FROM stock_movements WHERE movement_id=?", (movement_id,)).fetchone()
        assert (row["movement_type"], row["reason"], row["quantity"], row["recorded_by"]) == ("received", "supplier_delivery", 100, STAFF)
        log = c.execute("SELECT * FROM audit_log ORDER BY rowid DESC LIMIT 1").fetchone()
        assert _count(c, "audit_log") == audits + 1
        assert (log["entity_table"], log["channel"], log["actor_id"]) == ("stock_movements", "app:10", STAFF)
        after = fc.forecast_pairs(c, wh, item, 28, 90, TODAY)[0]
    assert after.stockout_date is None or after.stockout_date > before.stockout_date


@pytest.mark.parametrize("who", [None, "", "  ", "NOPE-99", "OPR-01"])
def test_receipt_refused_without_confirmation(db_path: Path, who: str | None) -> None:
    with db.transaction() as c:
        moves, audits = _count(c, "stock_movements"), _count(c, "audit_log")
    with pytest.raises(ConfirmationRequired), db.transaction() as c:
        fc.receive_stock(c, "WH-01", "ITM-FP-STD", 10, confirmed_by=who)
    with db.transaction() as c:
        assert (_count(c, "stock_movements"), _count(c, "audit_log")) == (moves, audits)


def test_receipt_rejects_bad_quantity_and_reason(db_path: Path) -> None:
    with pytest.raises(ValueError), db.transaction() as c:
        fc.receive_stock(c, "WH-01", "ITM-FP-STD", 0, confirmed_by=STAFF)
    with pytest.raises(ValueError), db.transaction() as c:
        fc.receive_stock(c, "WH-01", "ITM-FP-STD", 5, confirmed_by=STAFF, reason="write_off")


def test_dispatch_lowers_on_hand_and_marks_plan_dispatched(db_path: Path) -> None:
    with db.transaction() as c:
        plan = _first_plan(c)
        wh, item, site, qty, plan_id = plan["warehouse_id"], plan["item_id"], plan["site_id"], plan["planned_quantity"], plan["plan_id"]
        have = stock.on_hand_qty(c, wh, item)
        if qty > have:  # keep the test independent of the seeded balance
            fc.receive_stock(c, wh, item, qty - have, confirmed_by=STAFF)
    with db.transaction() as c:
        before, audits = stock.on_hand_qty(c, wh, item), _count(c, "audit_log")
    with db.transaction() as c:
        dispatch_id = fc.dispatch_stock(c, wh, site, item, qty, confirmed_by=STAFF, plan_id=plan_id)
    with db.transaction() as c:
        assert stock.on_hand_qty(c, wh, item) == before - qty
        assert _plan_status(c, plan_id) == "dispatched"
        assert c.execute("SELECT plan_id FROM dispatches WHERE dispatch_id=?", (dispatch_id,)).fetchone()[0] == plan_id
        move = c.execute("SELECT * FROM stock_movements WHERE dispatch_id=?", (dispatch_id,)).fetchone()
        assert (move["movement_type"], move["reason"], move["quantity"]) == ("dispatched", "dispatch", qty)
        logs = c.execute("SELECT entity_table, channel FROM audit_log ORDER BY rowid DESC LIMIT 2").fetchall()
        assert _count(c, "audit_log") == audits + 2
        assert {(r["entity_table"], r["channel"]) for r in logs} == {("dispatches", "app:10"), ("distribution_plans", "app:10")}
        # the plan no longer counts as planned demand, and cannot be dispatched twice
        assert plan_id not in set(fc.open_plans(c, wh, item, TODAY)["plan_id"])
    with pytest.raises(ValueError), db.transaction() as c:
        fc.dispatch_stock(c, wh, site, item, 1, confirmed_by=STAFF, plan_id=plan_id)


def test_dispatch_without_plan_and_plan_mismatch(db_path: Path) -> None:
    with db.transaction() as c:
        plan = _first_plan(c)
        before = stock.on_hand_qty(c, "WH-01", "ITM-HYG")
        wrong_site = _other_site(c, plan["site_id"])
    with db.transaction() as c:
        fc.dispatch_stock(c, "WH-01", "SITE-01", "ITM-HYG", 10, confirmed_by="CRD-01")
    with db.transaction() as c:
        assert stock.on_hand_qty(c, "WH-01", "ITM-HYG") == before - 10
    with pytest.raises(ValueError), db.transaction() as c:  # plan is for a different site
        fc.dispatch_stock(c, plan["warehouse_id"], wrong_site, plan["item_id"], 5, confirmed_by=STAFF, plan_id=plan["plan_id"])
    with pytest.raises(ValueError), db.transaction() as c:
        fc.dispatch_stock(c, "WH-01", "SITE-01", "ITM-FP-STD", 5, confirmed_by=STAFF, plan_id="PLN-NOPE")


@pytest.mark.parametrize("who", [None, "", "NOPE-99", "OPR-01"])
def test_dispatch_refused_without_confirmation(db_path: Path, who: str | None) -> None:
    with db.transaction() as c:
        plan = _first_plan(c)
        moves, disp, audits = _count(c, "stock_movements"), _count(c, "dispatches"), _count(c, "audit_log")
    with pytest.raises(ConfirmationRequired), db.transaction() as c:
        fc.dispatch_stock(c, plan["warehouse_id"], plan["site_id"], plan["item_id"], 1, confirmed_by=who, plan_id=plan["plan_id"])
    with db.transaction() as c:
        assert (_count(c, "stock_movements"), _count(c, "dispatches"), _count(c, "audit_log")) == (moves, disp, audits)
        assert _plan_status(c, plan["plan_id"]) == "planned"


def test_dispatch_beyond_on_hand_is_refused(db_path: Path) -> None:
    with db.transaction() as c:
        on_hand = stock.on_hand_qty(c, "WH-03", "ITM-MED")
        moves, disp = _count(c, "stock_movements"), _count(c, "dispatches")
    with pytest.raises(stock.InsufficientStock), db.transaction() as c:
        fc.dispatch_stock(c, "WH-03", "SITE-01", "ITM-MED", on_hand + 1, confirmed_by=STAFF)
    with db.transaction() as c:
        assert (_count(c, "stock_movements"), _count(c, "dispatches")) == (moves, disp)
        assert stock.on_hand_qty(c, "WH-03", "ITM-MED") == on_hand


def test_clock_is_frozen_for_these_tests(db_path: object) -> None:
    assert clock.today() == TODAY


# -- the app -----------------------------------------------------------------------------------
def test_app_starts(app_test) -> None:  # noqa: ANN001
    at = app_test(10).run()
    assert not at.exception
    assert len(at.dataframe) >= 3  # stock, forecast, ledger
    assert any("How this is calculated" in e.label for e in at.expander)


def test_app_other_warehouse_and_window(app_test) -> None:  # noqa: ANN001
    at = app_test(10).run()
    at.sidebar.selectbox[0].select("WH-03").run()
    assert not at.exception
    at.sidebar.number_input[0].set_value(14).run()
    assert not at.exception


def test_app_receipt_form_with_confirmation(app_test, conn: sqlite3.Connection) -> None:  # noqa: ANN001
    before = stock.on_hand_qty(conn, "WH-01", "ITM-FP-STD")
    at = app_test(10).run()
    at.selectbox(key="p10_r_item").select("ITM-FP-STD")
    at.number_input(key="p10_r_qty").set_value(40)
    at.run()
    # unticked: nothing changes
    at.button[0].click().run()
    assert not at.exception
    assert stock.on_hand_qty(conn, "WH-01", "ITM-FP-STD") == before
    at.checkbox(key="confirm_tick_receipt").check()
    at.selectbox(key="confirm_staff_receipt").select("WHO-01")
    at.button[0].click().run()
    assert not at.exception
    assert stock.on_hand_qty(conn, "WH-01", "ITM-FP-STD") == before + 40


def test_app_dispatch_form_against_plan(app_test, conn: sqlite3.Connection) -> None:  # noqa: ANN001
    plan = _first_plan(conn, "WH-01", "ITM-FP-STD")
    plan_id, qty = plan["plan_id"], int(plan["planned_quantity"])
    before = stock.on_hand_qty(conn, "WH-01", "ITM-FP-STD")
    if before < qty:
        pytest.skip("seeded stock below the plan quantity")
    at = app_test(10).run()
    at.selectbox(key="p10_d_plan").select(plan_id)
    at.run()
    at.checkbox(key="confirm_tick_dispatch").check()
    at.selectbox(key="confirm_staff_dispatch").select("WHO-01")
    at.button[1].click().run()
    assert not at.exception
    assert stock.on_hand_qty(conn, "WH-01", "ITM-FP-STD") == before - qty
    assert _plan_status(conn, plan_id) == "dispatched"
