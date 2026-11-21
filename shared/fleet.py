"""Vehicle readiness, position reports and staleness (Projects 14, 16, 17, 20).

Readiness rule: a vehicle is NOT READY on a date if any of these is true:
  * service overdue: date > last_service_date + service_interval_days, or
    odometer_km >= last_service_odometer + service_interval_km
  * registration, insurance or permit expired (expiry date before the date)
  * an unresolved defect of severity 'grounding'
It is DUE FOR SERVICE (still ready) when the service falls due within 14 days or
500 km. An unresolved 'major' defect is shown as a warning but does not block.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from shared import clock, db

SERVICE_DUE_SOON_DAYS = 14
SERVICE_DUE_SOON_KM = 500
DEFAULT_STALE_MINUTES = 15.0


@dataclass
class Readiness:
    vehicle_id: str
    status: str  # 'ready', 'due_for_service', 'not_ready'
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.status != "not_ready"


def _d(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def assess_vehicle(vehicle: dict[str, Any], defects: list[dict[str, Any]], on: date) -> Readiness:
    reasons: list[str] = []
    warnings: list[str] = []
    service_due_date = _d(vehicle["last_service_date"]) + timedelta(days=int(vehicle["service_interval_days"]))
    service_due_km = int(vehicle["last_service_odometer"]) + int(vehicle["service_interval_km"])
    odometer = int(vehicle["odometer_km"])
    if on > service_due_date:
        reasons.append(f"Service overdue since {service_due_date.isoformat()} (every {vehicle['service_interval_days']} days)")
    if odometer >= service_due_km:
        reasons.append(f"Service overdue by distance: {odometer} km on the odometer, service was due at {service_due_km} km")
    for label, col in (("Registration", "registration_expiry"), ("Insurance", "insurance_expiry"), ("Permit", "permit_expiry")):
        if _d(vehicle[col]) < on:
            reasons.append(f"{label} expired on {_d(vehicle[col]).isoformat()}")
    for d in defects:
        if d.get("resolved_at"):
            continue
        if d["severity"] == "grounding":
            reasons.append(f"Grounding defect {d['defect_id']}: {d['description']}")
        elif d["severity"] == "major":
            warnings.append(f"Major defect {d['defect_id']}: {d['description']}")
    if reasons:
        return Readiness(vehicle["vehicle_id"], "not_ready", reasons, warnings)
    due_soon = (service_due_date - on).days <= SERVICE_DUE_SOON_DAYS or service_due_km - odometer <= SERVICE_DUE_SOON_KM
    if due_soon:
        warnings.insert(0, f"Service due by {service_due_date.isoformat()} or {service_due_km} km")
        return Readiness(vehicle["vehicle_id"], "due_for_service", reasons, warnings)
    return Readiness(vehicle["vehicle_id"], "ready", reasons, warnings)


def fleet_readiness(conn: sqlite3.Connection, on: date | None = None) -> dict[str, Readiness]:
    on = on or clock.today()
    vehicles = db.query_all("SELECT * FROM vehicles ORDER BY vehicle_id", conn=conn)
    defects = db.query_all("SELECT * FROM vehicle_defects", conn=conn)
    return {v["vehicle_id"]: assess_vehicle(v, [d for d in defects if d["vehicle_id"] == v["vehicle_id"]], on) for v in vehicles}


def stale_limit_minutes(conn: sqlite3.Connection) -> float:
    return db.get_setting_float("stale_position_minutes", DEFAULT_STALE_MINUTES, conn)


def is_stale(last_report_at: datetime | str | None, now: datetime | None = None, limit_minutes: float = DEFAULT_STALE_MINUTES) -> bool:
    """A position is stale when it is older than the limit. Exactly at the limit is not stale."""
    if last_report_at is None:
        return True
    last = last_report_at if isinstance(last_report_at, datetime) else datetime.fromisoformat(str(last_report_at))
    now = now or clock.now()
    return (now - last) > timedelta(minutes=limit_minutes)


def record_position(
    conn: sqlite3.Connection,
    vehicle_id: str,
    x_km: float,
    y_km: float,
    source: str = "api",
    convoy_id: str | None = None,
    speed_kmh: float | None = None,
    at: str | None = None,
) -> int:
    """Store one position report. Used by the API and the simulator alike."""
    if not (0.0 <= x_km <= 100.0 and 0.0 <= y_km <= 100.0):
        raise ValueError("Position must be inside the invented 100 x 100 km grid")
    if conn.execute("SELECT 1 FROM vehicles WHERE vehicle_id = ?", (vehicle_id,)).fetchone() is None:
        raise ValueError(f"Unknown vehicle {vehicle_id}")
    cur = conn.execute(
        "INSERT INTO position_reports(vehicle_id, convoy_id, reported_at, x_km, y_km, speed_kmh, source) VALUES (?,?,?,?,?,?,?)",
        (vehicle_id, convoy_id, at or clock.iso(), round(x_km, 2), round(y_km, 2), speed_kmh, source),
    )
    return int(cur.lastrowid)
