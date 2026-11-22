"""Project 20: alert rules, simulator, scheduler, health report, auth and the API."""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shared import alerts, auth, clock, confirm, db, fleet, scheduler, simulator, stock
from shared.api import app

ANCHOR = datetime(2026, 11, 16, 10, 0, 0)


def rule(conn: sqlite3.Connection, rule_id: str) -> dict:
    return alerts.get_rule(conn, rule_id)


def refs(findings: list[alerts.Finding]) -> set[str]:
    return {f.entity_ref for f in findings}


def set_on_hand(conn: sqlite3.Connection, warehouse_id: str, item_id: str, target: int) -> None:
    """Bring on-hand stock to exactly target with one ledger movement."""
    current = stock.on_hand_qty(conn, warehouse_id, item_id)
    if target > current:
        stock.record_receipt(conn, warehouse_id, item_id, target - current, "WHO-01")
    elif target < current:
        conn.execute(
            """INSERT INTO stock_movements(warehouse_id, item_id, moved_at, movement_type, reason, quantity, recorded_by)
               VALUES (?,?,?,'dispatched','write_off',?, 'WHO-01')""",
            (warehouse_id, item_id, clock.iso(), current - target),
        )
    assert stock.on_hand_qty(conn, warehouse_id, item_id) == target


# -- stock_below: on_hand < threshold ------------------------------------------------
@pytest.mark.parametrize(("on_hand", "fires"), [(49, True), (50, False), (51, False)])
def test_stock_rule_pure_at_above_below(on_hand: int, fires: bool) -> None:
    rows = [{"warehouse_id": "WH-01", "item_id": "ITM-HYG", "on_hand": on_hand}]
    assert bool(alerts.stock_below_findings(rows, 50)) is fires


@pytest.mark.parametrize(("on_hand", "fires"), [(49, True), (50, False), (51, False)])
def test_stock_rule_in_database(conn: sqlite3.Connection, on_hand: int, fires: bool) -> None:
    set_on_hand(conn, "WH-01", "ITM-FP-STD", on_hand)
    found = alerts.findings_for_rule(conn, rule(conn, "RULE-STOCK"), clock.now())
    assert ("WH-01/ITM-FP-STD" in refs(found)) is fires
    result = alerts.evaluate_rules(conn)
    opened = conn.execute("SELECT COUNT(*) FROM alerts WHERE entity_ref = 'WH-01/ITM-FP-STD' AND status = 'open'").fetchone()[0]
    assert opened == (1 if fires else 0)
    assert result.rules_evaluated == 4


# -- overdue_callbacks: overdue_count >= threshold ------------------------------------
@pytest.mark.parametrize(("count", "fires"), [(4, False), (5, True), (6, True)])
def test_callback_rule_pure_at_above_below(count: int, fires: bool) -> None:
    assert bool(alerts.overdue_callback_findings(count, 5)) is fires


@pytest.mark.parametrize(("offset", "fires"), [(-1, True), (0, True), (1, False)])
def test_callback_rule_threshold_minus_at_plus(conn: sqlite3.Connection, offset: int, fires: bool) -> None:
    n = len(alerts.overdue_callbacks(conn))
    assert n > 1
    alerts.update_rule(conn, "RULE-CALLBACK", "CRD-01", threshold=n + offset)
    found = alerts.findings_for_rule(conn, rule(conn, "RULE-CALLBACK"), clock.now())
    assert bool(found) is fires
    if fires:
        assert found[0].observed_value == n


def test_overdue_callback_definition(conn: sqlite3.Connection) -> None:
    before = {r["request_id"] for r in alerts.overdue_callbacks(conn)}
    rid = sorted(before)[0]
    # Done callbacks, closed requests and future due times are not overdue.
    conn.execute("UPDATE service_requests SET callback_done_at = ? WHERE request_id = ?", (clock.iso(), rid))
    assert rid not in {r["request_id"] for r in alerts.overdue_callbacks(conn)}
    rid2 = sorted(before)[1]
    conn.execute("UPDATE service_requests SET status = 'closed', closed_at = ? WHERE request_id = ?", (clock.iso(), rid2))
    rid3 = sorted(before)[2]
    conn.execute(
        "UPDATE service_requests SET callback_due_at = ? WHERE request_id = ?", (clock.iso(clock.now() + timedelta(hours=1)), rid3)
    )
    # Exactly at the due time is not yet overdue (due_at < now).
    rid4 = sorted(before)[3]
    conn.execute("UPDATE service_requests SET callback_due_at = ? WHERE request_id = ?", (clock.iso(), rid4))
    after = {r["request_id"] for r in alerts.overdue_callbacks(conn)}
    assert after == before - {rid, rid2, rid3, rid4}


# -- stale_position: minutes since last report > threshold ---------------------------
@pytest.mark.parametrize(("minutes", "stale"), [(14, False), (15, False), (16, True)])
def test_fleet_is_stale_at_the_limit(minutes: int, stale: bool) -> None:
    now = ANCHOR
    assert fleet.is_stale(now - timedelta(minutes=minutes), now=now, limit_minutes=15) is stale


