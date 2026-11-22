"""Shared supply-chain rules (Group C): parcel plan, parcel-only coverage, returns, ledger balance."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from shared import db, food, stock


def test_parcel_plan_by_household_size() -> None:
    a = food.default_assumptions()
    expected = {
        1: ("ITM-FP-SML", 1),
        2: ("ITM-FP-SML", 1),
        3: ("ITM-FP-STD", 1),
        5: ("ITM-FP-STD", 1),
        6: ("ITM-FP-STD", 2),
        10: ("ITM-FP-STD", 2),
        11: ("ITM-FP-STD", 3),
    }
    for size, plan in expected.items():
        assert food.parcel_plan(size, a) == plan


def test_parcel_plan_follows_edited_assumptions() -> None:
    a = food.default_assumptions() | {"small_parcel_max_household_size": 1.0, "persons_per_standard_parcel": 4.0}
    assert food.parcel_plan(2, a) == ("ITM-FP-STD", 1)
    assert food.parcel_plan(5, a) == ("ITM-FP-STD", 2)
    with pytest.raises(ValueError):
        food.parcel_plan(0, a)


def test_parcel_coverage_ignores_meals_and_follows_assumptions() -> None:
    a = food.default_assumptions()
    with_meals = food.coverage(5, "ITM-FP-STD", 1, 30, a)
    assert with_meals.parcel_days == 30 and with_meals.meal_days == 2
    assert food.coverage(5, "ITM-FP-STD", 1, 0, a).parcel_days == with_meals.parcel_days
    edited = a | {"person_days.ITM-FP-STD": 120.0}
    assert food.coverage(5, "ITM-FP-STD", 1, 30, edited).parcel_days == 24
    assert food.coverage(2, "ITM-FP-SML", 1, 0, a).parcel_days == 30
    assert food.coverage(4, None, 0, 24, a) == (0, 2)
    assert food.coverage_end_date(date(2026, 11, 16), 30) == date(2026, 12, 15)
    with pytest.raises(ValueError):
        food.coverage(4, None, 1, 0, a)


def test_assumptions_have_one_source(conn: sqlite3.Connection) -> None:
    assert conn.execute("SELECT COUNT(*) FROM items WHERE person_days_per_unit IS NOT NULL").fetchone()[0] == 0
    conn.execute("UPDATE forecast_assumptions SET value = 100 WHERE assumption_key = 'person_days.ITM-FP-STD'")
    assert food.person_days_per_parcel(food.load_assumptions(conn), "ITM-FP-STD") == 100.0


def test_seeded_coverage_is_parcel_days_only(conn: sqlite3.Connection) -> None:
    a = food.load_assumptions(conn)
    rows = conn.execute(
        "SELECT household_size, parcel_item_id, parcel_quantity, rte_meal_quantity, expected_coverage_days FROM distributions"
    ).fetchall()
    with_meals = 0
    for size, item, parcels, meals, stored in rows:
        assert stored == food.coverage(size, item, parcels, meals, a).parcel_days
        with_meals += meals > 0
    assert with_meals > 0  # the check above covers rows that also received meals


def test_seeded_parcels_follow_parcel_plan(conn: sqlite3.Connection) -> None:
    a = food.load_assumptions(conn)
    s = json.loads(db.get_setting("demo.scenarios", conn=conn))["p12"]
    rows = conn.execute(
        """SELECT distribution_id, household_id, household_size, parcel_item_id, parcel_quantity FROM distributions
           WHERE parcel_quantity > 0"""
    ).fetchall()
    off = [r for r in rows if food.parcel_plan(r[2], a) != (r[3], r[4])]
    # Only the two Project 12 discrepancy rows (a repeat and an unmatched record, 1 parcel each) may differ.
    assert all(r[1] in (s["duplicate_household_id"], s["unmatched_household_id"]) for r in off)


def test_ledger_balances_by_warehouse_and_item(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """SELECT v.warehouse_id, v.item_id, v.on_hand,
                  (SELECT COALESCE(SUM(quantity),0) FROM stock_movements m WHERE m.warehouse_id=v.warehouse_id AND m.item_id=v.item_id AND movement_type='received')
                - (SELECT COALESCE(SUM(quantity),0) FROM stock_movements m WHERE m.warehouse_id=v.warehouse_id AND m.item_id=v.item_id AND movement_type='dispatched'),
                  (SELECT COALESCE(SUM(quantity),0) FROM dispatches d WHERE d.warehouse_id=v.warehouse_id AND d.item_id=v.item_id),
                  (SELECT COALESCE(SUM(quantity),0) FROM stock_movements m WHERE m.warehouse_id=v.warehouse_id AND m.item_id=v.item_id AND reason='dispatch'),
                  (SELECT COALESCE(SUM(r.quantity),0) FROM distribution_returns r JOIN dispatches d ON d.dispatch_id=r.dispatch_id
                    WHERE d.warehouse_id=v.warehouse_id AND d.item_id=v.item_id),
                  (SELECT COALESCE(SUM(quantity),0) FROM stock_movements m WHERE m.warehouse_id=v.warehouse_id AND m.item_id=v.item_id AND reason='distribution_return')
           FROM v_stock_on_hand v"""
    ).fetchall()
    assert len(rows) == 3 * 8
    for wh, item, on_hand, ledger, dispatched, ledger_dispatch, returned, ledger_return in rows:
        assert on_hand == ledger >= 0, (wh, item)
        assert dispatched == ledger_dispatch, (wh, item)
        assert returned == ledger_return, (wh, item)


def test_record_return_writes_both_tables(db_path: Path) -> None:
    with db.transaction() as conn:
        d = conn.execute(
            "SELECT dispatch_id, warehouse_id, item_id, quantity FROM dispatches WHERE item_id='ITM-FP-STD' ORDER BY dispatch_id DESC LIMIT 1"
        ).fetchone()
        did, wh, item, qty = d[0], d[1], d[2], d[3]
        already = conn.execute("SELECT COALESCE(SUM(quantity),0) FROM distribution_returns WHERE dispatch_id=?", (did,)).fetchone()[0]
        before = stock.on_hand_qty(conn, wh, item)
        rid = stock.record_return(conn, did, 2, "WHO-01", channel="test")
    with db.connect() as conn:
        assert stock.on_hand_qty(conn, wh, item) == before + 2
        assert tuple(conn.execute("SELECT quantity, dispatch_id FROM distribution_returns WHERE return_id=?", (rid,)).fetchone()) == (
            2,
            did,
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM stock_movements WHERE reason='distribution_return' AND reference=? AND quantity=2", (rid,)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM audit_log WHERE entity_table='distribution_returns' AND entity_id=?", (rid,)).fetchone()[0]
            == 1
        )
    with pytest.raises(ValueError), db.transaction() as conn:
        stock.record_return(conn, did, qty - already - 2 + 1, "WHO-01")
    with pytest.raises(ValueError), db.transaction() as conn:
        stock.record_return(conn, "DSP-9999", 1, "WHO-01")
