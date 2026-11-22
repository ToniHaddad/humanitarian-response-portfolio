"""Operations room live board: read-only data for every panel (Project 17).

Nothing here writes. `open_readonly()` opens the shared database with
`PRAGMA query_only = ON`, so even a coding mistake cannot change a row.

Staleness rules (not redefined here):
  * vehicle position: `shared.fleet.is_stale` with `shared.fleet.stale_limit_minutes`
  * panel badge: `shared.ui.freshness_badge`
A panel's "last updated" is the newest timestamp in that panel's own source
data. It is stale when that timestamp is older than the panel's limit, using the
same `is_stale` rule (exactly at the limit is not stale).
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from shared import alerts, clock, db, fleet, security

# Limits in minutes for panels whose source is edited by people, not by a feed.
# The position panel uses the configured setting `stale_position_minutes`.
PANEL_LIMIT_MINUTES: dict[str, float] = {
    "convoys": 1440.0,
    "requests": 240.0,
    "advisories": 1440.0,
    "stock": 1440.0,
    "distributions": 240.0,
}
PANEL_LIMIT_SETTING = "board_panel_limit_{panel}_minutes"  # optional settings row that overrides a constant above
DEFAULT_REFRESH_SECONDS = 30
MIN_REFRESH_SECONDS = 5


@dataclass(frozen=True)
class Freshness:
    """When a panel's source last reported, and whether that is past its limit."""

    newest: str | None
    limit_minutes: float
    stale: bool


def open_readonly() -> sqlite3.Connection:
    conn = db.connect()
    conn.execute("PRAGMA query_only = ON")
    return conn


def freshness(newest: str | None, limit_minutes: float, now: datetime | None = None) -> Freshness:
    """Stale when the newest source timestamp is missing or older than the limit."""
    return Freshness(newest, limit_minutes, fleet.is_stale(newest, now or clock.now(), limit_minutes))


def refresh_seconds(conn: sqlite3.Connection) -> int:
    value = int(db.get_setting_float("board_refresh_seconds", DEFAULT_REFRESH_SECONDS, conn))
    return max(MIN_REFRESH_SECONDS, value)


def panel_limit(conn: sqlite3.Connection, panel: str) -> float:
    """Staleness limit in minutes for a panel: the `board_panel_limit_<panel>_minutes` setting when present and positive, else the constant."""
    value = db.get_setting_float(PANEL_LIMIT_SETTING.format(panel=panel), PANEL_LIMIT_MINUTES[panel], conn)
    return value if value > 0 else PANEL_LIMIT_MINUTES[panel]


def label_free_areas(areas: pd.DataFrame, positions: pd.DataFrame, min_km: float = 6.0) -> pd.DataFrame:
    """Area names to draw on the map: drops a name when a vehicle sits within `min_km` of its centre, so vehicle labels stay readable."""
    if areas.empty or positions.empty:
        return areas
    pts = positions.dropna(subset=["x_km", "y_km"])
    keep = [
        not any(math.hypot(a.x_km - v.x_km, a.y_km - v.y_km) < min_km for v in pts.itertuples()) for a in areas.itertuples()
    ]
    return areas[keep]


def _newest(conn: sqlite3.Connection, *selects: str) -> str | None:
    """Newest timestamp over several `SELECT MAX(col) FROM table` queries."""
    values = [conn.execute(s).fetchone()[0] for s in selects]
    values = [v for v in values if v]
    return max(values) if values else None


# -- vehicles and the map ----------------------------------------------------
def vehicle_positions(conn: sqlite3.Connection, now: datetime | None = None) -> pd.DataFrame:
    """Last position of every vehicle with its state: Convoy, Idle or STALE."""
    now = now or clock.now()
    limit = fleet.stale_limit_minutes(conn)
    rows = db.query_all(
        """SELECT v.vehicle_id, p.convoy_id, p.reported_at, p.x_km, p.y_km, p.speed_kmh
           FROM vehicles v LEFT JOIN v_last_position p ON p.vehicle_id = v.vehicle_id
           ORDER BY v.vehicle_id""",
        conn=conn,
    )
    departed = {r[0] for r in conn.execute("SELECT convoy_id FROM convoys WHERE status = 'departed'")}
    out = []
    for r in rows:
        stale = fleet.is_stale(r["reported_at"], now, limit)
        age = ui_minutes(r["reported_at"], now)
        if stale:
            state = "STALE"
        elif r["convoy_id"] in departed:
            state = "On convoy"
        else:
            state = "Idle"
        out.append({**r, "state": state, "stale": stale, "age_min": age})
    return pd.DataFrame(out, columns=["vehicle_id", "convoy_id", "reported_at", "x_km", "y_km", "speed_kmh", "state", "stale", "age_min"])