@pytest.mark.parametrize(("minutes", "fires"), [(14, False), (15, False), (16, True)])
def test_stale_rule_at_above_below(conn: sqlite3.Connection, minutes: int, fires: bool) -> None:
    # VEH-07 is not simulated; give it one fresh report `minutes` old.
    fleet.record_position(conn, "VEH-07", 40.0, 50.0, source="api", at=clock.iso(clock.now() - timedelta(minutes=minutes)))
    found = alerts.findings_for_rule(conn, rule(conn, "RULE-STALE"), clock.now())
    assert ("VEH-07" in refs(found)) is fires
    result = alerts.evaluate_rules(conn)
    veh07 = conn.execute("SELECT status FROM alerts WHERE rule_id = 'RULE-STALE' AND entity_ref = 'VEH-07' ORDER BY alert_id").fetchall()
    assert len(veh07) == 1  # the seeded alert: never duplicated
    assert veh07[0][0] == ("open" if fires else "resolved")
    assert (veh07 and not fires) == bool(result.resolved)


def test_stale_rule_vehicle_without_reports(conn: sqlite3.Connection) -> None:
    found = alerts.stale_position_findings({"VEH-99": None}, 15, ANCHOR)
    assert refs(found) == {"VEH-99"}


# -- pipeline_failed: failed runs in window >= threshold ------------------------------
def add_failed_run(conn: sqlite3.Connection, hours_ago: float) -> None:
    start = clock.now() - timedelta(hours=hours_ago)
    conn.execute(
        "INSERT INTO job_runs(job_name, started_at, finished_at, status, trigger, detail) VALUES ('pipeline_ingest',?,?,'failed','scheduled','test')",
        (clock.iso(start), clock.iso(start + timedelta(seconds=5))),
    )


@pytest.mark.parametrize(("failed", "fires"), [(1, False), (2, True), (3, True)])
def test_pipeline_rule_at_above_below(conn: sqlite3.Connection, failed: int, fires: bool) -> None:
    window = 24
    base = alerts.failed_pipeline_runs(conn, clock.now(), window)
    assert base == 0
    alerts.update_rule(conn, "RULE-PIPELINE", "ADM-01", threshold=2)
    add_failed_run(conn, 30)  # outside the 24 h window: never counted
    for k in range(failed):
        add_failed_run(conn, 1 + k)
    assert alerts.failed_pipeline_runs(conn, clock.now(), window) == failed
    found = alerts.findings_for_rule(conn, rule(conn, "RULE-PIPELINE"), clock.now())
    assert bool(found) is fires


@pytest.mark.parametrize(("count", "fires"), [(0, False), (1, True), (2, True)])
def test_pipeline_rule_pure(count: int, fires: bool) -> None:
    assert bool(alerts.pipeline_failed_findings(count, 1, 24)) is fires


# -- evaluation: no duplicates, auto-resolve, disabled rules, audit ---------------------
def test_no_duplicates_and_auto_resolve(conn: sqlite3.Connection) -> None:
    first = alerts.evaluate_rules(conn)
    total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    second = alerts.evaluate_rules(conn)
    assert second.fired == []
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == total
    assert second.still_active == first.still_active + len(first.fired)
    # The seeded WH-03/ITM-HYG alert is still the only one for that pair.
    assert conn.execute("SELECT COUNT(*) FROM alerts WHERE entity_ref = 'WH-03/ITM-HYG'").fetchone()[0] == 1
    # Clear the condition: the alert resolves with resolved_at.
    set_on_hand(conn, "WH-03", "ITM-HYG", 80)
    third = alerts.evaluate_rules(conn)
    row = conn.execute("SELECT alert_id, status, resolved_at FROM alerts WHERE entity_ref = 'WH-03/ITM-HYG'").fetchone()
    assert row["status"] == "resolved" and row["resolved_at"] == clock.iso()
    assert row["alert_id"] in third.resolved
    # Falling below again opens a new alert (the old one is resolved).
    set_on_hand(conn, "WH-03", "ITM-HYG", 10)
    fourth = alerts.evaluate_rules(conn)
    assert len(fourth.fired) == 1
    assert conn.execute("SELECT COUNT(*) FROM alerts WHERE entity_ref = 'WH-03/ITM-HYG'").fetchone()[0] == 2


def test_acknowledged_alert_is_not_duplicated(conn: sqlite3.Connection) -> None:
    alert_id = conn.execute("SELECT alert_id FROM alerts WHERE entity_ref = 'WH-03/ITM-HYG'").fetchone()[0]
    alerts.acknowledge(conn, alert_id, "WHO-01")
    alerts.evaluate_rules(conn)
    rows = conn.execute("SELECT status FROM alerts WHERE entity_ref = 'WH-03/ITM-HYG'").fetchall()
    assert [r[0] for r in rows] == ["acknowledged"]


def test_disabled_rule_is_skipped(conn: sqlite3.Connection) -> None:
    alerts.update_rule(conn, "RULE-STOCK", "CRD-01", enabled=False)
    set_on_hand(conn, "WH-03", "ITM-HYG", 80)  # would resolve if evaluated
    set_on_hand(conn, "WH-01", "ITM-FP-STD", 1)  # would fire if evaluated
    result = alerts.evaluate_rules(conn)
    assert result.rules_skipped == 1 and result.rules_evaluated == 3
    rows = conn.execute("SELECT entity_ref, status FROM alerts WHERE rule_id = 'RULE-STOCK'").fetchall()
    assert [(r[0], r[1]) for r in rows] == [("WH-03/ITM-HYG", "open")]


