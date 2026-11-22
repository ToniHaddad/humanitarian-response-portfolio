"""Position simulator (Project 20; its reports are read by 14 and 17).

Each step writes one position report per simulated vehicle (vehicles.simulated = 1)
through shared.fleet.record_position(..., source="simulator"):

  * a vehicle on a convoy in progress (status 'departed') drives toward its next
    unreached stop at SIM_SPEED_KMH for one simulator interval;
  * any other vehicle drifts a little from its last position, staying within
    DRIFT_RADIUS_KM of its home warehouse.

Positions stay inside the invented 0-100 km grid. Vehicles with simulated = 0
(VEH-07 in the seed) are never moved, so they go stale. When the switch
(setting position_simulator_enabled) is off, a step writes nothing.
"""

from __future__ import annotations

import math
import random
import sqlite3
from datetime import datetime
from typing import Any

from shared import clock, db, fleet

SETTING_ENABLED = "position_simulator_enabled"
SETTING_INTERVAL = "simulator_interval_seconds"
SIM_SPEED_KMH = 40.0
DRIFT_STEP_KM = 0.2
DRIFT_RADIUS_KM = 0.5
STOP_REACHED_KM = 0.5


def is_enabled(conn: sqlite3.Connection) -> bool:
    return db.get_setting(SETTING_ENABLED, "0", conn) == "1"


def interval_seconds(conn: sqlite3.Connection) -> float:
    return db.get_setting_float(SETTING_INTERVAL, 60.0, conn)


def set_enabled(conn: sqlite3.Connection, enabled: bool, staff_id: str, channel: str = "app:20") -> None:
    """Switch the simulator on or off (audited through db.set_setting)."""
    from shared.alerts import set_setting_audited

    set_setting_audited(conn, SETTING_ENABLED, "1" if enabled else "0", staff_id, channel)


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _convoy_target(conn: sqlite3.Connection, vehicle_id: str) -> tuple[str, tuple[float, float] | None] | None:
    """(convoy_id, next unreached stop) for a vehicle on a departed convoy, else None.

    A stop counts as reached when any report of this vehicle on the convoy came
    within STOP_REACHED_KM of it, after the previous stop was reached.
    """
    convoy = conn.execute(
        """SELECT c.convoy_id, c.departed_at FROM convoys c JOIN convoy_vehicles cv USING (convoy_id)
           WHERE cv.vehicle_id = ? AND c.status = 'departed' ORDER BY c.departed_at DESC LIMIT 1""",
        (vehicle_id,),
    ).fetchone()
    if convoy is None:
        return None
    stops = conn.execute(
        """SELECT s.x_km, s.y_km FROM convoy_stops cs JOIN sites s USING (site_id)
           WHERE cs.convoy_id = ? AND (cs.vehicle_id = ? OR cs.vehicle_id IS NULL) ORDER BY cs.stop_seq""",
        (convoy["convoy_id"], vehicle_id),
    ).fetchall()
    reports = conn.execute(
        "SELECT x_km, y_km FROM position_reports WHERE vehicle_id = ? AND convoy_id = ? ORDER BY reported_at",
        (vehicle_id, convoy["convoy_id"]),
    ).fetchall()
    idx = 0
    for rep in reports:
        if idx < len(stops) and _dist((rep[0], rep[1]), (stops[idx][0], stops[idx][1])) <= STOP_REACHED_KM:
            idx += 1
    target = (float(stops[idx][0]), float(stops[idx][1])) if idx < len(stops) else None
    return convoy["convoy_id"], target


def next_position(
    current: tuple[float, float],
    home: tuple[float, float],
    target: tuple[float, float] | None,
    step_km: float,
    rng: random.Random,
) -> tuple[float, float, float]:
    """(x, y, speed_kmh) after one step. Toward target if given, else a small drift near home."""
    if target is not None:
        d = _dist(current, target)
        if d <= step_km:
            return _clamp(target[0]), _clamp(target[1]), 0.0
        f = step_km / d
        return _clamp(current[0] + (target[0] - current[0]) * f), _clamp(current[1] + (target[1] - current[1]) * f), SIM_SPEED_KMH
    x = current[0] + rng.uniform(-DRIFT_STEP_KM, DRIFT_STEP_KM)
    y = current[1] + rng.uniform(-DRIFT_STEP_KM, DRIFT_STEP_KM)
    if _dist((x, y), home) > DRIFT_RADIUS_KM:  # pull back toward home
        x = home[0] + (x - home[0]) * 0.5
        y = home[1] + (y - home[1]) * 0.5
        if _dist((x, y), home) > DRIFT_RADIUS_KM:
            x, y = home[0] + rng.uniform(-0.2, 0.2), home[1] + rng.uniform(-0.2, 0.2)
    return _clamp(x), _clamp(y), 0.0


def vehicle_positions(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict[str, Any]]:
    """Last position per vehicle with minutes since it and the stale flag (shared.fleet.is_stale)."""
    limit = fleet.stale_limit_minutes(conn)
    now = now or clock.now()
    rows = db.query_all(
        """SELECT v.vehicle_id, v.simulated, p.convoy_id, p.reported_at, p.x_km, p.y_km, p.speed_kmh, p.source
           FROM vehicles v LEFT JOIN v_last_position p ON p.vehicle_id = v.vehicle_id
           GROUP BY v.vehicle_id ORDER BY v.vehicle_id""",
        conn=conn,
    )
    for r in rows:
        r["simulated"] = bool(r["simulated"])
        last = r["reported_at"]
        r["minutes_since_report"] = round((now - datetime.fromisoformat(last)).total_seconds() / 60.0, 1) if last else None
        r["stale"] = fleet.is_stale(last, now=now, limit_minutes=limit)
    return rows


def step(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """Write one simulated report per simulated vehicle. Returns the number written (0 when switched off)."""
    if not is_enabled(conn):
        return 0
    now = now or clock.now()
    at = clock.iso(now)
    step_km = SIM_SPEED_KMH * interval_seconds(conn) / 3600.0
    vehicles: list[dict[str, Any]] = db.query_all(
        """SELECT v.vehicle_id, w.x_km AS home_x, w.y_km AS home_y, p.x_km AS last_x, p.y_km AS last_y
           FROM vehicles v JOIN warehouses w ON w.warehouse_id = v.home_warehouse_id
           LEFT JOIN v_last_position p ON p.vehicle_id = v.vehicle_id
           WHERE v.simulated = 1 GROUP BY v.vehicle_id ORDER BY v.vehicle_id""",
        conn=conn,
    )
    written = 0
    for v in vehicles:
        home = (float(v["home_x"]), float(v["home_y"]))
        current = (float(v["last_x"]), float(v["last_y"])) if v["last_x"] is not None else home
        convoy = _convoy_target(conn, v["vehicle_id"])
        convoy_id, target = convoy if convoy else (None, None)
        rng = random.Random(f"{v['vehicle_id']}|{at}")
        if convoy_id and target is None:  # all stops reached: wait near the current spot
            home = current
        x, y, speed = next_position(current, home, target, step_km, rng)
        fleet.record_position(conn, v["vehicle_id"], x, y, source="simulator", convoy_id=convoy_id, speed_kmh=speed, at=at)
        written += 1
    return written
