"""Project 14: fleet readiness and tracking. Clock frozen at 2026-11-16 10:00 (conftest.ANCHOR)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta

import p14_fleet as fl
import pytest

from shared import clock, db, fleet
from shared.confirm import ConfirmationRequired

TODAY = date(2026, 11, 16)
NOW = datetime(2026, 11, 16, 10, 0, 0)
STAFF = "CRD-01"


def _flag(conn: sqlite3.Connection, vehicle_id: str, now: datetime = NOW) -> bool:
    table = fl.position_table(conn, now).set_index("vehicle_id")
    return bool(table.loc[vehicle_id, "stale"])


def _set_last_report(conn: sqlite3.Connection, vehicle_id: str, at: datetime) -> None:
    conn.execute("DELETE FROM position_reports WHERE vehicle_id = ?", (vehicle_id,))
    fleet.record_position(conn, vehicle_id, 10, 10, source="api", at=at.isoformat(timespec="seconds"))


def _audit_rows(conn: sqlite3.Connection, action: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM audit_log WHERE channel = 'app:14' AND action = ?", (action,)).fetchall()


# -- readiness -------------------------------------------------------------
def test_scenario_ids_match_the_seed(conn: sqlite3.Connection) -> None:
    ids = json.loads(db.get_setting("demo.scenarios", conn=conn))["p14"]
    assert ids == {"stale_vehicle_id": "VEH-07", "convoy_in_progress": "CNV-002"}


def test_readiness_reasons_on_the_seed(conn: sqlite3.Connection) -> None:
    ready = fleet.fleet_readiness(conn, TODAY)
    assert ready["VEH-04"].status == "not_ready"
    assert any("Service overdue" in r for r in ready["VEH-04"].reasons)
    assert ready["VEH-06"].status == "not_ready"
    assert any("DEF-001" in r and "Grounding" in r for r in ready["VEH-06"].reasons)
    assert ready["VEH-07"].status == "not_ready"
    assert any("Insurance expired" in r for r in ready["VEH-07"].reasons)
    assert ready["VEH-05"].status == "due_for_service"
    assert ready["VEH-01"].status == "ready"


def test_fleet_table_shows_status_and_reasons(conn: sqlite3.Connection) -> None:
    table = fl.fleet_table(conn, TODAY).set_index("Vehicle")
    assert len(table) == 8
    assert table.loc["VEH-06", "Status"] == "Not ready"
    assert "DEF-001" in table.loc["VEH-06", "Why"]
    assert table.loc["VEH-06", "Open defects"] == "DEF-001 (grounding)"
    assert table.loc["VEH-05", "Status"] == "Due for service"
    assert table.loc["VEH-01", "Why"] == "All checks pass"
    assert table.loc["VEH-01", "Next service by distance (km)"] == 90100


def test_a_later_date_changes_readiness(conn: sqlite3.Connection) -> None:
    today = fl.fleet_table(conn, TODAY).set_index("Vehicle")["Status"]
    later = fl.fleet_table(conn, TODAY + timedelta(days=400)).set_index("Vehicle")["Status"]
    assert today["VEH-01"] == "Ready"
    assert later["VEH-01"] == "Not ready"  # service interval and documents run out
    assert (today != later).sum() >= 4


def test_explain_matches_shared_rule(conn: sqlite3.Connection) -> None:
    assert fl.explain(conn, "VEH-06", TODAY).reasons == fleet.fleet_readiness(conn, TODAY)["VEH-06"].reasons


# -- stale flag (Part 4) -----------------------------------------------------
def test_stale_flag_at_the_configured_limit(conn: sqlite3.Connection) -> None:
    assert fleet.stale_limit_minutes(conn) == 15
    _set_last_report(conn, "VEH-01", NOW - timedelta(minutes=15))
    assert _flag(conn, "VEH-01") is False  # exactly at the limit
    _set_last_report(conn, "VEH-01", NOW - timedelta(minutes=15, seconds=1))
    assert _flag(conn, "VEH-01") is True
    _set_last_report(conn, "VEH-01", NOW - timedelta(minutes=14))
    assert _flag(conn, "VEH-01") is False


def test_vehicle_without_report_is_stale(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM position_reports WHERE vehicle_id = 'VEH-03'")
    row = fl.position_table(conn, NOW).set_index("vehicle_id").loc["VEH-03"]
    assert bool(row["stale"]) is True
    assert row["minutes_since_report"] is None or row["minutes_since_report"] != row["minutes_since_report"]


def test_changing_the_setting_changes_the_flag(conn: sqlite3.Connection) -> None:
    _set_last_report(conn, "VEH-01", NOW - timedelta(minutes=30))
    assert _flag(conn, "VEH-01") is True
    fl.set_stale_limit(conn, 45, STAFF)
    assert fleet.stale_limit_minutes(conn) == 45
    assert _flag(conn, "VEH-01") is False
    fl.set_stale_limit(conn, 20, STAFF)
    assert _flag(conn, "VEH-01") is True


def test_seeded_stale_vehicle_with_clock_frozen(conn: sqlite3.Connection) -> None:
    stale_id = json.loads(db.get_setting("demo.scenarios", conn=conn))["p14"]["stale_vehicle_id"]
    table = fl.position_table(conn, clock.now()).set_index("vehicle_id")
    assert bool(table.loc[stale_id, "stale"]) is True
    assert bool(table.loc[stale_id, "tracker_simulated"]) is False
    assert 290 <= table.loc[stale_id, "minutes_since_report"] <= 310  # about five hours
    assert table["stale"].sum() == 1  # only the tracker that is off


def test_stale_limit_needs_confirmation_and_role(conn: sqlite3.Connection) -> None:
    with pytest.raises(ConfirmationRequired):
        fl.set_stale_limit(conn, 30, None)
    with pytest.raises(ConfirmationRequired):
        fl.set_stale_limit(conn, 30, "FW-01")
    with pytest.raises(ValueError):
        fl.set_stale_limit(conn, 0, STAFF)
    assert fleet.stale_limit_minutes(conn) == 15
    fl.set_stale_limit(conn, 30, "ADM-01")
    assert _audit_rows(conn, "update")  # settings change audited on channel app:14


# -- positions written by the API ------------------------------------------
def test_new_position_becomes_last_and_clears_stale(conn: sqlite3.Connection) -> None:
    assert _flag(conn, "VEH-07") is True
    fleet.record_position(conn, "VEH-07", 55.5, 60.25, source="api", speed_kmh=30.0)
    row = fl.position_table(conn, clock.now()).set_index("vehicle_id").loc["VEH-07"]
    assert bool(row["stale"]) is False
    assert (row["x_km"], row["y_km"], row["source"]) == (55.5, 60.25, "api")
    assert row["minutes_since_report"] == 0
    last = conn.execute("SELECT x_km, y_km FROM v_last_position WHERE vehicle_id = 'VEH-07'").fetchone()
    assert (last["x_km"], last["y_km"]) == (55.5, 60.25)


# -- trip log --------------------------------------------------------------
def test_trip_log_for_cnv_002(conn: sqlite3.Connection) -> None:
    log = fl.trip_log(conn, "CNV-002")
    assert log["convoy"]["status"] == "departed"
    assert list(log["events"]["status"]) == ["planned", "approved", "departed"]
    assert list(log["vehicles"]["vehicle_id"]) == ["VEH-01", "VEH-02"]
    assert list(log["vehicles"]["driver_id"]) == ["DRV-02", "DRV-08"]
    reports = log["reports"]
    assert len(reports) == conn.execute("SELECT COUNT(*) FROM position_reports WHERE convoy_id = 'CNV-002'").fetchone()[0] > 0
    assert list(reports["reported_at"]) == sorted(reports["reported_at"])
    assert set(reports["vehicle_id"]) == {"VEH-01", "VEH-02"}
    with pytest.raises(ValueError):
        fl.trip_log(conn, "CNV-999")


def test_trip_log_picks_up_new_reports_in_order(conn: sqlite3.Connection) -> None:
    before = len(fl.trip_log(conn, "CNV-002")["reports"])
    fleet.record_position(conn, "VEH-01", 50, 50, source="api", convoy_id="CNV-002")
    reports = fl.trip_log(conn, "CNV-002")["reports"]
    assert len(reports) == before + 1
    assert list(reports["reported_at"]) == sorted(reports["reported_at"])
    assert reports.iloc[-1]["x_km"] == 50


# -- audited updates --------------------------------------------------------
def test_report_defect_grounds_vehicle_and_is_audited(conn: sqlite3.Connection) -> None:
    with pytest.raises(ConfirmationRequired):
        fl.report_defect(conn, "VEH-01", "grounding", "Steering play", None)
    assert fleet.fleet_readiness(conn, TODAY)["VEH-01"].status == "ready"
    defect_id = fl.report_defect(conn, "VEH-01", "grounding", "Steering play", STAFF)
    assert defect_id == "DEF-005"
    ready = fleet.fleet_readiness(conn, TODAY)["VEH-01"]
    assert ready.status == "not_ready" and any(defect_id in r for r in ready.reasons)
    assert len(_audit_rows(conn, "report_defect")) == 1
    with pytest.raises(ValueError):
        fl.report_defect(conn, "VEH-01", "huge", "x", STAFF)
    with pytest.raises(ValueError):
        fl.report_defect(conn, "VEH-99", "minor", "x", STAFF)


def test_resolve_defect_makes_veh_06_ready(conn: sqlite3.Connection) -> None:
    with pytest.raises(ConfirmationRequired):
        fl.resolve_defect(conn, "DEF-001", "")
    with pytest.raises(ConfirmationRequired):
        fl.resolve_defect(conn, "DEF-001", "FW-02")  # field worker may not confirm
    assert conn.execute("SELECT resolved_at FROM vehicle_defects WHERE defect_id = 'DEF-001'").fetchone()[0] is None
    fl.resolve_defect(conn, "DEF-001", STAFF, reason="Brakes repaired")
    assert fleet.fleet_readiness(conn, TODAY)["VEH-06"].status == "ready"
    rows = _audit_rows(conn, "resolve_defect")
    assert len(rows) == 1 and rows[0]["entity_id"] == "DEF-001" and rows[0]["reason"] == "Brakes repaired"
    with pytest.raises(ValueError):
        fl.resolve_defect(conn, "DEF-001", STAFF)


def test_record_service_clears_overdue(conn: sqlite3.Connection) -> None:
    assert fleet.fleet_readiness(conn, TODAY)["VEH-04"].status == "not_ready"
    with pytest.raises(ConfirmationRequired):
        fl.record_service(conn, "VEH-04", None)
    assert conn.execute("SELECT last_service_date FROM vehicles WHERE vehicle_id = 'VEH-04'").fetchone()[0] != TODAY.isoformat()
    fl.record_service(conn, "VEH-04", STAFF, odometer_km=97700)
    v = db.row_dict(conn, "vehicles", "vehicle_id", "VEH-04")
    assert (v["last_service_date"], v["last_service_odometer"], v["odometer_km"]) == (TODAY.isoformat(), 97700, 97700)
    assert fleet.fleet_readiness(conn, TODAY)["VEH-04"].status == "ready"
    assert len(_audit_rows(conn, "record_service")) == 1
    with pytest.raises(ValueError):
        fl.record_service(conn, "VEH-04", STAFF, odometer_km=100)


def test_refused_update_changes_nothing(conn: sqlite3.Connection) -> None:
    before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    with pytest.raises(ConfirmationRequired):
        fl.record_service(conn, "VEH-01", "NOBODY")
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before


# -- the app ---------------------------------------------------------------
def test_app_starts(app_test) -> None:  # noqa: ANN001
    at = app_test(14).run()
    assert not at.exception


def test_app_resolve_defect_through_confirmation(app_test, db_path) -> None:  # noqa: ANN001
    def resolved() -> str | None:
        c = db.connect(db_path)
        try:
            return c.execute("SELECT resolved_at FROM vehicle_defects WHERE defect_id = 'DEF-001'").fetchone()[0]
        finally:
            c.close()

    at = app_test(14).run()
    confirm = [b for b in at.button if b.label == "Confirm"]
    assert len(confirm) == 4  # report defect, resolve defect, record service, stale limit
    # Without the tick box the app refuses and nothing changes.
    confirm[1].click()
    at.run()
    assert not at.exception and resolved() is None
    # Tick the box, then confirm: DEF-001 is resolved and the change is audited.
    at.checkbox(key="confirm_tick_resolve_defect").check()
    [b for b in at.button if b.label == "Confirm"][1].click()
    at.run()
    assert not at.exception
    assert resolved() is not None
    c = db.connect(db_path)
    try:
        assert c.execute("SELECT COUNT(*) FROM audit_log WHERE channel = 'app:14' AND action = 'resolve_defect'").fetchone()[0] == 1
    finally:
        c.close()