def test_every_alert_write_is_audited(conn: sqlite3.Connection) -> None:
    result = alerts.evaluate_rules(conn)
    assert result.fired
    fired_audits = conn.execute(
        "SELECT entity_id, actor_id, channel FROM audit_log WHERE entity_table = 'alerts' AND action = 'fire'"
    ).fetchall()
    assert {int(r[0]) for r in fired_audits} == set(result.fired)
    assert {(r[1], r[2]) for r in fired_audits} == {("system", "scheduler")}
    set_on_hand(conn, "WH-03", "ITM-HYG", 80)
    resolved = alerts.evaluate_rules(conn, channel="app:20").resolved
    rows = conn.execute("SELECT entity_id, channel FROM audit_log WHERE entity_table = 'alerts' AND action = 'resolve'").fetchall()
    assert {int(r[0]) for r in rows} == set(resolved) and {r[1] for r in rows} == {"app:20"}


# -- acknowledgement and rule changes -------------------------------------------------------
@pytest.mark.parametrize("who", [None, "", "NOPE-99", "FW-01", "OPR-01"])
def test_acknowledge_needs_a_valid_staff_member(conn: sqlite3.Connection, who: str | None) -> None:
    alert_id = conn.execute("SELECT alert_id FROM alerts WHERE status = 'open' ORDER BY alert_id").fetchone()[0]
    with pytest.raises(confirm.ConfirmationRequired):
        alerts.acknowledge(conn, alert_id, who)
    assert alerts.get_alert(conn, alert_id)["status"] == "open"


def test_acknowledge_records_staff_and_audit(conn: sqlite3.Connection) -> None:
    alert_id = conn.execute("SELECT alert_id FROM alerts WHERE status = 'open' ORDER BY alert_id").fetchone()[0]
    out = alerts.acknowledge(conn, alert_id, "CRD-01", channel="app:20")
    assert out["status"] == "acknowledged" and out["acknowledged_by"] == "CRD-01" and out["acknowledged_at"] == clock.iso()
    audit = conn.execute(
        "SELECT * FROM audit_log WHERE entity_table = 'alerts' AND action = 'acknowledge' ORDER BY audit_id DESC"
    ).fetchone()
    assert audit["entity_id"] == str(alert_id) and audit["actor_role"] == "coordinator" and audit["channel"] == "app:20"
    with pytest.raises(ValueError):
        alerts.acknowledge(conn, alert_id, "CRD-01")
    with pytest.raises(alerts.AlertNotFound):
        alerts.acknowledge(conn, 99999, "CRD-01")


def test_update_rule_is_audited(conn: sqlite3.Connection) -> None:
    out = alerts.update_rule(conn, "RULE-STOCK", "CRD-01", threshold=40, reason="test")
    assert out["threshold"] == 40
    row = conn.execute("SELECT * FROM audit_log WHERE entity_table = 'alert_rules' ORDER BY audit_id DESC").fetchone()
    assert row["entity_id"] == "RULE-STOCK" and '"threshold": 50' in row["before_json"] and '"threshold": 40' in row["after_json"]
    with pytest.raises(alerts.RuleNotFound):
        alerts.update_rule(conn, "RULE-NOPE", "CRD-01", threshold=1)


def test_set_stale_limit_moves_setting_and_rule(conn: sqlite3.Connection) -> None:
    alerts.set_stale_limit(conn, 5, "CRD-01", channel="api")
    assert fleet.stale_limit_minutes(conn) == 5
    assert rule(conn, "RULE-STALE")["threshold"] == 5
    row = conn.execute("SELECT channel, actor_id FROM audit_log WHERE entity_table = 'settings' ORDER BY audit_id DESC").fetchone()
    assert tuple(row) == ("api", "CRD-01")
    with pytest.raises(ValueError):
        alerts.set_stale_limit(conn, 0, "CRD-01")


# -- simulator ----------------------------------------------------------------------------------
def test_simulator_step_writes_only_when_enabled_and_never_moves_veh07(conn: sqlite3.Connection) -> None:
    simulated = [r[0] for r in conn.execute("SELECT vehicle_id FROM vehicles WHERE simulated = 1")]
    assert "VEH-07" not in simulated
    veh07_before = conn.execute("SELECT COUNT(*) FROM position_reports WHERE vehicle_id = 'VEH-07'").fetchone()[0]
    assert simulator.is_enabled(conn)
    clock.set_now(ANCHOR + timedelta(minutes=1))
    n = simulator.step(conn)
    assert n == len(simulated)
    rows = conn.execute("SELECT vehicle_id, x_km, y_km FROM position_reports WHERE source = 'simulator'").fetchall()
    assert sorted(r[0] for r in rows) == sorted(simulated)
    assert all(0 <= r[1] <= 100 and 0 <= r[2] <= 100 for r in rows)
    assert conn.execute("SELECT COUNT(*) FROM position_reports WHERE vehicle_id = 'VEH-07'").fetchone()[0] == veh07_before

    simulator.set_enabled(conn, False, "CRD-01")
    assert not simulator.is_enabled(conn)
    assert db.get_setting("position_simulator_enabled", conn=conn) == "0"
    audit = conn.execute("SELECT actor_id, entity_id FROM audit_log WHERE entity_table = 'settings' ORDER BY audit_id DESC").fetchone()
    assert tuple(audit) == ("CRD-01", "position_simulator_enabled")
    clock.set_now(ANCHOR + timedelta(minutes=2))
    assert simulator.step(conn) == 0
    assert conn.execute("SELECT COUNT(*) FROM position_reports WHERE source = 'simulator'").fetchone()[0] == len(simulated)


