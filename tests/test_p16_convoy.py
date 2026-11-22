"""Tests for Project 16: convoy dispatch planner (readiness and capacity blocks, advisories,
stop order, vehicle split, stock limit, checklist, approval, dispatch and tracking)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

import p16_convoy as p16
import p16_routing as routing
import pytest

from shared import confirm, db, fleet, stock

# The walkthrough plan on CNV-004 (origin WH-03): stops added in this order, then these loads on VEH-08.
WALK_STOPS = ["SITE-04", "SITE-02", "SITE-03"]
WALK_LOADS = [
    ("SITE-04", "ITM-BLK", 80),
    ("SITE-04", "ITM-HYG", 10),
    ("SITE-03", "ITM-BLK", 60),
    ("SITE-03", "ITM-MAT", 15),
    ("SITE-02", "ITM-BLK", 40),
    ("SITE-02", "ITM-HYG", 20),
    ("SITE-02", "ITM-MAT", 15),
]
WALK_KG = 660.0  # 180 blankets x 2 + 30 kits x 4 + 30 mattresses x 6


def sc() -> dict[str, Any]:
    return json.loads(db.get_setting("demo.scenarios"))["p16"]


def tick_all(conn: sqlite3.Connection, cid: str, staff: str = "CRD-02") -> None:
    for row in p16.checklist(conn, cid):
        if not row["checked"]:
            p16.set_check(conn, cid, row["item_key"], True, staff)


def build_walkthrough(conn: sqlite3.Connection, tick: bool = True) -> str:
    cid = sc()["draft_convoy_id"]
    p16.assign_vehicle(conn, cid, "VEH-08", "DRV-01", "CRD-02")
    for s in WALK_STOPS:
        p16.add_stop(conn, cid, s, "CRD-02")
    for site, item, qty in WALK_LOADS:
        p16.add_load(conn, cid, site, item, qty, "VEH-08", "CRD-02")
    if tick:
        tick_all(conn, cid)
    return cid


def add_grounding_defect(conn: sqlite3.Connection, vehicle_id: str) -> None:
    conn.execute(
        "INSERT INTO vehicle_defects(defect_id, vehicle_id, reported_at, reported_by, description, severity) VALUES (?,?,?,?,?,?)",
        ("DEF-900", vehicle_id, "2026-11-16T09:00:00", "FW-01", "Steering failure reported after assignment.", "grounding"),
    )


def dispatch_count(conn: sqlite3.Connection, cid: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM dispatches WHERE convoy_id = ?", (cid,)).fetchone()[0]


# -- capacity -----------------------------------------------------------------------
def test_exactly_at_capacity_is_not_overloaded_one_kg_over_is() -> None:
    assert routing.is_overloaded(6000, 6000) is False
    assert routing.is_overloaded(6001, 6000) is True
    assert routing.is_overloaded(5999.5, 6000) is False


def test_vehicle_at_capacity_can_be_approved(conn: sqlite3.Connection) -> None:
    cid = build_walkthrough(conn)
    conn.execute("UPDATE vehicles SET load_capacity_kg = ? WHERE vehicle_id = 'VEH-08'", (WALK_KG,))
    veh = p16.assigned_vehicles(conn, cid)[0]
    assert veh["load_kg"] == WALK_KG and veh["overloaded"] is False
    assert p16.blockers(conn, cid) == []
    assert p16.approve(conn, cid, "CRD-02")["status"] == "approved"


def test_one_kg_over_blocks_approval(conn: sqlite3.Connection) -> None:
    cid = build_walkthrough(conn)
    conn.execute("UPDATE vehicles SET load_capacity_kg = ? WHERE vehicle_id = 'VEH-08'", (WALK_KG - 1,))
    assert p16.assigned_vehicles(conn, cid)[0]["overloaded"] is True
    with pytest.raises(p16.ConvoyBlocked) as err:
        p16.approve(conn, cid, "CRD-02")
    assert "VEH-08 is overloaded: 660 kg loaded, capacity 659 kg (1 kg over)" in str(err.value)
    assert p16.get_convoy(conn, cid)["status"] == "draft"


def test_overloaded_vehicle_blocks_departure(conn: sqlite3.Connection) -> None:
    cid = build_walkthrough(conn)
    p16.approve(conn, cid, "CRD-02")
    conn.execute("UPDATE vehicles SET load_capacity_kg = 600 WHERE vehicle_id = 'VEH-08'")  # fleet record corrected after approval
    with pytest.raises(p16.ConvoyBlocked, match="VEH-08 is overloaded"):
        p16.depart(conn, cid, "CRD-02")
    assert dispatch_count(conn, cid) == 0
    assert p16.get_convoy(conn, cid)["status"] == "approved"


def test_overloading_is_flagged_while_planning(conn: sqlite3.Connection) -> None:
    cid = sc()["draft_convoy_id"]
    p16.assign_vehicle(conn, cid, "VEH-02", "DRV-07", "CRD-02")  # van, 1500 kg
    p16.add_stop(conn, cid, "SITE-02", "CRD-02")
    result = p16.add_load(conn, cid, "SITE-02", "ITM-FP-STD", 101, "VEH-02", "CRD-02")  # 1515 kg
    assert result["weight_kg"] == 1515.0 and result["vehicle"]["overloaded"] is True


# -- readiness ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("overdue_vehicle_id", "Service overdue since"),
        ("grounded_vehicle_id", "Grounding defect DEF-001: Brake pedal goes to the floor"),
        ("insurance_expired_vehicle_id", "Insurance expired on"),
    ],
)
def test_assigning_not_ready_vehicle_refused_with_reason(conn: sqlite3.Connection, key: str, expected: str) -> None:
    cid = sc()["draft_convoy_id"]
    vid = sc()[key]
    planned = p16.get_convoy(conn, cid)["planned_date"]
    with pytest.raises(p16.VehicleNotReady) as err:
        p16.assign_vehicle(conn, cid, vid, "DRV-01", "CRD-02")
    assert str(err.value).startswith(f"{vid} is not ready on {planned}: ")
    assert expected in str(err.value)
    assert err.value.reasons == fleet.fleet_readiness(conn, date.fromisoformat(planned))[vid].reasons
    assert conn.execute("SELECT COUNT(*) FROM convoy_vehicles WHERE convoy_id = ?", (cid,)).fetchone()[0] == 0


def test_veh04_reason_has_the_service_due_date(conn: sqlite3.Connection) -> None:
    cid = sc()["draft_convoy_id"]
    v = db.row_dict(conn, "vehicles", "vehicle_id", "VEH-04")
    due = date.fromisoformat(v["last_service_date"]) + timedelta(days=v["service_interval_days"])
    planned = p16.get_convoy(conn, cid)["planned_date"]
    with pytest.raises(p16.VehicleNotReady) as err:
        p16.assign_vehicle(conn, cid, "VEH-04", "DRV-01", "CRD-02")
    assert str(err.value) == f"VEH-04 is not ready on {planned}: Service overdue since {due.isoformat()} (every 180 days)"


def test_due_for_service_allowed_with_warning(conn: sqlite3.Connection) -> None:
    cid = sc()["draft_convoy_id"]
    r = p16.assign_vehicle(conn, cid, "VEH-05", "DRV-07", "CRD-02")
    assert r.status == "due_for_service" and r.warnings and r.warnings[0].startswith("Service due by")


def test_vehicle_not_ready_after_assignment_blocks_approval_and_departure(conn: sqlite3.Connection) -> None:
    cid = build_walkthrough(conn)
    add_grounding_defect(conn, "VEH-08")
    with pytest.raises(p16.ConvoyBlocked) as err:
        p16.approve(conn, cid, "CRD-02")
    assert "VEH-08 is not ready on" in str(err.value) and "Grounding defect DEF-900: Steering failure" in str(err.value)
    conn.execute("UPDATE vehicle_defects SET resolved_at = '2026-11-16T09:30:00' WHERE defect_id = 'DEF-900'")
    p16.approve(conn, cid, "CRD-02")
    conn.execute("UPDATE vehicle_defects SET resolved_at = NULL WHERE defect_id = 'DEF-900'")
    with pytest.raises(p16.ConvoyBlocked) as err:
        p16.depart(conn, cid, "CRD-02")
    assert "Grounding defect DEF-900" in str(err.value)
    assert dispatch_count(conn, cid) == 0


def test_seeded_cnv003_vehicle_states(conn: sqlite3.Connection) -> None:
    cid = sc()["convoy_with_blocked_stop"]
    states = {v["vehicle_id"]: v["readiness"] for v in p16.assigned_vehicles(conn, cid)}
    assert states == {"VEH-03": "ready", "VEH-05": "due_for_service"}


# -- drivers --------------------------------------------------------------------------
def test_expired_licence_refused(conn: sqlite3.Connection) -> None:
    cid = sc()["draft_convoy_id"]
    drv = sc()["expired_licence_driver_id"]
    with pytest.raises(p16.DriverInvalid, match=f"{drv} licence expired on"):
        p16.assign_vehicle(conn, cid, "VEH-02", drv, "CRD-02")


def test_class_b_licence_refused_on_truck_allowed_on_van(conn: sqlite3.Connection) -> None:
    cid = sc()["draft_convoy_id"]
    with pytest.raises(p16.DriverInvalid, match="DRV-07 holds a class B licence; a truck needs class C"):
        p16.assign_vehicle(conn, cid, "VEH-08", "DRV-07", "CRD-02")
    assert p16.assign_vehicle(conn, cid, "VEH-02", "DRV-07", "CRD-02").ready


def test_one_driver_per_vehicle_per_convoy(conn: sqlite3.Connection) -> None:
    cid = sc()["draft_convoy_id"]
    p16.assign_vehicle(conn, cid, "VEH-08", "DRV-01", "CRD-02")
    with pytest.raises(p16.DriverInvalid, match="DRV-01 already drives VEH-08"):
        p16.assign_vehicle(conn, cid, "VEH-03", "DRV-01", "CRD-02")


def test_driver_problems_rule() -> None:
    drv = {"driver_id": "DRV-X", "active": 1, "licence_class": "C", "licence_expiry": "2026-11-18"}
    assert p16.driver_problems(drv, "truck", date(2026, 11, 18)) == []  # valid on its expiry day
    assert p16.driver_problems(drv, "truck", date(2026, 11, 19)) == ["DRV-X licence expired on 2026-11-18"]
    assert "not an active driver" in p16.driver_problems({**drv, "active": 0}, "van", date(2026, 11, 18))[0]


# -- advisories ---------------------------------------------------------------------------
def test_blocked_stop_excluded_from_suggestion_and_blocks_approval(conn: sqlite3.Connection) -> None:
    s = sc()
    cid, site, area = s["convoy_with_blocked_stop"], s["blocked_site_id"], s["blocked_area_id"]
    plan = p16.plan_suggestion(conn, cid)
    assert site not in plan["suggested_order"]
    assert plan["suggested_order"] == ["SITE-02", "SITE-08"]
    assert [e["site_id"] for e in plan["excluded"]] == [site]
    assert f"is in {area}" in plan["excluded"][0]["reason"] and "level 2 Restricted" in plan["excluded"][0]["reason"]
    assert plan["manual_order"] == ["SITE-09", "SITE-02", "SITE-08"] and plan["manual_blocked"] == [site]
    tick_all(conn, cid)
    with pytest.raises(p16.ConvoyBlocked) as err:
        p16.approve(conn, cid, "CRD-02")
    assert f"Stop in a blocked area: {site}" in str(err.value)
    with pytest.raises(p16.PlanError, match="Remove the stops in blocked areas first"):
        p16.apply_suggestion(conn, cid, "CRD-02")
    assert p16.remove_stop(conn, cid, site, "CRD-02", reason="Restricted advisory") == 2  # its two load lines go too
    p16.apply_suggestion(conn, cid, "CRD-02")
    assert p16.blockers(conn, cid) == []
    assert p16.approve(conn, cid, "CRD-02")["status"] == "approved"


# -- stop order and distances -----------------------------------------------------------------
def test_nearest_neighbour_small_known_case() -> None:
    origin = (0.0, 0.0)
    pos = {"A": (10.0, 0.0), "B": (1.0, 0.0), "C": (5.0, 0.0)}
    order = routing.nearest_neighbour_order(origin, pos)
    assert order == ["B", "C", "A"]
    assert routing.route_distance(origin, order, pos) == 26.0  # (1 + 4 + 5 + 10) km x 1.3
    assert routing.route_distance(origin, ["A", "B", "C"], pos) == 36.4  # (10 + 9 + 4 + 5) x 1.3
    assert routing.route_distance(origin, [], pos) == 0.0


def test_nearest_neighbour_tie_goes_to_lower_id() -> None:
    assert routing.nearest_neighbour_order((0.0, 0.0), {"S2": (3.0, 0.0), "S1": (-3.0, 0.0)})[0] == "S1"


def test_seed_case_suggestion_not_longer_than_manual(conn: sqlite3.Connection) -> None:
    cid = build_walkthrough(conn, tick=False)
    plan = p16.plan_suggestion(conn, cid)
    assert plan["manual_order"] == WALK_STOPS
    assert plan["suggested_order"] == ["SITE-02", "SITE-03", "SITE-04"]
    assert plan["manual_distance_km"] == 200.82 and plan["suggested_distance_km"] == 125.96
    assert plan["suggested_distance_km"] <= plan["manual_distance_km"]
    row = p16.get_convoy(conn, cid)
    assert json.loads(row["manual_order_json"]) == WALK_STOPS
    assert json.loads(row["suggested_order_json"]) == ["SITE-02", "SITE-03", "SITE-04"]
    assert (row["manual_distance_km"], row["suggested_distance_km"]) == (200.82, 125.96)
    cnv3 = p16.plan_suggestion(conn, sc()["convoy_with_blocked_stop"])
    assert cnv3["suggested_distance_km"] <= cnv3["manual_distance_km"]


def test_manual_order_edit(conn: sqlite3.Connection) -> None:
    cid = build_walkthrough(conn, tick=False)
    assert p16.set_manual_order(conn, cid, ["SITE-02", "SITE-04", "SITE-03"], "CRD-02") == 125.62
    assert p16.stop_ids(conn, cid) == ["SITE-02", "SITE-04", "SITE-03"]
    # Nearest neighbour is a heuristic: this hand-made order is 0.34 km shorter, and the comparison says so.
    assert p16.plan_suggestion(conn, cid)["saving_km"] == -0.34
    with pytest.raises(p16.PlanError, match="every stop exactly once"):
        p16.set_manual_order(conn, cid, ["SITE-02", "SITE-04"], "CRD-02")


# -- vehicle split ------------------------------------------------------------------------------
def line(site: str, qty: int, unit: float, item: str = "ITM") -> dict[str, Any]:
    return {"site_id": site, "item_id": item, "quantity": qty, "unit_weight_kg": unit}


def test_first_fit_decreasing_keeps_stops_whole() -> None:
    out = routing.split_loads([("V2", 500), ("V1", 1000)], [line("S3", 30, 10), line("S1", 60, 10), line("S2", 45, 10)])
    assert {r["site_id"]: r["vehicle_id"] for r in out} == {"S1": "V1", "S2": "V2", "S3": "V1"}
    assert routing.primary_vehicle(out) == {"S1": "V1", "S2": "V2", "S3": "V1"}


def test_split_by_quantity_and_overflow() -> None:
    out = routing.split_loads([("V1", 100), ("V2", 100)], [line("S1", 15, 10)])
    assert sorted((r["vehicle_id"], r["quantity"]) for r in out) == [("V1", 10), ("V2", 5)]
    out = routing.split_loads([("V1", 100), ("V2", 100)], [line("S1", 25, 10)])
    weights = {r["vehicle_id"]: r["weight_kg"] for r in out}
    assert sum(r["quantity"] for r in out) == 25
    assert any(routing.is_overloaded(w, 100) for w in weights.values())
    with pytest.raises(ValueError):
        routing.split_loads([], [line("S1", 1, 1)])


def test_apply_suggestion_records_vehicle_per_stop(conn: sqlite3.Connection) -> None:
    cid = build_walkthrough(conn, tick=False)
    p16.assign_vehicle(conn, cid, "VEH-02", "DRV-07", "CRD-02")
    plan = p16.apply_suggestion(conn, cid, "CRD-02")
    stops = {
        r["site_id"]: (r["stop_seq"], r["vehicle_id"]) for r in db.query_all("SELECT * FROM convoy_stops WHERE convoy_id = ?", (cid,), conn)
    }
    assert stops == {"SITE-02": (1, "VEH-08"), "SITE-03": (2, "VEH-08"), "SITE-04": (3, "VEH-08")}
    assert plan["split_weights"] == {"VEH-08": WALK_KG}
    # quantities are preserved by the split
    assert sum(r["quantity"] for r in p16.load_lines(conn, cid)) == sum(q for _, _, q in WALK_LOADS)


# -- stock --------------------------------------------------------------------------------
def test_stock_limit_across_lines(conn: sqlite3.Connection) -> None:
    cid = sc()["draft_convoy_id"]
    p16.assign_vehicle(conn, cid, "VEH-08", "DRV-01", "CRD-02")
    p16.add_stop(conn, cid, "SITE-02", "CRD-02")
    p16.add_stop(conn, cid, "SITE-03", "CRD-02")
    have = stock.on_hand_qty(conn, "WH-03", "ITM-HYG")
    assert have == 30
    with pytest.raises(p16.PlanError, match="31 kits, only 30 kits on hand"):
        p16.add_load(conn, cid, "SITE-02", "ITM-HYG", have + 1, "VEH-08", "CRD-02")
    p16.add_load(conn, cid, "SITE-02", "ITM-HYG", 20, "VEH-08", "CRD-02")
    p16.add_load(conn, cid, "SITE-03", "ITM-HYG", 10, "VEH-08", "CRD-02")  # exactly what is on hand
    with pytest.raises(p16.PlanError, match="Not enough Family hygiene kit"):
        p16.add_load(conn, cid, "SITE-03", "ITM-HYG", 1, "VEH-08", "CRD-02")


def test_stock_used_elsewhere_blocks_approval(conn: sqlite3.Connection) -> None:
    cid = build_walkthrough(conn)
    stock.record_dispatch(conn, "WH-03", "SITE-08", "ITM-MAT", 5, "WHO-01")  # stock leaves before approval
    with pytest.raises(p16.ConvoyBlocked, match="Not enough Foam mattress at WH-03: 30 pieces planned, 25 pieces on hand"):
        p16.approve(conn, cid, "CRD-02")


# -- approval and checklist ----------------------------------------------------------------------
def test_approval_requires_coordinator_confirmation(conn: sqlite3.Connection) -> None:
    cid = build_walkthrough(conn)
    with pytest.raises(confirm.ConfirmationRequired):
        p16.approve(conn, cid, None)
    with pytest.raises(confirm.ConfirmationRequired, match="needs one of coordinator"):
        p16.approve(conn, cid, "WHO-01")
    assert p16.get_convoy(conn, cid)["status"] == "draft"
    assert p16.approve(conn, cid, "CRD-01")["approved_by"] == "CRD-01"


def test_incomplete_checklist_blocks_approval(conn: sqlite3.Connection) -> None:
    cid = build_walkthrough(conn)
    p16.set_check(conn, cid, "first_aid", False, "CRD-02")
    with pytest.raises(p16.ConvoyBlocked) as err:
        p16.approve(conn, cid, "CRD-02")
    assert err.value.reasons == ["Pre-departure checklist incomplete: 1 of 8 items not ticked (first_aid)"]


def test_empty_convoy_lists_every_reason(conn: sqlite3.Connection) -> None:
    cid = sc()["draft_convoy_id"]
    found = p16.blockers(conn, cid)
    assert found[:3] == ["No vehicles assigned", "No destination stops", "No load lines"]
    assert found[3].startswith("Pre-departure checklist incomplete: 8 of 8")


def test_checklist_tick_records_staff_and_time(conn: sqlite3.Connection) -> None:
    cid = sc()["draft_convoy_id"]
    assert p16.generate_checklist(conn, cid, "CRD-02") == 0  # seed already made the 8 rows
    assert [r["item_key"] for r in p16.checklist(conn, cid)] == [k for k, _ in p16.CHECKLIST]
    p16.set_check(conn, cid, "fuel", True, "FW-01")
    row = db.query_one("SELECT * FROM convoy_checklist WHERE convoy_id = ? AND item_key = 'fuel'", (cid,), conn)
    assert (row["checked"], row["checked_by"], row["checked_at"]) == (1, "FW-01", "2026-11-16T10:00:00")


def test_new_convoy_gets_checklist_event_and_audit(conn: sqlite3.Connection) -> None:
    new_id = p16.create_convoy(conn, "Test run", "2026-11-20", "WH-01", "CRD-01", "CRD-01")
    assert new_id == "CNV-005"
    assert len(p16.checklist(conn, new_id)) == 8
    assert [e["status"] for e in p16.events(conn, new_id)] == ["draft"]
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE entity_id = ? AND channel = 'app:16'", (new_id,)).fetchone()[0] >= 2
    with pytest.raises(p16.PlanError, match="in the past"):
        p16.create_convoy(conn, "Late", "2026-11-01", "WH-01", "CRD-01", "CRD-01")


# -- status tracking ----------------------------------------------------------------------------------
def test_status_transitions_in_order_only(conn: sqlite3.Connection) -> None:
    cid = build_walkthrough(conn)
    with pytest.raises(p16.PlanError, match="only an approved convoy can depart"):
        p16.depart(conn, cid, "CRD-02")
    with pytest.raises(p16.PlanError, match="can only become arrived from departed"):
        p16.mark_arrived(conn, cid, "CRD-02")
    p16.mark_planned(conn, cid, "CRD-02")
    p16.approve(conn, cid, "CRD-02")
    with pytest.raises(p16.PlanError, match="plan can only be changed while it is draft or planned"):
        p16.add_stop(conn, cid, "SITE-08", "CRD-02")
    with pytest.raises(p16.PlanError, match="can only become returned from arrived"):
        p16.mark_returned(conn, cid, "CRD-02")
    p16.depart(conn, cid, "CRD-02")
    with pytest.raises(p16.PlanError, match="can only become cancelled"):
        p16.cancel(conn, cid, "CRD-02", "Too late")
    p16.mark_arrived(conn, cid, "CRD-02")
    p16.mark_returned(conn, cid, "CRD-02")
    assert [e["status"] for e in p16.events(conn, cid)] == ["draft", "planned", "approved", "departed", "arrived", "returned"]
    row = p16.get_convoy(conn, cid)
    assert row["status"] == "returned" and all(
        row[k] == "2026-11-16T10:00:00" for k in ("approved_at", "departed_at", "arrived_at", "returned_at")
    )
    actions = [
        r[0]
        for r in conn.execute(
            "SELECT action FROM audit_log WHERE entity_table = 'convoys' AND entity_id = ? AND channel = 'app:16' ORDER BY audit_id", (cid,)
        )
    ]
    assert [a for a in actions if a in ("update", "approve", "dispatch")] == ["update", "approve", "dispatch", "update", "update"]


def test_cancel_needs_confirmation_and_reason(conn: sqlite3.Connection) -> None:
    cid = sc()["draft_convoy_id"]
    with pytest.raises(confirm.ConfirmationRequired):
        p16.cancel(conn, cid, "FW-01", "No trucks")
    with pytest.raises(p16.PlanError, match="needs a reason"):
        p16.cancel(conn, cid, "CRD-02", " ")
    p16.cancel(conn, cid, "CRD-02", "Road closed")
    assert p16.get_convoy(conn, cid)["status"] == "cancelled"
    assert p16.events(conn, cid)[-1]["note"] == "Road closed"


def test_every_planning_write_is_audited(conn: sqlite3.Connection) -> None:
    before = conn.execute("SELECT COUNT(*) FROM audit_log WHERE channel = 'app:16'").fetchone()[0]
    build_walkthrough(conn)
    after = conn.execute("SELECT COUNT(*) FROM audit_log WHERE channel = 'app:16'").fetchone()[0]
    assert after - before == 1 + len(WALK_STOPS) + len(WALK_LOADS) + 8  # assign + stops + loads + ticks


# -- dispatch ledger ----------------------------------------------------------------------------------
def test_departure_writes_one_dispatch_and_ledger_row_per_line(conn: sqlite3.Connection) -> None:
    cid = build_walkthrough(conn)
    p16.approve(conn, cid, "CRD-02")
    before = {i: stock.on_hand_qty(conn, "WH-03", i) for i in ("ITM-BLK", "ITM-HYG", "ITM-MAT")}
    moves_before = conn.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0]
    ids = p16.depart(conn, cid, "CRD-02")
    assert len(ids) == len(WALK_LOADS) == dispatch_count(conn, cid)
    assert conn.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] - moves_before == len(WALK_LOADS)
    ledger = conn.execute(
        f"SELECT COUNT(*) FROM stock_movements WHERE movement_type = 'dispatched' AND dispatch_id IN ({','.join('?' * len(ids))})", ids
    ).fetchone()[0]
    assert ledger == len(WALK_LOADS)
    planned = {"ITM-BLK": 180, "ITM-HYG": 30, "ITM-MAT": 30}
    for item, qty in planned.items():
        assert stock.on_hand_qty(conn, "WH-03", item) == before[item] - qty
    assert p16.get_convoy(conn, cid)["status"] == "departed"


def test_already_departed_seed_convoy_is_not_dispatched_again(conn: sqlite3.Connection) -> None:
    n = dispatch_count(conn, "CNV-002")
    with pytest.raises(p16.PlanError):
        p16.depart(conn, "CNV-002", "CRD-01")
    assert dispatch_count(conn, "CNV-002") == n


# -- walkthrough ----------------------------------------------------------------------------------
def test_full_walkthrough(conn: sqlite3.Connection) -> None:
    s = sc()
    cid = s["draft_convoy_id"]
    assert p16.get_convoy(conn, cid)["status"] == "draft" and p16.assigned_vehicles(conn, cid) == []
    with pytest.raises(p16.VehicleNotReady, match="Service overdue since"):
        p16.assign_vehicle(conn, cid, s["overdue_vehicle_id"], "DRV-01", "CRD-02")
    assert p16.assign_vehicle(conn, cid, "VEH-08", "DRV-01", "CRD-02").status == "ready"
    for site in WALK_STOPS:
        p16.add_stop(conn, cid, site, "CRD-02")
    for site, item, qty in WALK_LOADS:
        p16.add_load(conn, cid, site, item, qty, "VEH-08", "CRD-02")
    veh = p16.assigned_vehicles(conn, cid)[0]
    assert (veh["load_kg"], veh["remaining_kg"], veh["overloaded"]) == (660.0, 5340.0, False)
    plan = p16.plan_suggestion(conn, cid)
    assert (plan["manual_distance_km"], plan["suggested_distance_km"], plan["saving_km"]) == (200.82, 125.96, 74.86)
    p16.apply_suggestion(conn, cid, "CRD-02")
    assert p16.stop_ids(conn, cid) == ["SITE-02", "SITE-03", "SITE-04"]
    assert p16.blockers(conn, cid) == [
        "Pre-departure checklist incomplete: 8 of 8 items not ticked (" + ", ".join(k for k, _ in p16.CHECKLIST) + ")"
    ]
    tick_all(conn, cid)
    assert p16.approve(conn, cid, "CRD-02")["status"] == "approved"
    assert len(p16.depart(conn, cid, "CRD-02")) == 7
    p16.mark_arrived(conn, cid, "CRD-02")
    p16.mark_returned(conn, cid, "CRD-02")
    assert p16.get_convoy(conn, cid)["status"] == "returned"


# -- app ------------------------------------------------------------------------------------------
def test_app_starts(app_test) -> None:  # noqa: ANN001
    at = app_test(16).run()
    assert not at.exception
    assert at.selectbox(key="p16_convoy").value == "CNV-004"


def test_app_assign_overdue_vehicle_shows_reason(app_test, conn: sqlite3.Connection) -> None:  # noqa: ANN001
    at = app_test(16).run()
    at.selectbox(key="p16_vehicle").set_value("VEH-04")
    at.button(key="p16_assign").click().run()
    assert not at.exception
    errors = [e.value for e in at.error]
    assert any(e.startswith("VEH-04 is not ready on") and "Service overdue since" in e for e in errors)
    assert conn.execute("SELECT COUNT(*) FROM convoy_vehicles WHERE convoy_id = 'CNV-004'").fetchone()[0] == 0


def test_app_blocked_stop_shown_for_cnv003(app_test) -> None:  # noqa: ANN001
    at = app_test(16).run()
    at.selectbox(key="p16_convoy").set_value("CNV-003").run()
    assert not at.exception
    assert any("SITE-09" in e.value and "Restricted" in e.value for e in at.error)
