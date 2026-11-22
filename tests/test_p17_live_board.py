"""Tests for Project 17: operations room live board (read-only)."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta

import p17_board as board
import p17_view as view

from shared import clock, db, fleet, simulator

TABLES = (
    "position_reports",
    "service_requests",
    "alerts",
    "advisories",
    "stock_movements",
    "distributions",
    "convoys",
    "settings",
    "audit_log",
)


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def _scenario(conn: sqlite3.Connection) -> dict:
    return json.loads(db.get_setting("demo.scenarios", conn=conn))["p17"]


def _state(conn: sqlite3.Connection, vehicle_id: str) -> str:
    return board.vehicle_positions(conn).set_index("vehicle_id").loc[vehicle_id, "state"]


def _last(conn: sqlite3.Connection, vehicle_id: str) -> str:
    return conn.execute("SELECT reported_at FROM v_last_position WHERE vehicle_id = ?", (vehicle_id,)).fetchone()[0]


def test_vehicle_not_flagged_exactly_at_limit_flagged_just_past(conn: sqlite3.Connection) -> None:
    limit = fleet.stale_limit_minutes(conn)
    last = clock.parse(_last(conn, "VEH-01"))
    clock.set_now(last + timedelta(minutes=limit))
    assert _state(conn, "VEH-01") != "STALE"
    clock.set_now(last + timedelta(minutes=limit, seconds=1))
    assert _state(conn, "VEH-01") == "STALE"


def test_seeded_stale_vehicle_is_flagged_at_start(conn: sqlite3.Connection) -> None:
    df = board.vehicle_positions(conn).set_index("vehicle_id")
    assert bool(df.loc[_scenario(conn)["already_stale_vehicle_id"], "stale"])
    assert not bool(df.loc["VEH-01", "stale"])


def test_stopped_simulator_walkthrough(conn: sqlite3.Connection) -> None:
    """Simulator off: nothing new arrives, and after the limit the vehicle is flagged on the board."""
    switch = _scenario(conn)["simulator_switch_setting"]
    with db.transaction() as c:
        c.execute("UPDATE settings SET value = '0' WHERE key = ?", (switch,))
    before = conn.execute("SELECT COUNT(*) FROM position_reports").fetchone()[0]
    limit = fleet.stale_limit_minutes(conn)
    last = clock.parse(_last(conn, "VEH-03"))
    clock.set_now(last + timedelta(minutes=limit - 1))
    assert _state(conn, "VEH-03") != "STALE"
    clock.set_now(last + timedelta(minutes=limit + 1))
    with db.transaction() as c:
        assert simulator.is_enabled(c) is False
        simulator.step(c)  # switched off: writes nothing
    assert conn.execute("SELECT COUNT(*) FROM position_reports").fetchone()[0] == before
    assert _state(conn, "VEH-03") == "STALE"
    assert "VEH-03 STALE" in view.map_caption(board.vehicle_positions(conn), clock.now())


def test_switching_simulator_back_on_clears_stale_flags(conn: sqlite3.Connection) -> None:
    """Walkthrough step 4: after the stop, one simulator run clears the flags; the unsimulated vehicle stays stale."""
    scenario = _scenario(conn)
    with db.transaction() as c:
        c.execute("UPDATE settings SET value = '0' WHERE key = ?", (scenario["simulator_switch_setting"],))
    limit = fleet.stale_limit_minutes(conn)
    clock.set_now(clock.now() + timedelta(minutes=limit + 1))
    df = board.vehicle_positions(conn).set_index("vehicle_id")
    assert bool(df["stale"].all())
    with db.transaction() as c:
        simulator.set_enabled(c, True, "CRD-01")
        assert simulator.step(c) == len(scenario["simulated_vehicle_ids"])
    df = board.vehicle_positions(conn).set_index("vehicle_id")
    assert list(df[df["stale"]].index) == [scenario["already_stale_vehicle_id"]]
    assert all(not r["stale_vehicles"] for r in board.convoy_rows(conn))
    assert not board.position_freshness(conn).stale


def test_panel_freshness_turns_stale_past_its_limit() -> None:
    now = clock.parse("2026-11-16T10:00:00")
    newest = "2026-11-16T09:00:00"
    assert board.freshness(newest, 60.0, now).stale is False  # exactly at the limit
    assert board.freshness(newest, 59.9, now).stale is True
    assert board.freshness(None, 60.0, now).stale is True
    html_fresh = view.panel("Demo", board.freshness(newest, 90.0, now), "body", now)
    html_stale = view.panel("Demo", board.freshness(newest, 30.0, now), "body", now)
    assert "p17-stale" not in html_fresh and "Updated 60 min ago" in html_fresh
    assert "p17-stale" in html_stale and "STALE" in html_stale


def test_panel_source_stops_reporting(conn: sqlite3.Connection) -> None:
    """Move time past each panel's limit: each panel's badge goes stale on its own source."""
    pos = board.position_freshness(conn)
    assert not pos.stale
    clock.set_now(clock.now() + timedelta(minutes=pos.limit_minutes + 1))
    assert board.position_freshness(conn).stale
    assert not board.advisory_freshness(conn).stale
    clock.set_now(clock.now() + timedelta(days=3))
    for fn in (
        board.convoy_freshness,
        board.request_freshness,
        board.advisory_freshness,
        board.stock_freshness,
        board.distribution_freshness,
    ):
        assert fn(conn).stale


def test_board_writes_nothing(conn: sqlite3.Connection) -> None:
    before = _counts(conn)
    ro = board.open_readonly()
    try:
        board.vehicle_positions(ro)
        board.position_freshness(ro)
        board.areas_frame(ro)
        board.convoy_rows(ro)
        board.convoy_freshness(ro)
        board.request_summary(ro)
        board.request_freshness(ro)
        board.active_advisories(ro)
        board.advisory_freshness(ro)
        board.stock_below_reorder(ro)
        board.open_alerts(ro)
        board.stock_freshness(ro)
        board.distributions_today(ro)
        board.distribution_freshness(ro)
        board.refresh_seconds(ro)
        try:
            ro.execute("DELETE FROM alerts")
            wrote = True
        except sqlite3.OperationalError:
            wrote = False
        assert wrote is False
    finally:
        ro.close()
    assert _counts(conn) == before


def test_distributions_keep_parcels_and_meals_separate(conn: sqlite3.Connection) -> None:
    day = clock.today().isoformat()
    figures = board.distributions_today(conn, day)
    parcels = conn.execute("SELECT COALESCE(SUM(parcel_quantity),0) FROM distributions WHERE distribution_date = ?", (day,)).fetchone()[0]
    meals = conn.execute("SELECT COALESCE(SUM(rte_meal_quantity),0) FROM distributions WHERE distribution_date = ?", (day,)).fetchone()[0]
    assert figures["parcels"]["done"] == parcels
    assert figures["meals"]["done"] == meals
    assert figures["parcels"]["unit"] == "parcels" and figures["meals"]["unit"] == "meals"
    assert meals > 0 and parcels > 0
    assert all(f["done"] != parcels + meals for f in (figures["parcels"], figures["meals"]))
    # Parcel plan counts parcel items only; no meal plan exists today, so it stays 0 rather than borrowing parcels.
    assert figures["meals"]["planned"] == 0 and figures["meals"]["percent"] is None


def test_stock_alert_is_strictly_below_threshold(conn: sqlite3.Connection) -> None:
    row = db.query_all("SELECT warehouse_id, item_id, reorder_threshold FROM item_lead_times LIMIT 1", conn=conn)[0]
    key = (row["warehouse_id"], row["item_id"])
    item_name = conn.execute("SELECT name FROM items WHERE item_id = ?", (key[1],)).fetchone()[0]
    sql = "INSERT INTO stock_movements(warehouse_id,item_id,moved_at,movement_type,reason,quantity) VALUES (?,?,?,?,?,?)"

    def on_hand() -> int:
        return conn.execute("SELECT on_hand FROM v_stock_on_hand WHERE warehouse_id=? AND item_id=?", key).fetchone()[0]

    def listed() -> bool:
        return any(r["warehouse_id"] == key[0] and r["item"] == item_name for r in board.stock_below_reorder(conn))

    with db.transaction() as c:
        delta = row["reorder_threshold"] - on_hand()
        if delta > 0:
            c.execute(sql, (*key, clock.iso(), "received", "supplier_delivery", delta))
        elif delta < 0:
            c.execute(sql, (*key, clock.iso(), "dispatched", "write_off", -delta))
    assert on_hand() == row["reorder_threshold"]
    assert not listed()  # at the threshold: no alert
    with db.transaction() as c:
        c.execute(sql, (*key, clock.iso(), "dispatched", "write_off", 1))
    assert listed()


def test_advisories_list_only_above_normal(conn: sqlite3.Connection) -> None:
    rows = board.active_advisories(conn)
    assert rows and all(r["level"] >= 1 for r in rows)
    assert [r["level"] for r in rows] == sorted((r["level"] for r in rows), reverse=True)


def test_request_summary_counts_overdue(conn: sqlite3.Connection) -> None:
    s = board.request_summary(conn)
    assert s["overdue_count"] == len(s["overdue"]) >= 1
    assert s["open"] >= s["unassigned"]


def test_app_starts(app_test) -> None:  # noqa: ANN001
    at = app_test(17).run()
    assert not at.exception


def test_panel_limit_reads_settings_with_constant_fallback(conn: sqlite3.Connection) -> None:
    assert board.panel_limit(conn, "requests") == board.PANEL_LIMIT_MINUTES["requests"]
    with conn:
        db.set_setting(conn, "board_panel_limit_requests_minutes", "90", "ADM-01")
    assert board.panel_limit(conn, "requests") == 90.0
    with conn:
        db.set_setting(conn, "board_panel_limit_requests_minutes", "not a number", "ADM-01")
    assert board.panel_limit(conn, "requests") == board.PANEL_LIMIT_MINUTES["requests"]
    with conn:
        db.set_setting(conn, "board_panel_limit_requests_minutes", "0", "ADM-01")
    assert board.panel_limit(conn, "requests") == board.PANEL_LIMIT_MINUTES["requests"]


def test_area_names_near_a_vehicle_are_dropped_from_the_map(conn: sqlite3.Connection) -> None:
    positions = board.vehicle_positions(conn)
    areas = board.areas_frame(conn)
    kept = board.label_free_areas(areas, positions)
    assert len(kept) <= len(areas)
    for a in kept.itertuples():
        assert all(((a.x_km - v.x_km) ** 2 + (a.y_km - v.y_km) ** 2) ** 0.5 >= 6.0 for v in positions.dropna(subset=["x_km"]).itertuples())