def test_simulated_vehicles_stay_fresh_and_veh07_goes_stale(conn: sqlite3.Connection) -> None:
    for k in range(1, 4):
        clock.set_now(ANCHOR + timedelta(minutes=10 * k))
        simulator.step(conn)
    positions = {p["vehicle_id"]: p for p in simulator.vehicle_positions(conn)}
    assert positions["VEH-07"]["stale"] is True
    assert all(not p["stale"] for v, p in positions.items() if v != "VEH-07")
    assert all(p["source"] == "simulator" for v, p in positions.items() if v != "VEH-07")


def test_next_position_moves_toward_target_and_drifts_near_home() -> None:
    import random

    rng = random.Random(1)
    x, y, speed = simulator.next_position((10.0, 10.0), (10.0, 10.0), (20.0, 10.0), 1.0, rng)
    assert (round(x, 6), round(y, 6), speed) == (11.0, 10.0, simulator.SIM_SPEED_KMH)
    x, y, speed = simulator.next_position((19.5, 10.0), (10.0, 10.0), (20.0, 10.0), 1.0, rng)
    assert (x, y, speed) == (20.0, 10.0, 0.0)
    x, y = 50.2, 50.1
    for _ in range(50):
        x, y, _s = simulator.next_position((x, y), (50.0, 50.0), None, 0.7, rng)
        assert ((x - 50) ** 2 + (y - 50) ** 2) ** 0.5 <= simulator.DRIFT_RADIUS_KM
    x, y, _s = simulator.next_position((99.95, 0.05), (99.9, 0.1), None, 0.7, rng)
    assert 0 <= x <= 100 and 0 <= y <= 100


# -- scheduler ----------------------------------------------------------------------------------
def test_run_job_success_is_recorded(db_path: Path) -> None:
    run = scheduler.run_job("alert_evaluation", "manual", requested_by="ADM-01", channel="app:20")
    assert run["status"] == "success" and "rules evaluated" in run["detail"]
    with db.transaction(db_path) as conn:
        row = conn.execute("SELECT * FROM job_runs WHERE job_run_id = ?", (run["job_run_id"],)).fetchone()
        assert row["status"] == "success" and row["finished_at"] and row["trigger"] == "manual"
        audit = conn.execute("SELECT * FROM audit_log WHERE entity_table = 'job_runs' ORDER BY audit_id DESC").fetchone()
        assert audit["actor_id"] == "ADM-01" and audit["channel"] == "app:20"


def test_run_job_pipeline_not_installed(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "shared.pipeline", None)  # import fails even if the module exists
    run = scheduler.run_job("pipeline_ingest")
    assert run["status"] == "failed" and run["detail"] == "pipeline not installed"
    with db.transaction(db_path) as conn:
        row = conn.execute("SELECT status, detail, finished_at FROM job_runs WHERE job_run_id = ?", (run["job_run_id"],)).fetchone()
        assert tuple(row)[:2] == ("failed", "pipeline not installed") and row[2]


