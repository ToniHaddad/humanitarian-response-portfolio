"""Convoy planning, approval and tracking for Project 16.

Every function takes an open connection and does not commit; the caller wraps it
in shared.db.transaction() so the change and its audit row land together.

Rules used here are not redefined: vehicle readiness comes from shared.fleet
(Project 14) and blocked areas from shared.security (Project 15).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

import p16_routing as routing

from shared import clock, confirm, db, fleet, security, stock

CHANNEL = "app:16"
APPROVER_ROLES: tuple[str, ...] = ("coordinator",)
EDITABLE = ("draft", "planned")
STATUS_FLOW = ("draft", "planned", "approved", "departed", "arrived", "returned")
# new status -> statuses it may come from
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "planned": ("draft",),
    "approved": ("draft", "planned"),
    "departed": ("approved",),
    "arrived": ("departed",),
    "returned": ("arrived",),
    "cancelled": ("draft", "planned", "approved"),
}
TIMESTAMP_COLUMN = {"departed": "departed_at", "arrived": "arrived_at", "returned": "returned_at"}

# Same keys and labels as the seed uses for the pre-departure checklist.
CHECKLIST: tuple[tuple[str, str], ...] = (
    ("vehicle_inspection", "Vehicles inspected and ready"),
    ("fuel", "Fuel tanks full"),
    ("licences", "Driver licences checked"),
    ("load_secured", "Load secured and within capacity"),
    ("comms", "Radio and phone check done"),
    ("advisories", "Movement advisories checked for every stop"),
    ("manifest", "Load manifest printed"),
    ("first_aid", "First aid kit on board"),
)
TRUCK_LICENCE = "C"


class PlanError(ValueError):
    """A planning step was refused. reasons lists every reason found."""

    def __init__(self, message: str, reasons: list[str] | None = None) -> None:
        super().__init__(message)
        self.reasons = reasons or [message]


class VehicleNotReady(PlanError):
    pass


class DriverInvalid(PlanError):
    pass


class ConvoyBlocked(PlanError):
    pass


# -- small helpers ----------------------------------------------------------
def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def plural(unit: str, n: float) -> str:
    if n == 1:
        return unit
    return unit + ("es" if unit.endswith(("x", "s")) else "s")


def qty_label(n: int, unit: str) -> str:
    return f"{n} {plural(unit, n)}"


def get_convoy(conn: sqlite3.Connection, convoy_id: str) -> dict[str, Any]:
    row = db.row_dict(conn, "convoys", "convoy_id", convoy_id)
    if row is None:
        raise PlanError(f"Unknown convoy {convoy_id}")
    return row


def _editable(conn: sqlite3.Connection, convoy_id: str) -> dict[str, Any]:
    cv = get_convoy(conn, convoy_id)
    if cv["status"] not in EDITABLE:
        raise PlanError(f"{convoy_id} is {cv['status']}; the plan can only be changed while it is draft or planned.")
    return cv


def _audit(conn: sqlite3.Connection, staff_id: str, action: str, table: str, entity_id: str, **kw: Any) -> None:
    db.audit(conn, actor_id=staff_id, channel=CHANNEL, action=action, entity_table=table, entity_id=entity_id, **kw)


def manual_order(cv: dict[str, Any]) -> list[str]:
    return list(json.loads(cv["manual_order_json"])) if cv.get("manual_order_json") else []


def list_convoys(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return db.query_all(
        """SELECT c.convoy_id, c.name, c.planned_date, c.origin_warehouse_id, c.status, c.coordinator_id,
                  (SELECT COUNT(*) FROM convoy_vehicles v WHERE v.convoy_id = c.convoy_id) AS vehicles,
                  (SELECT COUNT(*) FROM convoy_stops s WHERE s.convoy_id = c.convoy_id) AS stops,
                  (SELECT COUNT(*) FROM convoy_loads l WHERE l.convoy_id = c.convoy_id) AS load_lines
           FROM convoys c ORDER BY c.planned_date DESC, c.convoy_id""",
        conn=conn,
    )


# -- convoy -----------------------------------------------------------------
def create_convoy(
    conn: sqlite3.Connection, name: str, planned_date: date | str, origin_warehouse_id: str, coordinator_id: str, staff_id: str
) -> str:
    if not name or not name.strip():
        raise PlanError("Give the convoy a name.")
    if conn.execute("SELECT 1 FROM warehouses WHERE warehouse_id = ?", (origin_warehouse_id,)).fetchone() is None:
        raise PlanError(f"Unknown warehouse {origin_warehouse_id}")
    if db.staff_role(conn, coordinator_id) not in ("coordinator", "admin"):
        raise PlanError(f"{coordinator_id} is not a coordinator.")
    planned = _date(planned_date)
    if planned < clock.today():
        raise PlanError(f"Planned date {planned.isoformat()} is in the past.")
    convoy_id = db.next_id(conn, "convoys", "convoy_id", "CNV-", 3)
    row = dict(
        convoy_id=convoy_id,
        name=name.strip(),
        planned_date=planned.isoformat(),
        origin_warehouse_id=origin_warehouse_id,
        status="draft",
        coordinator_id=coordinator_id,
        manual_order_json=json.dumps([]),
    )
    conn.execute(
        "INSERT INTO convoys(convoy_id, name, planned_date, origin_warehouse_id, status, coordinator_id, manual_order_json) "
        "VALUES (:convoy_id, :name, :planned_date, :origin_warehouse_id, :status, :coordinator_id, :manual_order_json)",
        row,
    )
    conn.execute(
        "INSERT INTO convoy_events(convoy_id, at, status, staff_id, note) VALUES (?,?,?,?,?)",
        (convoy_id, clock.iso(), "draft", staff_id, "Draft created"),
    )
    _audit(conn, staff_id, "create", "convoys", convoy_id, after=row)
    generate_checklist(conn, convoy_id, staff_id)
    return convoy_id


# -- vehicles and drivers -----------------------------------------------------
def readiness_on(conn: sqlite3.Connection, on: date | str) -> dict[str, fleet.Readiness]:
    return fleet.fleet_readiness(conn, on=_date(on))


def driver_problems(driver: dict[str, Any], vehicle_type: str, on: date | str) -> list[str]:
    """Why this driver cannot drive this vehicle type on that date (empty list = fine)."""
    problems = []
    if not driver["active"]:
        problems.append(f"{driver['driver_id']} is not an active driver")
    if _date(driver["licence_expiry"]) < _date(on):
        problems.append(f"{driver['driver_id']} licence expired on {_date(driver['licence_expiry']).isoformat()}")
    if vehicle_type == "truck" and driver["licence_class"] != TRUCK_LICENCE:
        problems.append(f"{driver['driver_id']} holds a class {driver['licence_class']} licence; a truck needs class {TRUCK_LICENCE}")
    return problems


def not_ready_message(vehicle_id: str, on: date | str, reasons: list[str]) -> str:
    return f"{vehicle_id} is not ready on {_date(on).isoformat()}: " + "; ".join(reasons)


def _same_day_conflicts(conn: sqlite3.Connection, convoy: dict[str, Any], column: str, value: str) -> list[str]:
    rows = conn.execute(
        f"""SELECT c.convoy_id FROM convoy_vehicles v JOIN convoys c ON c.convoy_id = v.convoy_id
            WHERE v.{column} = ? AND c.convoy_id <> ? AND c.planned_date = ? AND c.status NOT IN ('cancelled','returned')""",
        (value, convoy["convoy_id"], convoy["planned_date"]),
    ).fetchall()
    return [r[0] for r in rows]


def assign_vehicle(conn: sqlite3.Connection, convoy_id: str, vehicle_id: str, driver_id: str, staff_id: str) -> fleet.Readiness:
    """Add a vehicle and its driver to the convoy. Refused, with the reasons, if either is not fit to go."""
    cv = _editable(conn, convoy_id)
    vehicle = db.row_dict(conn, "vehicles", "vehicle_id", vehicle_id)
    if vehicle is None:
        raise PlanError(f"Unknown vehicle {vehicle_id}")
    if conn.execute("SELECT 1 FROM convoy_vehicles WHERE convoy_id = ? AND vehicle_id = ?", (convoy_id, vehicle_id)).fetchone():
        raise PlanError(f"{vehicle_id} is already on {convoy_id}.")
    readiness = readiness_on(conn, cv["planned_date"])[vehicle_id]
    if not readiness.ready:
        raise VehicleNotReady(not_ready_message(vehicle_id, cv["planned_date"], readiness.reasons), readiness.reasons)
    busy = _same_day_conflicts(conn, cv, "vehicle_id", vehicle_id)
    if busy:
        raise PlanError(f"{vehicle_id} is already on {', '.join(busy)} on {cv['planned_date']}.")
    driver = db.row_dict(conn, "drivers", "driver_id", driver_id)
    if driver is None:
        raise PlanError(f"Unknown driver {driver_id}")
    problems = driver_problems(driver, vehicle["vehicle_type"], cv["planned_date"])
    on_convoy = conn.execute(
        "SELECT vehicle_id FROM convoy_vehicles WHERE convoy_id = ? AND driver_id = ?", (convoy_id, driver_id)
    ).fetchone()
    if on_convoy:
        problems.append(f"{driver_id} already drives {on_convoy[0]} on this convoy")
    busy = _same_day_conflicts(conn, cv, "driver_id", driver_id)
    if busy:
        problems.append(f"{driver_id} is already driving on {', '.join(busy)} on {cv['planned_date']}")
    if problems:
        raise DriverInvalid(f"{driver_id} cannot drive {vehicle_id}: " + "; ".join(problems), problems)
    conn.execute("INSERT INTO convoy_vehicles(convoy_id, vehicle_id, driver_id) VALUES (?,?,?)", (convoy_id, vehicle_id, driver_id))
    _audit(
        conn,
        staff_id,
        "assign_vehicle",
        "convoy_vehicles",
        f"{convoy_id}/{vehicle_id}",
        after={"driver_id": driver_id, "readiness": readiness.status, "warnings": readiness.warnings},
    )
    return readiness


def remove_vehicle(conn: sqlite3.Connection, convoy_id: str, vehicle_id: str, staff_id: str) -> None:
    _editable(conn, convoy_id)
    row = db.query_one("SELECT * FROM convoy_vehicles WHERE convoy_id = ? AND vehicle_id = ?", (convoy_id, vehicle_id), conn)
    if row is None:
        raise PlanError(f"{vehicle_id} is not on {convoy_id}.")
    lines = conn.execute("SELECT COUNT(*) FROM convoy_loads WHERE convoy_id = ? AND vehicle_id = ?", (convoy_id, vehicle_id)).fetchone()[0]
    if lines:
        raise PlanError(f"{vehicle_id} still carries {lines} load line(s). Remove them or apply the suggested split first.")
    conn.execute("DELETE FROM convoy_vehicles WHERE convoy_id = ? AND vehicle_id = ?", (convoy_id, vehicle_id))
    conn.execute("UPDATE convoy_stops SET vehicle_id = NULL WHERE convoy_id = ? AND vehicle_id = ?", (convoy_id, vehicle_id))
    _audit(conn, staff_id, "remove_vehicle", "convoy_vehicles", f"{convoy_id}/{vehicle_id}", before=row)


def assigned_vehicles(conn: sqlite3.Connection, convoy_id: str, on: date | str | None = None) -> list[dict[str, Any]]:
    """Vehicles on the convoy with driver, readiness (on the planned date unless given) and load weight."""
    cv = get_convoy(conn, convoy_id)
    when = _date(on or cv["planned_date"])
    ready = readiness_on(conn, when)
    rows = db.query_all(
        """SELECT cv.vehicle_id, cv.driver_id, v.vehicle_type, v.load_capacity_kg,
                  d.licence_class, d.licence_expiry, d.active AS driver_active,
                  COALESCE((SELECT SUM(l.weight_kg) FROM convoy_loads l WHERE l.convoy_id = cv.convoy_id AND l.vehicle_id = cv.vehicle_id), 0)
                      AS load_kg
           FROM convoy_vehicles cv JOIN vehicles v ON v.vehicle_id = cv.vehicle_id JOIN drivers d ON d.driver_id = cv.driver_id
           WHERE cv.convoy_id = ? ORDER BY cv.vehicle_id""",
        (convoy_id,),
        conn,
    )
    for r in rows:
        rd = ready[r["vehicle_id"]]
        r["readiness"] = rd.status
        r["reasons"] = rd.reasons
        r["warnings"] = rd.warnings
        r["load_kg"] = round(float(r["load_kg"]), 2)
        r["remaining_kg"] = round(float(r["load_capacity_kg"]) - r["load_kg"], 2)
        r["overloaded"] = routing.is_overloaded(r["load_kg"], r["load_capacity_kg"])
        drv = {
            "driver_id": r["driver_id"],
            "active": r["driver_active"],
            "licence_class": r["licence_class"],
            "licence_expiry": r["licence_expiry"],
        }
        r["driver_problems"] = driver_problems(drv, r["vehicle_type"], when)
    return rows


# -- stops and manual order ---------------------------------------------------
def _set_manual(conn: sqlite3.Connection, convoy_id: str, order: list[str]) -> None:
    conn.execute("UPDATE convoys SET manual_order_json = ? WHERE convoy_id = ?", (json.dumps(order), convoy_id))
    for seq, site in enumerate(order, start=1):
        conn.execute("UPDATE convoy_stops SET stop_seq = ? WHERE convoy_id = ? AND site_id = ?", (seq, convoy_id, site))


def stop_ids(conn: sqlite3.Connection, convoy_id: str) -> list[str]:
    return [r[0] for r in conn.execute("SELECT site_id FROM convoy_stops WHERE convoy_id = ? ORDER BY stop_seq, site_id", (convoy_id,))]


def add_stop(conn: sqlite3.Connection, convoy_id: str, site_id: str, staff_id: str) -> None:
    """Add a destination site. It goes last in the coordinator's manual order."""
    cv = _editable(conn, convoy_id)
    if conn.execute("SELECT 1 FROM sites WHERE site_id = ?", (site_id,)).fetchone() is None:
        raise PlanError(f"Unknown site {site_id}")
    if site_id in stop_ids(conn, convoy_id):
        raise PlanError(f"{site_id} is already a stop on {convoy_id}.")
    order = [s for s in manual_order(cv) if s in stop_ids(conn, convoy_id)]
    for s in stop_ids(conn, convoy_id):
        if s not in order:
            order.append(s)
    order.append(site_id)
    conn.execute("INSERT INTO convoy_stops(convoy_id, site_id, vehicle_id, stop_seq) VALUES (?,?,NULL,?)", (convoy_id, site_id, len(order)))
    _set_manual(conn, convoy_id, order)
    _audit(conn, staff_id, "add_stop", "convoy_stops", f"{convoy_id}/{site_id}", after={"manual_order": order})
    store_plan_fields(conn, convoy_id)