def ui_minutes(timestamp: str | None, now: datetime) -> float | None:
    if timestamp is None:
        return None
    return round((now - clock.parse(timestamp)).total_seconds() / 60.0, 1)


def position_freshness(conn: sqlite3.Connection, now: datetime | None = None) -> Freshness:
    """Newest report of any vehicle: the position feed as a whole has stopped when this is stale."""
    return freshness(conn.execute("SELECT MAX(reported_at) FROM v_last_position").fetchone()[0], fleet.stale_limit_minutes(conn), now)


def areas_frame(conn: sqlite3.Connection) -> pd.DataFrame:
    return db.query_df("SELECT name_en, x_km, y_km FROM areas", conn=conn)


# -- convoys -----------------------------------------------------------------
def convoy_rows(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict[str, Any]]:
    """Convoys that are active today or coming up: departed, approved, planned (newest first)."""
    now = now or clock.now()
    limit = fleet.stale_limit_minutes(conn)
    rows = db.query_all(
        """SELECT c.convoy_id, c.name, c.status, c.planned_date, c.departed_at,
                  (SELECT COUNT(*) FROM convoy_vehicles cv WHERE cv.convoy_id = c.convoy_id) AS vehicles,
                  (SELECT COUNT(*) FROM convoy_stops cs WHERE cs.convoy_id = c.convoy_id) AS stops
           FROM convoys c WHERE c.status IN ('departed','approved','planned')
           ORDER BY CASE c.status WHEN 'departed' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, c.planned_date, c.convoy_id""",
        conn=conn,
    )
    for r in rows:
        last = conn.execute(
            """SELECT MAX(p.reported_at) FROM position_reports p
               JOIN convoy_vehicles cv ON cv.vehicle_id = p.vehicle_id AND cv.convoy_id = ?""",
            (r["convoy_id"],),
        ).fetchone()[0]
        r["stale_vehicles"] = [
            v["vehicle_id"]
            for v in db.query_all(
                """SELECT cv.vehicle_id, (SELECT MAX(reported_at) FROM position_reports p WHERE p.vehicle_id = cv.vehicle_id) AS last
                   FROM convoy_vehicles cv WHERE cv.convoy_id = ?""",
                (r["convoy_id"],),
                conn=conn,
            )
            if r["status"] == "departed" and fleet.is_stale(v["last"], now, limit)
        ]
        r["last_report"] = last
    return rows


def convoy_freshness(conn: sqlite3.Connection, now: datetime | None = None) -> Freshness:
    newest = _newest(conn, "SELECT MAX(at) FROM convoy_events")
    return freshness(newest, panel_limit(conn, "convoys"), now)


# -- requests and callbacks ----------------------------------------------------
def request_summary(conn: sqlite3.Connection, now: datetime | None = None) -> dict[str, Any]:
    now = now or clock.now()
    counts = conn.execute(
        """SELECT COUNT(*) AS open_total,
                  COALESCE(SUM(status = 'new'), 0) AS unassigned,
                  COALESCE(SUM(priority IN ('urgent','high')), 0) AS urgent_high
           FROM service_requests WHERE status != 'closed'"""
    ).fetchone()
    overdue = alerts.overdue_callbacks(conn, now)
    for r in overdue:
        r["overdue_hours"] = round((now - clock.parse(r["callback_due_at"])).total_seconds() / 3600.0, 1)
    return {
        "open": int(counts["open_total"]),
        "unassigned": int(counts["unassigned"]),
        "urgent_high": int(counts["urgent_high"]),
        "overdue_count": len(overdue),
        "overdue": overdue,
    }


def request_freshness(conn: sqlite3.Connection, now: datetime | None = None) -> Freshness:
    newest = _newest(conn, "SELECT MAX(at) FROM request_events", "SELECT MAX(created_at) FROM service_requests")
    return freshness(newest, panel_limit(conn, "requests"), now)