def test_run_job_forced_failure_never_raises(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(conn: sqlite3.Connection, trigger: str, channel: str) -> tuple[bool, str]:
        conn.execute("UPDATE settings SET value = 'x' WHERE key = 'board_refresh_seconds'")
        raise RuntimeError("disk on fire")

    monkeypatch.setitem(scheduler.JOBS, "db_backup", broken)
    run = scheduler.run_job("db_backup", "scheduled")
    assert run["status"] == "failed" and "disk on fire" in run["detail"]
    with db.transaction(db_path) as conn:
        assert db.get_setting("board_refresh_seconds", conn=conn) != "x"  # the failed job's writes were rolled back
        assert conn.execute("SELECT status FROM job_runs WHERE job_run_id = ?", (run["job_run_id"],)).fetchone()[0] == "failed"
    assert scheduler.run_job("no_such_job")["status"] == "failed"


def test_db_backup_keeps_the_last_copies(db_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "backups"
    monkeypatch.setenv(scheduler.BACKUP_ENV_VAR, str(target))
    for k in range(scheduler.BACKUPS_TO_KEEP + 2):
        clock.set_now(ANCHOR + timedelta(minutes=k))
        assert scheduler.run_job("db_backup")["status"] == "success"
    files = sorted(target.glob("*.db"))
    assert len(files) == scheduler.BACKUPS_TO_KEEP
    with sqlite3.connect(files[-1]) as copy:
        assert copy.execute("SELECT COUNT(*) FROM alert_rules").fetchone()[0] == 4


def test_position_simulator_job(db_path: Path) -> None:
    clock.set_now(ANCHOR + timedelta(minutes=1))
    run = scheduler.run_job("position_simulator")
    assert run["status"] == "success" and run["detail"] == "7 vehicles reported"
    with db.transaction(db_path) as conn:
        simulator.set_enabled(conn, False, "CRD-01")
    run = scheduler.run_job("position_simulator")
    assert run["status"] == "success" and "switched off" in run["detail"]
    with db.transaction(db_path) as conn:
        hb = conn.execute("SELECT status, detail FROM service_heartbeats WHERE service_name = 'position_simulator'").fetchone()
        assert tuple(hb) == ("degraded", "stopped")


def test_stopped_simulator_vehicles_go_stale_after_the_limit(db_path: Path) -> None:
    """Project 17's walkthrough: stop the simulator, positions stop, vehicles go stale after the limit."""
    clock.set_now(ANCHOR + timedelta(minutes=1))
    assert scheduler.run_job("position_simulator")["detail"] == "7 vehicles reported"
    with db.transaction(db_path) as conn:
        simulator.set_enabled(conn, False, "CRD-01")
        before = conn.execute("SELECT COUNT(*) FROM position_reports").fetchone()[0]
        limit = fleet.stale_limit_minutes(conn)
    for k in (2, 8, 16):
        clock.set_now(ANCHOR + timedelta(minutes=k))
        scheduler.run_job("position_simulator", "scheduled")
    with db.transaction(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM position_reports").fetchone()[0] == before
        at_limit = {p["vehicle_id"]: p["stale"] for p in simulator.vehicle_positions(conn, now=ANCHOR + timedelta(minutes=1 + limit))}
        past = {p["vehicle_id"]: p["stale"] for p in simulator.vehicle_positions(conn, now=ANCHOR + timedelta(minutes=2 + limit))}
    assert at_limit["VEH-03"] is False and past["VEH-03"] is True


def test_heartbeat_probes_homepage_and_api(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scheduler, "probe", lambda url, timeout=2.0: "8600" in url)
    scheduler.heartbeat(db_path)
    with db.transaction(db_path) as conn:
        beats = {r[0]: r[1] for r in conn.execute("SELECT service_name, status FROM service_heartbeats")}
    assert beats["api"] == "ok" and beats["homepage"] == "down" and beats["scheduler"] == "ok"
    assert beats["position_simulator"] == "ok"


def test_health_report_lists_every_job_and_service(conn: sqlite3.Connection) -> None:
    report = scheduler.health_report(conn)
    job_names = {r[0] for r in conn.execute("SELECT job_name FROM scheduled_jobs")}
    services = {r[0] for r in conn.execute("SELECT service_name FROM service_heartbeats")}
    assert (
        {j["job_name"] for j in report["jobs"]} == job_names == {"pipeline_ingest", "alert_evaluation", "position_simulator", "db_backup"}
    )
    assert {s["service_name"] for s in report["services"]} == services
    jobs = {j["job_name"]: j for j in report["jobs"]}
    assert jobs["alert_evaluation"]["last_success_at"] is not None
    assert jobs["alert_evaluation"]["overdue"] is False
    # Two hours later with no runs, the 5-minute alert job is overdue.
    later = scheduler.health_report(conn, now=ANCHOR + timedelta(hours=2))
    assert {j["job_name"]: j for j in later["jobs"]}["alert_evaluation"]["overdue"] is True


def test_build_scheduler_registers_one_job_per_enabled_row(conn: sqlite3.Connection, db_path: Path) -> None:
    sched = scheduler.build_scheduler(db_path)
    ids = {j.id for j in sched.get_jobs()} - {"_heartbeat"}
    assert ids == {"pipeline_ingest", "alert_evaluation", "position_simulator", "db_backup"}
    intervals = {j.id: int(j.trigger.interval.total_seconds()) for j in sched.get_jobs() if j.id != "_heartbeat"}
    assert intervals == {r[0]: r[1] for r in conn.execute("SELECT job_name, interval_seconds FROM scheduled_jobs")}
    conn.execute("UPDATE scheduled_jobs SET enabled = 0 WHERE job_name = 'db_backup'")
    conn.commit()
    assert "db_backup" not in {j.id for j in scheduler.build_scheduler(db_path).get_jobs()}
    assert not sched.running


# -- auth -----------------------------------------------------------------------------------------
def test_auth_refuses_missing_and_placeholder_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (auth.USERNAME_VAR, auth.PASSWORD_VAR, auth.TOKEN_VAR):
        monkeypatch.delenv(var, raising=False)
    assert not auth.check_staff_login("", "") and not auth.check_api_token("")
    monkeypatch.setenv(auth.USERNAME_VAR, "change-me")
    monkeypatch.setenv(auth.PASSWORD_VAR, "change-me")
    monkeypatch.setenv(auth.TOKEN_VAR, "change-me")
    assert not auth.check_staff_login("change-me", "change-me")
    assert not auth.check_api_token("change-me")
    monkeypatch.setenv(auth.USERNAME_VAR, "duty-desk")
    monkeypatch.setenv(auth.PASSWORD_VAR, "test-pass-123")
    monkeypatch.setenv(auth.TOKEN_VAR, "test-token-123")
    assert auth.check_staff_login("duty-desk", "test-pass-123")
    assert not auth.check_staff_login("duty-desk", "wrong")
    assert auth.check_api_token("test-token-123") and not auth.check_api_token("test-token-12")
    assert auth.bearer_token("Bearer test-token-123") == "test-token-123"
    assert auth.bearer_token("Basic abc") is None
    monkeypatch.setenv("PORTFOLIO_MODE", "server")
    assert auth.server_mode()
    monkeypatch.setenv("PORTFOLIO_MODE", "local")
    assert not auth.server_mode()


# -- API ------------------------------------------------------------------------------------------
@pytest.fixture
def client(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("PORTFOLIO_MODE", "local")
    return TestClient(app)


def test_api_docs_pages_load(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    paths = spec.json()["paths"]
    for path in ("/health", "/alerts", "/alert-rules/{rule_id}", "/positions", "/simulator", "/audit", "/stock", "/jobs/{job_name}/run"):
        assert path in paths
    ref = client.get("/reference")
    assert ref.status_code == 200 and "text/html" in ref.headers["content-type"]
    for path in paths:
        assert path in ref.text
    assert "coordinator, admin" in ref.text
    assert "https://" not in ref.text and "<script" not in ref.text  # no external assets


def test_api_change_appears_in_audit_log(client: TestClient, db_path: Path) -> None:
    r = client.patch("/alert-rules/RULE-STOCK", json={"threshold": 45, "reason": "demo"}, headers={"X-Staff-Id": "CRD-01"})
    assert r.status_code == 200 and r.json()["threshold"] == 45
    entries = client.get("/audit", params={"channel": "api", "entity_table": "alert_rules"}).json()
    assert entries and entries[0]["entity_id"] == "RULE-STOCK" and entries[0]["actor_id"] == "CRD-01"
    assert entries[0]["actor_role"] == "coordinator" and '"threshold": 45' in entries[0]["after_json"]


def test_api_position_report_through_record_position(client: TestClient, db_path: Path) -> None:
    r = client.post("/positions", json={"vehicle_id": "VEH-07", "x_km": 41.5, "y_km": 52.0}, headers={"X-Staff-Id": "FW-01"})
    assert r.status_code == 201 and r.json()["source"] == "api"
    report_id = r.json()["report_id"]
    with db.transaction(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM audit_log WHERE entity_table = 'position_reports' AND entity_id = ?", (str(report_id),)
        ).fetchone()
        assert row["channel"] == "api" and row["actor_role"] == "field_worker"
    positions = {p["vehicle_id"]: p for p in client.get("/vehicles/positions").json()}
    assert positions["VEH-07"]["stale"] is False and positions["VEH-07"]["source"] == "api"
    assert (
        client.post("/positions", json={"vehicle_id": "VEH-07", "x_km": 140, "y_km": 5}, headers={"X-Staff-Id": "FW-01"}).status_code == 422
    )
    assert (
        client.post("/positions", json={"vehicle_id": "VEH-99", "x_km": 1, "y_km": 5}, headers={"X-Staff-Id": "FW-01"}).status_code == 400
    )


def test_api_roles_401_and_403(client: TestClient) -> None:
    body = {"threshold": 10}
    assert client.patch("/alert-rules/RULE-STOCK", json=body).status_code == 401
    assert client.patch("/alert-rules/RULE-STOCK", json=body, headers={"X-Staff-Id": "NOPE-01"}).status_code == 401
    assert client.patch("/alert-rules/RULE-STOCK", json=body, headers={"X-Staff-Id": "FW-01"}).status_code == 403
    assert (
        client.post("/positions", json={"vehicle_id": "VEH-03", "x_km": 1, "y_km": 1}, headers={"X-Staff-Id": "OPR-01"}).status_code == 403
    )
    assert client.put("/simulator", json={"enabled": False}, headers={"X-Staff-Id": "DO-01"}).status_code == 403
    assert client.post("/jobs/db_backup/run", headers={"X-Staff-Id": "WHO-01"}).status_code == 403
    assert client.patch("/alert-rules/RULE-STOCK", json=body, headers={"X-Staff-Id": "ADM-01"}).status_code == 200
    assert client.get("/alerts").status_code == 200  # reads are open


def test_api_server_mode_requires_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORTFOLIO_MODE", "server")
    monkeypatch.setenv(auth.TOKEN_VAR, "test-token-123")
    staff = {"X-Staff-Id": "CRD-01"}
    assert client.post("/alerts/evaluate", headers=staff).status_code == 401
    assert client.post("/alerts/evaluate", headers={**staff, "Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/alerts/evaluate", headers={**staff, "Authorization": "Bearer test-token-123"}).status_code == 200
    monkeypatch.setenv(auth.TOKEN_VAR, "change-me")
    assert client.post("/alerts/evaluate", headers={**staff, "Authorization": "Bearer change-me"}).status_code == 401
    assert client.get("/health").status_code == 200


def test_api_server_mode_accepts_the_staff_login(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import base64

    def basic(user: str, password: str) -> str:
        return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    monkeypatch.setenv("PORTFOLIO_MODE", "server")
    monkeypatch.delenv(auth.TOKEN_VAR, raising=False)
    monkeypatch.setenv(auth.USERNAME_VAR, "duty-desk")
    monkeypatch.setenv(auth.PASSWORD_VAR, "test-pass-123")
    staff = {"X-Staff-Id": "CRD-01"}
    assert client.post("/alerts/evaluate", headers={**staff, "Authorization": basic("duty-desk", "wrong")}).status_code == 401
    assert client.post("/alerts/evaluate", headers={**staff, "Authorization": "Basic not-base64!"}).status_code == 401
    assert client.post("/alerts/evaluate", headers={**staff, "Authorization": basic("duty-desk", "test-pass-123")}).status_code == 200
    monkeypatch.setenv("PORTFOLIO_MODE", "local")  # local demo mode: the role header alone
    assert client.post("/alerts/evaluate", headers=staff).status_code == 200


def test_api_alert_threshold_crossed_fires(client: TestClient, db_path: Path) -> None:
    with db.transaction(db_path) as conn:
        set_on_hand(conn, "WH-01", "ITM-FP-STD", 50)
    staff = {"X-Staff-Id": "CRD-01"}
    client.post("/alerts/evaluate", headers=staff)
    refs_open = {a["entity_ref"] for a in client.get("/alerts", params={"status": "open"}).json()}
    assert "WH-01/ITM-FP-STD" not in refs_open
    with db.transaction(db_path) as conn:
        set_on_hand(conn, "WH-01", "ITM-FP-STD", 49)
    out = client.post("/alerts/evaluate", headers=staff).json()
    assert len(out["fired"]) == 1
    new = client.get("/alerts", params={"status": "open"}).json()[0]
    assert new["entity_ref"] == "WH-01/ITM-FP-STD" and new["observed_value"] == 49
    ack = client.post(f"/alerts/{new['alert_id']}/acknowledge", headers={"X-Staff-Id": "WHO-01"})
    assert ack.status_code == 200 and ack.json()["status"] == "acknowledged"
    assert client.post(f"/alerts/{new['alert_id']}/acknowledge", headers={"X-Staff-Id": "WHO-01"}).status_code == 400
    assert client.post("/alerts/99999/acknowledge", headers={"X-Staff-Id": "WHO-01"}).status_code == 404


def test_api_health_jobs_simulator_and_settings(client: TestClient, db_path: Path) -> None:
    health = client.get("/health").json()
    assert {j["job_name"] for j in health["jobs"]} == {"pipeline_ingest", "alert_evaluation", "position_simulator", "db_backup"}
    assert {s["service_name"] for s in health["services"]} >= {"api", "scheduler", "position_simulator"}
    assert len(client.get("/jobs").json()) == 4
    admin = {"X-Staff-Id": "ADM-01"}
    run = client.post("/jobs/alert_evaluation/run", headers=admin).json()
    assert run["status"] == "success"
    assert client.post("/jobs/nope/run", headers=admin).status_code == 404
    sim = client.put("/simulator", json={"enabled": False}, headers={"X-Staff-Id": "CRD-01"}).json()
    assert sim["enabled"] is False and "VEH-07" not in sim["simulated_vehicle_ids"]
    assert client.get("/simulator").json()["enabled"] is False
    out = client.put("/settings/stale_position_minutes", json={"minutes": 5}, headers={"X-Staff-Id": "CRD-01"}).json()
    assert out["value"] == "5"
    assert {r["rule_id"]: r["threshold"] for r in client.get("/alert-rules").json()}["RULE-STALE"] == 5
    channels = {e["channel"] for e in client.get("/audit", params={"limit": 20}).json()}
    assert "api" in channels
    assert client.get("/stock", params={"below": 50}).status_code == 200
    assert client.get("/requests/overdue-callbacks").status_code == 200
    assert client.get("/advisories/current").status_code == 200


def test_api_sync_endpoints_detect_and_resolve_conflicts(client: TestClient, db_path: Path) -> None:
    from shared import api

    if not api.SYNC_INSTALLED:
        assert client.post("/sync/changes").status_code == 503
        return
    record = client.get("/sync/records/INT-0001").json()
    base = record["version"]
    # An office edit raises the server version.
    r = client.put("/sync/records/INT-0001", json={"changes": {"urgency": "urgent"}}, headers={"X-Staff-Id": "CW-01"})
    assert r.status_code == 200 and r.json()["version"] == base + 1
    # A device edit from the old version is a conflict, never applied silently.
    change = {"device_id": "DEV-01", "record_id": "INT-0001", "operation": "update", "payload": {"household_size": 7}, "base_version": base}
    assert client.post("/sync/changes", json=change, headers={"X-Staff-Id": "OPR-01"}).status_code == 403
    out = client.post("/sync/changes", json=change, headers={"X-Staff-Id": "FW-01"}).json()
    assert out["outcome"] == "conflict" and out["conflict_id"]
    assert client.get("/sync/records/INT-0001").json()["household_size"] == record["household_size"]
    conflict = client.get(f"/sync/conflicts/{out['conflict_id']}").json()
    assert "household_size" in conflict["differing_fields"]
    assert out["conflict_id"] in {c["conflict_id"] for c in client.get("/sync/conflicts").json()}
    # Field workers cannot choose; a caseworker chooses with a reason.
    body = {"choice": "keep_device", "reason": "Device has the newer head count"}
    assert client.post(f"/sync/conflicts/{out['conflict_id']}/resolve", json=body, headers={"X-Staff-Id": "FW-01"}).status_code == 403
    assert (
        client.post(
            f"/sync/conflicts/{out['conflict_id']}/resolve", json={"choice": "keep_device", "reason": ""}, headers={"X-Staff-Id": "CW-01"}
        ).status_code
        == 422
    )
    done = client.post(f"/sync/conflicts/{out['conflict_id']}/resolve", json=body, headers={"X-Staff-Id": "CW-01"})
    assert done.status_code == 200 and done.json()["data"]["household_size"] == 7
    assert client.post(f"/sync/conflicts/{out['conflict_id']}/resolve", json=body, headers={"X-Staff-Id": "CW-01"}).status_code == 400
    with db.transaction(db_path) as conn:
        channels = {
            r[0]
            for r in conn.execute(
                "SELECT channel FROM audit_log WHERE entity_table IN ('sync_conflicts', 'intake_records') AND actor_id IN ('CW-01', 'FW-01')"
            )
        }
    assert channels == {"api"}
    assert client.get("/sync/devices/DEV-01/status").json()["device_id"] == "DEV-01"
    assert client.get("/sync/conflicts/CNF-9999").status_code == 404


def test_run_job_pipeline_installed(db_path: Path) -> None:
    if not scheduler.pipeline_installed():
        pytest.skip("shared.pipeline not installed")
    run = scheduler.run_job("pipeline_ingest", "manual")
    assert run["job_run_id"] is not None
    assert run["status"] in ("success", "failed") and run["detail"]


# -- the Streamlit app ----------------------------------------------------------------------------
def test_app_starts(app_test) -> None:  # noqa: ANN001
    at = app_test(20).run()
    assert not at.exception
    assert "Evaluate alert rules now" in [b.label for b in at.button]


def test_app_evaluate_and_run_now(app_test, db_path: Path) -> None:  # noqa: ANN001
    at = app_test(20).run()
    at = next(b for b in at.button if b.label == "Evaluate alert rules now").click().run()
    assert not at.exception
    assert any("Alert rules evaluated" in s.value for s in at.success)
    at = next(b for b in at.button if b.key == "p20_run_position_simulator").click().run()
    assert not at.exception
    assert any("position_simulator run" in s.value and "success" in s.value for s in at.success)


def test_app_acknowledge_with_confirmation(app_test, db_path: Path) -> None:  # noqa: ANN001
    at = app_test(20).run()
    pick = at.selectbox(key="p20_ack_pick")
    alert_id = pick.value
    at = next(b for b in at.button if b.label == "Acknowledge alert").click().run()  # not ticked: nothing changes
    with db.transaction(db_path) as conn:
        assert alerts.get_alert(conn, alert_id)["status"] == "open"
    at.selectbox(key="confirm_staff_p20_ack").set_value("CRD-01")
    at.checkbox(key="confirm_tick_p20_ack").check()
    at = next(b for b in at.button if b.label == "Acknowledge alert").click().run()
    assert not at.exception
    with db.transaction(db_path) as conn:
        alert = alerts.get_alert(conn, alert_id)
        assert alert["status"] == "acknowledged" and alert["acknowledged_by"] == "CRD-01"
        audit = conn.execute(
            "SELECT channel FROM audit_log WHERE entity_table = 'alerts' AND action = 'acknowledge' ORDER BY audit_id DESC"
        ).fetchone()
        assert audit[0] == "app:20"


def test_every_api_write_endpoint_writes_an_audit_row_on_channel_api(client: TestClient, db_path: Path) -> None:
    def api_rows() -> int:
        with db.transaction(db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM audit_log WHERE channel = 'api'").fetchone()[0]

    open_alert = client.get("/alerts", params={"status": "open"}).json()[0]["alert_id"]
    writes = [
        ("patch", "/alert-rules/RULE-STOCK", {"json": {"threshold": 44}, "headers": {"X-Staff-Id": "CRD-01"}}),
        ("post", f"/alerts/{open_alert}/acknowledge", {"headers": {"X-Staff-Id": "CRD-01"}}),
        ("post", "/jobs/alert_evaluation/run", {"headers": {"X-Staff-Id": "ADM-01"}}),
        ("put", "/simulator", {"json": {"enabled": False}, "headers": {"X-Staff-Id": "CRD-01"}}),
        ("put", "/settings/stale_position_minutes", {"json": {"minutes": 9}, "headers": {"X-Staff-Id": "CRD-01"}}),
        ("post", "/positions", {"json": {"vehicle_id": "VEH-07", "x_km": 5, "y_km": 5}, "headers": {"X-Staff-Id": "FW-01"}}),
    ]
    for method, url, kwargs in writes:
        before = api_rows()
        response = getattr(client, method)(url, **kwargs)
        assert response.status_code in (200, 201), (url, response.text)
        assert api_rows() > before, f"{method.upper()} {url} wrote no audit row on channel api"


def test_app_health_tab_lists_every_service_and_job(app_test) -> None:  # noqa: ANN001
    at = app_test(20).run()
    assert not at.exception
    frames = [d.value for d in at.dataframe]
    services = next(f for f in frames if "service_name" in f.columns)
    jobs = next(f for f in frames if "last_success_at" in f.columns and "job_name" in f.columns)
    assert {"api", "homepage", "position_simulator", "scheduler"} <= set(services["service_name"])
    assert set(jobs["job_name"]) == {"pipeline_ingest", "alert_evaluation", "position_simulator", "db_backup"}