def remove_stop(conn: sqlite3.Connection, convoy_id: str, site_id: str, staff_id: str, reason: str | None = None) -> int:
    """Remove a stop and its load lines. Returns the number of load lines removed."""
    cv = _editable(conn, convoy_id)
    if site_id not in stop_ids(conn, convoy_id):
        raise PlanError(f"{site_id} is not a stop on {convoy_id}.")
    lines = db.query_all("SELECT * FROM convoy_loads WHERE convoy_id = ? AND site_id = ?", (convoy_id, site_id), conn)
    conn.execute("DELETE FROM convoy_loads WHERE convoy_id = ? AND site_id = ?", (convoy_id, site_id))
    conn.execute("DELETE FROM convoy_stops WHERE convoy_id = ? AND site_id = ?", (convoy_id, site_id))
    order = [s for s in manual_order(cv) if s != site_id and s in stop_ids(conn, convoy_id)]
    _set_manual(conn, convoy_id, order)
    _audit(conn, staff_id, "remove_stop", "convoy_stops", f"{convoy_id}/{site_id}", before={"load_lines": lines}, reason=reason)
    store_plan_fields(conn, convoy_id)
    return len(lines)


def set_manual_order(conn: sqlite3.Connection, convoy_id: str, order: list[str], staff_id: str) -> float:
    """The coordinator's own visiting order. Must list every stop exactly once. Returns its distance in km."""
    cv = _editable(conn, convoy_id)
    stops = stop_ids(conn, convoy_id)
    if sorted(order) != sorted(stops) or len(set(order)) != len(order):
        raise PlanError(f"The manual order must list every stop exactly once: {', '.join(sorted(stops))}.")
    before = manual_order(cv)
    _set_manual(conn, convoy_id, list(order))
    _audit(conn, staff_id, "set_manual_order", "convoys", convoy_id, before={"manual_order": before}, after={"manual_order": order})
    return store_plan_fields(conn, convoy_id)["manual_distance_km"]


