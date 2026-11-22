"""Project 14: fleet readiness and tracking.

Readiness comes only from shared.fleet (assess_vehicle / fleet_readiness). Positions are
read from position_reports, which the Project 20 API and simulator write through
shared.fleet.record_position. This module adds the fleet table, the position table with
the stale flag, the trip log, and a few small audited updates.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from shared import alerts, clock, db, fleet
from shared.confirm import require_confirmation

CHANNEL = "app:14"
SEVERITIES = ("minor", "major", "grounding")
UPDATE_ROLES = ("coordinator", "duty_officer", "warehouse_officer")
LIMIT_ROLES = ("coordinator",)  # admin is always allowed by require_confirmation
STATUS_LABELS = {"ready": "Ready", "due_for_service": "Due for service", "not_ready": "Not ready"}


# -- fleet table and readiness --------------------------------------------
def open_defects(conn: sqlite3.Connection, vehicle_id: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM vehicle_defects WHERE resolved_at IS NULL"
    params: list[Any] = []
    if vehicle_id:
        sql += " AND vehicle_id = ?"
        params.append(vehicle_id)
    return db.query_all(sql + " ORDER BY defect_id", params, conn=conn)


def fleet_table(conn: sqlite3.Connection, on: date | None = None) -> pd.DataFrame:
    """One row per vehicle: specification, service schedule, expiry dates, open defects and readiness on `on`."""
    on = on or clock.today()
    readiness = fleet.fleet_readiness(conn, on)
    defects = open_defects(conn)
    rows = []
    for v in db.query_all("SELECT * FROM vehicles ORDER BY vehicle_id", conn=conn):
        r = readiness[v["vehicle_id"]]
        mine = [d for d in defects if d["vehicle_id"] == v["vehicle_id"]]
        due_date = date.fromisoformat(v["last_service_date"]) + timedelta(days=int(v["service_interval_days"]))
        rows.append(
            {
                "Vehicle": v["vehicle_id"],
                "Status": STATUS_LABELS[r.status],
                "Why": " | ".join(r.reasons + r.warnings) or "All checks pass",
                "Type": v["vehicle_type"],
                "Load capacity (kg)": v["load_capacity_kg"],
                "Odometer (km)": v["odometer_km"],
                "Last service": v["last_service_date"],
                "Odometer at last service (km)": v["last_service_odometer"],
                "Next service by date": due_date.isoformat(),
                "Next service by distance (km)": int(v["last_service_odometer"]) + int(v["service_interval_km"]),
                "Registration expires": v["registration_expiry"],
                "Insurance expires": v["insurance_expiry"],
                "Permit expires": v["permit_expiry"],
                "Open defects": "; ".join(f"{d['defect_id']} ({d['severity']})" for d in mine) or "none",
            }
        )
    return pd.DataFrame(rows)


def explain(conn: sqlite3.Connection, vehicle_id: str, on: date | None = None) -> fleet.Readiness:
    return fleet.fleet_readiness(conn, on or clock.today())[vehicle_id]


# -- positions and staleness ----------------------------------------------
def position_table(conn: sqlite3.Connection, now: datetime | None = None, limit_minutes: float | None = None) -> pd.DataFrame:
    """Last known position per vehicle, minutes since the report, and the STALE flag (a vehicle with no report is stale)."""
    now = now or clock.now()
    limit = fleet.stale_limit_minutes(conn) if limit_minutes is None else limit_minutes
    rows = []
    for v in db.query_all("SELECT vehicle_id, simulated FROM vehicles ORDER BY vehicle_id", conn=conn):
        last = db.query_one(
            "SELECT * FROM position_reports WHERE vehicle_id = ? ORDER BY reported_at DESC, report_id DESC LIMIT 1",
            (v["vehicle_id"],),
            conn=conn,
        )
        at = last["reported_at"] if last else None
        minutes = round((now - datetime.fromisoformat(at)).total_seconds() / 60.0, 1) if at else None
        rows.append(
            {
                "vehicle_id": v["vehicle_id"],
                "last_report_at": at,
                "minutes_since_report": minutes,
                "x_km": last["x_km"] if last else None,
                "y_km": last["y_km"] if last else None,
                "speed_kmh": last["speed_kmh"] if last else None,
                "convoy_id": last["convoy_id"] if last else None,
                "source": last["source"] if last else None,
                "tracker_simulated": bool(v["simulated"]),
                "stale": fleet.is_stale(at, now, limit),
                "limit_minutes": limit,
            }
        )
    return pd.DataFrame(rows)


# -- trip log --------------------------------------------------------------
def convoy_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return db.query_all("SELECT convoy_id, name, status, planned_date FROM convoys ORDER BY planned_date DESC, convoy_id DESC", conn=conn)


def trip_log(conn: sqlite3.Connection, convoy_id: str) -> dict[str, Any]:
    """Status events, vehicles with drivers, and the position reports that carry the convoy id, oldest first."""
    convoy = db.query_one("SELECT * FROM convoys WHERE convoy_id = ?", (convoy_id,), conn=conn)
    if convoy is None:
        raise ValueError(f"Unknown convoy {convoy_id}")
    return {
        "convoy": convoy,
        "events": db.query_df(
            "SELECT at, status, staff_id, note FROM convoy_events WHERE convoy_id = ? ORDER BY at, event_id", (convoy_id,), conn=conn
        ),
        "vehicles": db.query_df(
            "SELECT cv.vehicle_id, v.vehicle_type, cv.driver_id, d.licence_class FROM convoy_vehicles cv "
            "JOIN vehicles v USING (vehicle_id) JOIN drivers d USING (driver_id) WHERE cv.convoy_id = ? ORDER BY cv.vehicle_id",
            (convoy_id,),
            conn=conn,
        ),
        "reports": db.query_df(
            "SELECT report_id, reported_at, vehicle_id, x_km, y_km, speed_kmh, source FROM position_reports "
            "WHERE convoy_id = ? ORDER BY reported_at, report_id",
            (convoy_id,),
            conn=conn,
        ),
    }


# -- small audited updates (each needs staff confirmation) -----------------
def _vehicle(conn: sqlite3.Connection, vehicle_id: str) -> dict[str, Any]:
    v = db.row_dict(conn, "vehicles", "vehicle_id", vehicle_id)
    if v is None:
        raise ValueError(f"Unknown vehicle {vehicle_id}")
    return v


def report_defect(conn: sqlite3.Connection, vehicle_id: str, severity: str, description: str, confirmed_by: str | None) -> str:
    staff = require_confirmation(conn, confirmed_by, UPDATE_ROLES)
    if severity not in SEVERITIES:
        raise ValueError(f"Severity must be one of {', '.join(SEVERITIES)}")
    if not description.strip():
        raise ValueError("Describe the defect")
    _vehicle(conn, vehicle_id)
    defect_id = db.next_id(conn, "vehicle_defects", "defect_id", "DEF-", 3)
    conn.execute(
        "INSERT INTO vehicle_defects(defect_id, vehicle_id, reported_at, reported_by, description, severity) VALUES (?,?,?,?,?,?)",
        (defect_id, vehicle_id, clock.iso(), staff, description.strip(), severity),
    )
    db.audit(
        conn,
        actor_id=staff,
        channel=CHANNEL,
        action="report_defect",
        entity_table="vehicle_defects",
        entity_id=defect_id,
        after=db.row_dict(conn, "vehicle_defects", "defect_id", defect_id),
    )
    return defect_id


def resolve_defect(conn: sqlite3.Connection, defect_id: str, confirmed_by: str | None, reason: str | None = None) -> None:
    staff = require_confirmation(conn, confirmed_by, UPDATE_ROLES)
    before = db.row_dict(conn, "vehicle_defects", "defect_id", defect_id)
    if before is None:
        raise ValueError(f"Unknown defect {defect_id}")
    if before["resolved_at"]:
        raise ValueError(f"Defect {defect_id} is already resolved")
    conn.execute("UPDATE vehicle_defects SET resolved_at = ? WHERE defect_id = ?", (clock.iso(), defect_id))
    db.audit(
        conn,
        actor_id=staff,
        channel=CHANNEL,
        action="resolve_defect",
        entity_table="vehicle_defects",
        entity_id=defect_id,
        before=before,
        after=db.row_dict(conn, "vehicle_defects", "defect_id", defect_id),
        reason=reason,
    )


def record_service(
    conn: sqlite3.Connection,
    vehicle_id: str,
    confirmed_by: str | None,
    service_date: date | None = None,
    odometer_km: int | None = None,
) -> None:
    """Set the last service date and odometer. The odometer reading defaults to the current one and may not go backwards."""
    staff = require_confirmation(conn, confirmed_by, UPDATE_ROLES)
    before = _vehicle(conn, vehicle_id)
    odo = int(before["odometer_km"] if odometer_km is None else odometer_km)
    if odo < int(before["odometer_km"]):
        raise ValueError("The odometer cannot go below its current reading")
    when = (service_date or clock.today()).isoformat()
    conn.execute(
        "UPDATE vehicles SET last_service_date = ?, last_service_odometer = ?, odometer_km = ? WHERE vehicle_id = ?",
        (when, odo, odo, vehicle_id),
    )
    db.audit(
        conn,
        actor_id=staff,
        channel=CHANNEL,
        action="record_service",
        entity_table="vehicles",
        entity_id=vehicle_id,
        before={k: before[k] for k in ("odometer_km", "last_service_date", "last_service_odometer")},
        after={"odometer_km": odo, "last_service_date": when, "last_service_odometer": odo},
    )


def set_stale_limit(conn: sqlite3.Connection, minutes: float, confirmed_by: str | None) -> None:
    """Change the shared stale_position_minutes setting (also used by Projects 17 and 20)."""
    staff = require_confirmation(conn, confirmed_by, LIMIT_ROLES)
    alerts.set_stale_limit(conn, float(minutes), staff, channel=CHANNEL)