# -- advisories ----------------------------------------------------------------
def active_advisories(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Areas whose current advisory is above Normal, highest level first."""
    names = {r["area_id"]: r["name_en"] for r in db.query_all("SELECT area_id, name_en FROM areas", conn=conn)}
    rows = []
    for area_id, adv in security.current_advisories(conn).items():
        if adv["final_level"] >= 1:
            rows.append(
                {
                    "area_id": area_id,
                    "area": names.get(area_id, area_id),
                    "level": int(adv["final_level"]),
                    "level_name": security.LEVEL_NAMES[int(adv["final_level"])],
                    "decided_at": adv["decided_at"],
                    "override": bool(adv["is_override"]),
                }
            )
    return sorted(rows, key=lambda r: (-r["level"], r["area_id"]))


def advisory_freshness(conn: sqlite3.Connection, now: datetime | None = None) -> Freshness:
    return freshness(_newest(conn, "SELECT MAX(decided_at) FROM advisories"), panel_limit(conn, "advisories"), now)


# -- stock alerts ----------------------------------------------------------------
def stock_below_reorder(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Warehouse and item rows whose on-hand quantity is below the reorder threshold (at it is fine)."""
    return db.query_all(
        """SELECT s.warehouse_id, w.name AS warehouse, i.name AS item, i.unit, s.on_hand, l.reorder_threshold
           FROM v_stock_on_hand s
           JOIN item_lead_times l ON l.warehouse_id = s.warehouse_id AND l.item_id = s.item_id
           JOIN warehouses w ON w.warehouse_id = s.warehouse_id
           JOIN items i ON i.item_id = s.item_id
           WHERE s.on_hand < l.reorder_threshold
           ORDER BY CAST(s.on_hand AS REAL) / l.reorder_threshold, s.warehouse_id, s.item_id""",
        conn=conn,
    )


def open_alerts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return db.query_all(
        """SELECT a.alert_id, a.rule_id, r.severity, a.fired_at, a.entity_ref, a.message
           FROM alerts a JOIN alert_rules r ON r.rule_id = a.rule_id
           WHERE a.status = 'open'
           ORDER BY CASE r.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, a.fired_at DESC""",
        conn=conn,
    )


def stock_freshness(conn: sqlite3.Connection, now: datetime | None = None) -> Freshness:
    newest = _newest(conn, "SELECT MAX(moved_at) FROM stock_movements", "SELECT MAX(fired_at) FROM alerts")
    return freshness(newest, panel_limit(conn, "stock"), now)


# -- today's distributions against plan --------------------------------------------
def distributions_today(conn: sqlite3.Connection, today: str | None = None) -> dict[str, dict[str, Any]]:
    """Parcels and ready-to-eat meals, each against its own plan. The two are never added together.

    Plan: today's non-cancelled `distribution_plans`, grouped by the item's unit
    ('parcel' or 'meal'). Done: `distributions.parcel_quantity` (parcels) and
    `distributions.rte_meal_quantity` (meals).
    """
    day = today or clock.today().isoformat()
    planned = {
        r["unit"]: int(r["qty"])
        for r in db.query_all(
            """SELECT i.unit, SUM(p.planned_quantity) AS qty FROM distribution_plans p JOIN items i ON i.item_id = p.item_id
               WHERE p.planned_date = ? AND p.status != 'cancelled' AND i.unit IN ('parcel','meal') GROUP BY i.unit""",
            (day,),
            conn=conn,
        )
    }
    done = conn.execute(
        """SELECT COALESCE(SUM(parcel_quantity), 0) AS parcels, COALESCE(SUM(rte_meal_quantity), 0) AS meals,
                  COUNT(*) AS households
           FROM distributions WHERE distribution_date = ?""",
        (day,),
    ).fetchone()
    result: dict[str, dict[str, Any]] = {}
    for key, unit, label in (("parcels", "parcel", "Parcels"), ("meals", "meal", "Ready-to-eat meals")):
        plan, got = planned.get(unit, 0), int(done[key])
        result[key] = {
            "label": label,
            "unit": "parcels" if key == "parcels" else "meals",
            "planned": plan,
            "done": got,
            "percent": round(100.0 * got / plan, 1) if plan else None,
        }
    result["households"] = {
        "label": "Households served",
        "unit": "households",
        "planned": None,
        "done": int(done["households"]),
        "percent": None,
    }
    return result


def distribution_freshness(conn: sqlite3.Connection, now: datetime | None = None) -> Freshness:
    newest = _newest(conn, "SELECT MAX(recorded_at) FROM distributions")
    return freshness(newest, panel_limit(conn, "distributions"), now)