# -- load plan ----------------------------------------------------------------
def load_lines(conn: sqlite3.Connection, convoy_id: str) -> list[dict[str, Any]]:
    return db.query_all(
        """SELECT l.load_id, l.site_id, l.vehicle_id, l.item_id, i.name AS item_name, i.unit, l.quantity, i.unit_weight_kg, l.weight_kg
           FROM convoy_loads l JOIN items i ON i.item_id = l.item_id
           LEFT JOIN convoy_stops s ON s.convoy_id = l.convoy_id AND s.site_id = l.site_id
           WHERE l.convoy_id = ? ORDER BY s.stop_seq, l.site_id, l.vehicle_id, l.item_id, l.load_id""",
        (convoy_id,),
        conn,
    )


def stock_check(conn: sqlite3.Connection, convoy_id: str, extra: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Per item: quantity planned on this convoy (plus extra) against on-hand stock at the origin warehouse."""
    cv = get_convoy(conn, convoy_id)
    need: dict[str, int] = {}
    for r in conn.execute("SELECT item_id, SUM(quantity) FROM convoy_loads WHERE convoy_id = ? GROUP BY item_id", (convoy_id,)):
        need[r[0]] = int(r[1])
    for item, q in (extra or {}).items():
        need[item] = need.get(item, 0) + int(q)
    out = []
    for item, q in sorted(need.items()):
        it = db.row_dict(conn, "items", "item_id", item) or {"name": item, "unit": "unit"}
        have = stock.on_hand_qty(conn, cv["origin_warehouse_id"], item)
        out.append({"item_id": item, "name": it["name"], "unit": it["unit"], "planned": q, "on_hand": have, "short": max(0, q - have)})
    return out


def shortage_messages(conn: sqlite3.Connection, convoy_id: str, extra: dict[str, int] | None = None) -> list[str]:
    cv = get_convoy(conn, convoy_id)
    return [
        f"Not enough {r['name']} at {cv['origin_warehouse_id']}: {qty_label(r['planned'], r['unit'])} planned, "
        f"{qty_label(r['on_hand'], r['unit'])} on hand"
        for r in stock_check(conn, convoy_id, extra)
        if r["short"] > 0
    ]


def _sync_stop_vehicles(conn: sqlite3.Connection, convoy_id: str) -> None:
    primary = routing.primary_vehicle(load_lines(conn, convoy_id))
    for site in stop_ids(conn, convoy_id):
        conn.execute("UPDATE convoy_stops SET vehicle_id = ? WHERE convoy_id = ? AND site_id = ?", (primary.get(site), convoy_id, site))


def add_load(
    conn: sqlite3.Connection, convoy_id: str, site_id: str, item_id: str, quantity: int, vehicle_id: str, staff_id: str
) -> dict[str, Any]:
    """Add one load line. Refused if it would take more than the origin warehouse holds.

    Overloading a vehicle is allowed while planning but flagged (returned) and blocks approval.
    """
    _editable(conn, convoy_id)
    quantity = int(quantity)
    if quantity <= 0:
        raise PlanError("Quantity must be at least 1.")
    if site_id not in stop_ids(conn, convoy_id):
        raise PlanError(f"{site_id} is not a stop on {convoy_id}; add it as a stop first.")
    if conn.execute("SELECT 1 FROM convoy_vehicles WHERE convoy_id = ? AND vehicle_id = ?", (convoy_id, vehicle_id)).fetchone() is None:
        raise PlanError(f"{vehicle_id} is not assigned to {convoy_id}.")
    item = db.row_dict(conn, "items", "item_id", item_id)
    if item is None:
        raise PlanError(f"Unknown item {item_id}")
    row = next(r for r in stock_check(conn, convoy_id, {item_id: quantity}) if r["item_id"] == item_id)
    if row["short"] > 0:
        raise PlanError(
            f"Not enough {item['name']} at the origin warehouse: this line would bring the convoy to "
            f"{qty_label(row['planned'], item['unit'])}, only {qty_label(row['on_hand'], item['unit'])} on hand."
        )
    weight = round(quantity * float(item["unit_weight_kg"]), 2)
    cur = conn.execute(
        "INSERT INTO convoy_loads(convoy_id, vehicle_id, site_id, item_id, quantity, weight_kg) VALUES (?,?,?,?,?,?)",
        (convoy_id, vehicle_id, site_id, item_id, quantity, weight),
    )
    load_id = int(cur.lastrowid)
    _sync_stop_vehicles(conn, convoy_id)
    _audit(
        conn,
        staff_id,
        "add_load",
        "convoy_loads",
        str(load_id),
        after={
            "convoy_id": convoy_id,
            "vehicle_id": vehicle_id,
            "site_id": site_id,
            "item_id": item_id,
            "quantity": quantity,
            "weight_kg": weight,
        },
    )
    veh = next(v for v in assigned_vehicles(conn, convoy_id) if v["vehicle_id"] == vehicle_id)
    return {"load_id": load_id, "weight_kg": weight, "vehicle": veh}


def remove_load(conn: sqlite3.Connection, convoy_id: str, load_id: int, staff_id: str) -> None:
    _editable(conn, convoy_id)
    row = db.query_one("SELECT * FROM convoy_loads WHERE convoy_id = ? AND load_id = ?", (convoy_id, int(load_id)), conn)
    if row is None:
        raise PlanError(f"Load line {load_id} is not on {convoy_id}.")
    conn.execute("DELETE FROM convoy_loads WHERE load_id = ?", (int(load_id),))
    _sync_stop_vehicles(conn, convoy_id)
    _audit(conn, staff_id, "remove_load", "convoy_loads", str(load_id), before=row)


# -- suggestion and comparison -------------------------------------------------
def stop_details(conn: sqlite3.Connection, convoy_id: str) -> list[dict[str, Any]]:
    """Stops with position, area, current advisory and whether the area is blocked."""
    blocked = security.blocked_areas(conn)
    current = security.current_advisories(conn)
    rows = db.query_all(
        """SELECT s.site_id, t.name, t.area_id, a.name_en AS area_name, t.x_km, t.y_km, s.stop_seq, s.vehicle_id
           FROM convoy_stops s JOIN sites t ON t.site_id = s.site_id JOIN areas a ON a.area_id = t.area_id
           WHERE s.convoy_id = ? ORDER BY s.stop_seq, s.site_id""",
        (convoy_id,),
        conn,
    )
    for r in rows:
        level = current.get(r["area_id"], {}).get("final_level")
        r["advisory_level"] = level
        r["advisory"] = "No advisory" if level is None else f"{level} {security.LEVEL_NAMES[level]}"
        r["blocked"] = r["area_id"] in blocked
        r["block_reason"] = (
            f"{r['site_id']} {r['name']} is in {r['area_id']} {r['area_name']}, which has an active level {level} "
            f"{security.LEVEL_NAMES[level]} movement advisory (level {security.BLOCKING_LEVEL} or higher blocks convoy stops)"
            if r["blocked"]
            else ""
        )
    return rows


def plan_suggestion(conn: sqlite3.Connection, convoy_id: str) -> dict[str, Any]:
    """Suggested stop order and vehicle split, compared with the coordinator's manual order."""
    cv = get_convoy(conn, convoy_id)
    wh = db.row_dict(conn, "warehouses", "warehouse_id", cv["origin_warehouse_id"])
    assert wh is not None
    origin = (float(wh["x_km"]), float(wh["y_km"]))
    stops = stop_details(conn, convoy_id)
    pos = {s["site_id"]: (float(s["x_km"]), float(s["y_km"])) for s in stops}
    allowed = {s["site_id"]: pos[s["site_id"]] for s in stops if not s["blocked"]}
    excluded = [{"site_id": s["site_id"], "reason": s["block_reason"]} for s in stops if s["blocked"]]
    suggested = routing.nearest_neighbour_order(origin, allowed)
    manual = [s for s in manual_order(cv) if s in pos] or [s["site_id"] for s in stops]
    blocked_ids = {e["site_id"] for e in excluded}
    vehicles = assigned_vehicles(conn, convoy_id)
    usable = [(v["vehicle_id"], float(v["load_capacity_kg"])) for v in vehicles if v["readiness"] != "not_ready"]
    lines = [ln for ln in load_lines(conn, convoy_id) if ln["site_id"] in allowed]
    split: list[dict[str, Any]] = []
    split_note = ""
    if lines and usable:
        split = routing.split_loads(usable, lines, suggested)
    elif lines:
        split_note = "No ready vehicle is assigned, so no split can be suggested."
    caps = dict(usable)
    split_weights: dict[str, float] = {}
    for ln in split:
        split_weights[ln["vehicle_id"]] = split_weights.get(ln["vehicle_id"], 0.0) + ln["weight_kg"]
    manual_km = routing.route_distance(origin, manual, pos)
    suggested_km = routing.route_distance(origin, suggested, pos)
    return {
        "origin_id": wh["warehouse_id"],
        "origin": origin,
        "positions": pos,
        "stops": stops,
        "manual_order": manual,
        "manual_distance_km": manual_km,
        "manual_blocked": [s for s in manual if s in blocked_ids],
        "suggested_order": suggested,
        "suggested_distance_km": suggested_km,
        "saving_km": round(manual_km - suggested_km, 2),
        "excluded": excluded,
        "split": split,
        "split_note": split_note,
        "split_weights": {v: round(w, 2) for v, w in split_weights.items()},
        "split_overloaded": [v for v, w in split_weights.items() if routing.is_overloaded(w, caps[v])],
        "stop_vehicle": routing.primary_vehicle(split),
    }


def store_plan_fields(conn: sqlite3.Connection, convoy_id: str) -> dict[str, Any]:
    """Keep the stored orders and distances on convoys in step with the stops."""
    plan = plan_suggestion(conn, convoy_id)
    has_stops = bool(plan["stops"])
    conn.execute(
        "UPDATE convoys SET suggested_order_json = ?, manual_distance_km = ?, suggested_distance_km = ? WHERE convoy_id = ?",
        (
            json.dumps(plan["suggested_order"]) if has_stops else None,
            plan["manual_distance_km"] if has_stops else None,
            plan["suggested_distance_km"] if has_stops else None,
            convoy_id,
        ),
    )
    return plan


def apply_suggestion(conn: sqlite3.Connection, convoy_id: str, staff_id: str) -> dict[str, Any]:
    """Use the suggested stop order and vehicle split. Blocked stops must be removed first."""
    _editable(conn, convoy_id)
    plan = plan_suggestion(conn, convoy_id)
    if plan["excluded"]:
        raise PlanError(
            "Remove the stops in blocked areas first: " + "; ".join(e["reason"] for e in plan["excluded"]),
            [e["reason"] for e in plan["excluded"]],
        )
    if plan["split_note"]:
        raise PlanError(plan["split_note"])
    before = {"stop_order": stop_ids(conn, convoy_id), "loads": load_lines(conn, convoy_id)}
    if plan["split"]:
        conn.execute("DELETE FROM convoy_loads WHERE convoy_id = ?", (convoy_id,))
        for ln in plan["split"]:
            conn.execute(
                "INSERT INTO convoy_loads(convoy_id, vehicle_id, site_id, item_id, quantity, weight_kg) VALUES (?,?,?,?,?,?)",
                (convoy_id, ln["vehicle_id"], ln["site_id"], ln["item_id"], int(ln["quantity"]), ln["weight_kg"]),
            )
    for seq, site in enumerate(plan["suggested_order"], start=1):
        conn.execute(
            "UPDATE convoy_stops SET stop_seq = ?, vehicle_id = ? WHERE convoy_id = ? AND site_id = ?",
            (seq, plan["stop_vehicle"].get(site), convoy_id, site),
        )
    store_plan_fields(conn, convoy_id)
    _audit(
        conn,
        staff_id,
        "apply_suggestion",
        "convoys",
        convoy_id,
        before=before,
        after={"stop_order": plan["suggested_order"], "split": plan["split"], "distance_km": plan["suggested_distance_km"]},
        reason="Suggested order (nearest-neighbour) and first-fit-decreasing vehicle split",
    )
    return plan


# -- checklist ------------------------------------------------------------------
def generate_checklist(conn: sqlite3.Connection, convoy_id: str, staff_id: str) -> int:
    """Create any missing pre-departure checklist rows. Returns the number created."""
    get_convoy(conn, convoy_id)
    created = 0
    for key, label in CHECKLIST:
        cur = conn.execute(
            "INSERT OR IGNORE INTO convoy_checklist(convoy_id, item_key, label, checked) VALUES (?,?,?,0)", (convoy_id, key, label)
        )
        created += cur.rowcount
    if created:
        _audit(conn, staff_id, "generate_checklist", "convoy_checklist", convoy_id, after={"items_created": created})
    return created


def checklist(conn: sqlite3.Connection, convoy_id: str) -> list[dict[str, Any]]:
    order = {k: i for i, (k, _) in enumerate(CHECKLIST)}
    rows = db.query_all("SELECT * FROM convoy_checklist WHERE convoy_id = ?", (convoy_id,), conn)
    return sorted(rows, key=lambda r: (order.get(r["item_key"], 99), r["item_key"]))


def set_check(conn: sqlite3.Connection, convoy_id: str, item_key: str, checked: bool, staff_id: str) -> None:
    cv = get_convoy(conn, convoy_id)
    if cv["status"] not in ("draft", "planned", "approved"):
        raise PlanError(f"{convoy_id} is {cv['status']}; the checklist is closed.")
    row = db.query_one("SELECT * FROM convoy_checklist WHERE convoy_id = ? AND item_key = ?", (convoy_id, item_key), conn)
    if row is None:
        raise PlanError(f"No checklist item {item_key} on {convoy_id}.")
    if db.staff_role(conn, staff_id) == "system":
        raise PlanError(f"Unknown staff ID {staff_id}.")
    when = clock.iso() if checked else None
    conn.execute(
        "UPDATE convoy_checklist SET checked = ?, checked_by = ?, checked_at = ? WHERE convoy_id = ? AND item_key = ?",
        (1 if checked else 0, staff_id if checked else None, when, convoy_id, item_key),
    )
    _audit(
        conn,
        staff_id,
        "tick" if checked else "untick",
        "convoy_checklist",
        f"{convoy_id}/{item_key}",
        before={"checked": row["checked"], "checked_by": row["checked_by"]},
        after={"checked": int(checked), "checked_by": staff_id if checked else None, "checked_at": when},
    )


# -- blockers, approval and status ------------------------------------------------
def blockers(conn: sqlite3.Connection, convoy_id: str, on: date | str | None = None) -> list[str]:
    """Every reason the convoy cannot be approved or dispatched (empty list = clear).

    Readiness and driver licences are judged on `on` (default: the planned date).
    """
    cv = get_convoy(conn, convoy_id)
    when = _date(on or cv["planned_date"])
    out: list[str] = []
    vehicles = assigned_vehicles(conn, convoy_id, when)
    stops = stop_details(conn, convoy_id)
    lines = load_lines(conn, convoy_id)
    if not vehicles:
        out.append("No vehicles assigned")
    if not stops:
        out.append("No destination stops")
    if not lines:
        out.append("No load lines")
    for v in vehicles:
        if v["readiness"] == "not_ready":
            out.append(not_ready_message(v["vehicle_id"], when, v["reasons"]))
        if v["overloaded"]:
            over = round(v["load_kg"] - float(v["load_capacity_kg"]), 2)
            out.append(
                f"{v['vehicle_id']} is overloaded: {v['load_kg']:g} kg loaded, capacity {float(v['load_capacity_kg']):g} kg ({over:g} kg over)"
            )
        if v["driver_problems"]:
            out.append(f"{v['driver_id']} cannot drive {v['vehicle_id']}: " + "; ".join(v["driver_problems"]))
    for s in stops:
        if s["blocked"]:
            out.append("Stop in a blocked area: " + s["block_reason"])
    loaded_sites = {ln["site_id"] for ln in lines}
    for s in stops:
        if s["site_id"] not in loaded_sites:
            out.append(f"Stop {s['site_id']} has no load")
    out.extend(shortage_messages(conn, convoy_id))
    items = checklist(conn, convoy_id)
    if not items:
        out.append("Pre-departure checklist not generated")
    else:
        open_items = [r["item_key"] for r in items if not r["checked"]]
        if open_items:
            out.append(f"Pre-departure checklist incomplete: {len(open_items)} of {len(items)} items not ticked ({', '.join(open_items)})")
    return out


def _set_status(conn: sqlite3.Connection, convoy_id: str, new: str, staff_id: str, note: str | None = None) -> dict[str, Any]:
    cv = get_convoy(conn, convoy_id)
    if cv["status"] not in TRANSITIONS[new]:
        raise PlanError(f"{convoy_id} is {cv['status']}; it can only become {new} from {' or '.join(TRANSITIONS[new])}.")
    when = clock.iso()
    sets = ["status = ?"]
    params: list[Any] = [new]
    if new == "approved":
        sets += ["approved_by = ?", "approved_at = ?"]
        params += [staff_id, when]
    if new in TIMESTAMP_COLUMN:
        sets.append(f"{TIMESTAMP_COLUMN[new]} = ?")
        params.append(when)
    conn.execute(f"UPDATE convoys SET {', '.join(sets)} WHERE convoy_id = ?", (*params, convoy_id))
    conn.execute(
        "INSERT INTO convoy_events(convoy_id, at, status, staff_id, note) VALUES (?,?,?,?,?)", (convoy_id, when, new, staff_id, note)
    )
    action = {"approved": "approve", "departed": "dispatch", "cancelled": "cancel"}.get(new, "update")
    _audit(conn, staff_id, action, "convoys", convoy_id, before={"status": cv["status"]}, after={"status": new, "at": when}, reason=note)
    return get_convoy(conn, convoy_id)


def mark_planned(conn: sqlite3.Connection, convoy_id: str, staff_id: str) -> None:
    _set_status(conn, convoy_id, "planned", staff_id, "Convoy planned")


def approve(conn: sqlite3.Connection, convoy_id: str, confirmed_by: str | None) -> dict[str, Any]:
    """Coordinator approval. Refused, with every reason listed, while anything blocks the convoy."""
    staff = confirm.require_confirmation(conn, confirmed_by, APPROVER_ROLES)
    cv = get_convoy(conn, convoy_id)
    if cv["status"] not in TRANSITIONS["approved"]:
        raise PlanError(f"{convoy_id} is {cv['status']}; only a draft or planned convoy can be approved.")
    found = blockers(conn, convoy_id, cv["planned_date"])
    if found:
        raise ConvoyBlocked(f"Approval of {convoy_id} refused: " + " | ".join(found), found)
    store_plan_fields(conn, convoy_id)
    return _set_status(conn, convoy_id, "approved", staff, "Approved: no blockers, checklist complete")


def depart(conn: sqlite3.Connection, convoy_id: str, confirmed_by: str | None) -> list[str]:
    """Dispatch: only from approved. Re-checks every blocker today, then records the stock leaving
    the origin warehouse, one dispatch and one ledger row per load line. Returns the dispatch IDs."""
    staff = confirm.require_confirmation(conn, confirmed_by, APPROVER_ROLES)
    cv = get_convoy(conn, convoy_id)
    if cv["status"] != "approved":
        raise PlanError(f"{convoy_id} is {cv['status']}; only an approved convoy can depart.")
    found = blockers(conn, convoy_id, clock.today())
    if found:
        raise ConvoyBlocked(f"Departure of {convoy_id} refused: " + " | ".join(found), found)
    when = clock.iso()
    dispatch_ids = [
        stock.record_dispatch(
            conn,
            cv["origin_warehouse_id"],
            ln["site_id"],
            ln["item_id"],
            int(ln["quantity"]),
            staff,
            convoy_id=convoy_id,
            at=when,
            channel=CHANNEL,
        )
        for ln in load_lines(conn, convoy_id)
    ]
    _set_status(conn, convoy_id, "departed", staff, f"Departed with {len(dispatch_ids)} dispatch(es): {', '.join(dispatch_ids)}")
    return dispatch_ids


def mark_arrived(conn: sqlite3.Connection, convoy_id: str, staff_id: str, note: str | None = None) -> None:
    _set_status(conn, convoy_id, "arrived", staff_id, note)


def mark_returned(conn: sqlite3.Connection, convoy_id: str, staff_id: str, note: str | None = None) -> None:
    _set_status(conn, convoy_id, "returned", staff_id, note)


def cancel(conn: sqlite3.Connection, convoy_id: str, confirmed_by: str | None, reason: str) -> None:
    staff = confirm.require_confirmation(conn, confirmed_by, APPROVER_ROLES)
    if not reason or not reason.strip():
        raise PlanError("Cancelling needs a reason.")
    _set_status(conn, convoy_id, "cancelled", staff, reason.strip())


def events(conn: sqlite3.Connection, convoy_id: str) -> list[dict[str, Any]]:
    return db.query_all(
        "SELECT at, status, staff_id, note FROM convoy_events WHERE convoy_id = ? ORDER BY at, event_id", (convoy_id,), conn
    )
