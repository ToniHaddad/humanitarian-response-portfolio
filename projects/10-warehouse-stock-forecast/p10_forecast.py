"""Warehouse stock forecast (Project 10): stock table, demand, stockout dates, replenishment.

Technique: straightforward calculation (day-by-day projection), no statistical model.

Rules (documented in the README):
* On hand comes only from the ledger (`shared.stock`, view `v_stock_on_hand`).
* Recent demand = (ledger `dispatched` minus `received` with reason `distribution_return`)
  over the `window_days` days before today, divided by `window_days`, never below zero.
* Planned demand = `distribution_plans` rows with status `planned`, `planned_date >= today`
  and no dispatch yet. For a warehouse-item pair, from today to its last planned date the
  daily demand is the planned quantities only; after that, and for pairs without plans,
  the recent daily demand is used. This avoids counting the same activity twice.
* Stockout date = first day the projected balance goes below zero within the horizon.
* Reorder-by = stockout date minus lead time. Suggested replenishment = demand over
  (lead time + safety days + 28 days) minus on hand, rounded up to the next 10.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from shared import clock, db
from shared import stock as stock_ledger
from shared.confirm import require_confirmation

CHANNEL = "app:10"
ALLOWED_ROLES = ("warehouse_officer", "coordinator")
DEFAULT_WINDOW_DAYS = 28
DEFAULT_HORIZON_DAYS = 90
COVER_DAYS_AFTER_LEAD = 28  # replenishment covers lead time + safety days + this many days
ROUND_TO = 10
RECEIPT_REASONS = ("supplier_delivery", "transfer_in")


# -- helpers ---------------------------------------------------------------
def plural_unit(unit: str) -> str:
    return unit if unit.endswith("s") else f"{unit}s"


def axis_title(item_name: str, unit: str) -> str:
    """'Standard food parcels (parcels)': item name in plural plus its unit."""
    name = item_name if item_name.endswith("s") else f"{item_name}s"
    return f"{name} ({plural_unit(unit)})"


def round_up_to(value: float, step: int = ROUND_TO) -> int:
    """Next multiple of `step` (0 stays 0). Tiny float noise is ignored."""
    if value <= 1e-9:
        return 0
    return int(math.ceil(round(value, 6) / step) * step)


def warehouses(conn: sqlite3.Connection) -> list[dict]:
    return db.query_all("SELECT warehouse_id, name FROM warehouses ORDER BY warehouse_id", conn=conn)


def items(conn: sqlite3.Connection) -> list[dict]:
    return db.query_all("SELECT item_id, name, unit FROM items ORDER BY item_id", conn=conn)


def sites(conn: sqlite3.Connection) -> list[dict]:
    return db.query_all("SELECT site_id, name FROM sites ORDER BY site_id", conn=conn)


# -- stock table -----------------------------------------------------------
def stock_table(conn: sqlite3.Connection, warehouse_id: str) -> pd.DataFrame:
    """Received, dispatched and on hand per item for one warehouse (from the ledger view)."""
    oh = stock_ledger.on_hand(conn, warehouse_id=warehouse_id)
    meta = db.query_df("SELECT item_id, name AS item, unit FROM items", conn=conn)
    out = oh.merge(meta, on="item_id")
    return out[["item_id", "item", "received", "dispatched", "on_hand", "unit"]].reset_index(drop=True)


# -- demand ----------------------------------------------------------------
def window_bounds(window_days: int, today: date | None = None) -> tuple[str, str]:
    """[start, end) timestamps of the demand window: the `window_days` days before today."""
    today = today or clock.today()
    start = (today - timedelta(days=window_days)).isoformat() + "T00:00:00"
    end = today.isoformat() + "T00:00:00"
    return start, end


def recent_net_demand(
    conn: sqlite3.Connection, window_days: int = DEFAULT_WINDOW_DAYS, today: date | None = None
) -> dict[tuple[str, str], int]:
    """Net quantity leaving each (warehouse, item) in the window: dispatched minus distribution returns."""
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    start, end = window_bounds(window_days, today)
    rows = conn.execute(
        """SELECT warehouse_id, item_id,
                  COALESCE(SUM(CASE WHEN movement_type = 'dispatched' THEN quantity END), 0)
                - COALESCE(SUM(CASE WHEN movement_type = 'received' AND reason = 'distribution_return'
                                    THEN quantity END), 0) AS net
           FROM stock_movements
           WHERE moved_at >= ? AND moved_at < ?
           GROUP BY warehouse_id, item_id""",
        (start, end),
    ).fetchall()
    return {(r["warehouse_id"], r["item_id"]): max(0, int(r["net"])) for r in rows}


def recent_daily_demand(
    conn: sqlite3.Connection, warehouse_id: str, item_id: str, window_days: int = DEFAULT_WINDOW_DAYS, today: date | None = None
) -> float:
    return recent_net_demand(conn, window_days, today).get((warehouse_id, item_id), 0) / window_days


# -- planned distributions --------------------------------------------------
def open_plans(
    conn: sqlite3.Connection, warehouse_id: str | None = None, item_id: str | None = None, today: date | None = None
) -> pd.DataFrame:
    """Plans that still draw on stock: status planned, dated today or later, no dispatch yet."""
    today = today or clock.today()
    sql = """SELECT p.plan_id, p.site_id, p.warehouse_id, p.item_id, p.planned_date, p.planned_quantity
             FROM distribution_plans p
             WHERE p.status = 'planned' AND p.planned_date >= ?
               AND NOT EXISTS (SELECT 1 FROM dispatches d WHERE d.plan_id = p.plan_id)"""
    params: list[str] = [today.isoformat()]
    if warehouse_id:
        sql += " AND p.warehouse_id = ?"
        params.append(warehouse_id)
    if item_id:
        sql += " AND p.item_id = ?"
        params.append(item_id)
    return db.query_df(sql + " ORDER BY p.planned_date, p.plan_id", params, conn)


def daily_demand_series(rate: float, plan_by_date: dict[date, int], today: date, days: int) -> list[float]:
    """Demand for each day from today (index 0) for `days` days.

    Up to and including the last planned date: planned quantities only (0 on non-plan days).
    After it, and with no plans at all: the recent daily rate. Never both for the same day.
    """
    last_plan = max(plan_by_date) if plan_by_date else None
    series: list[float] = []
    for i in range(days):
        d = today + timedelta(days=i)
        if last_plan is not None and d <= last_plan:
            series.append(float(plan_by_date.get(d, 0)))
        else:
            series.append(float(rate))
    return series


def first_stockout_index(on_hand: float, series: list[float]) -> int | None:
    """Index of the first day whose demand cannot be fully met (balance below zero), else None."""
    balance = float(on_hand)
    for i, demand in enumerate(series):
        balance -= demand
        if balance < -1e-9:
            return i
    return None


def projected_balances(on_hand: float, series: list[float]) -> list[float]:
    balances: list[float] = []
    balance = float(on_hand)
    for demand in series:
        balance -= demand
        balances.append(balance)
    return balances


# -- the forecast ----------------------------------------------------------
@dataclass
class PairForecast:
    warehouse_id: str
    item_id: str
    item: str
    unit: str
    on_hand: int
    daily_demand: float
    planned_in_horizon: int
    stockout_date: date | None
    lead_time_days: int
    safety_days: int
    reorder_threshold: int
    reorder_by: date | None
    order_now: bool
    suggested_replenishment: int
    below_threshold: bool
    series: list[float]


def forecast_pair(
    *,
    warehouse_id: str,
    item_id: str,
    item: str,
    unit: str,
    on_hand: int,
    rate: float,
    plan_by_date: dict[date, int],
    lead_time_days: int,
    safety_days: int,
    reorder_threshold: int,
    horizon_days: int,
    today: date,
) -> PairForecast:
    """Pure calculation for one warehouse-item pair (tested directly with hand-built inputs)."""
    cover = lead_time_days + safety_days + COVER_DAYS_AFTER_LEAD
    series = daily_demand_series(rate, plan_by_date, today, max(horizon_days, cover))
    idx = first_stockout_index(on_hand, series[:horizon_days])
    stockout = today + timedelta(days=idx) if idx is not None else None
    reorder_by = stockout - timedelta(days=lead_time_days) if stockout else None
    horizon_end = today + timedelta(days=horizon_days)
    planned = sum(q for d, q in plan_by_date.items() if d < horizon_end)
    return PairForecast(
        warehouse_id=warehouse_id,
        item_id=item_id,
        item=item,
        unit=unit,
        on_hand=on_hand,
        daily_demand=rate,
        planned_in_horizon=planned,
        stockout_date=stockout,
        lead_time_days=lead_time_days,
        safety_days=safety_days,
        reorder_threshold=reorder_threshold,
        reorder_by=reorder_by,
        order_now=reorder_by is not None and reorder_by <= today,
        suggested_replenishment=round_up_to(sum(series[:cover]) - on_hand),
        below_threshold=on_hand < reorder_threshold,
        series=series,
    )


def forecast_pairs(
    conn: sqlite3.Connection,
    warehouse_id: str | None = None,
    item_id: str | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: date | None = None,
) -> list[PairForecast]:
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    today = today or clock.today()
    demand = recent_net_demand(conn, window_days, today)
    plans = open_plans(conn, warehouse_id, item_id, today)
    plan_map: dict[tuple[str, str], dict[date, int]] = {}
    for p in plans.itertuples():
        d = date.fromisoformat(p.planned_date)
        slot = plan_map.setdefault((p.warehouse_id, p.item_id), {})
        slot[d] = slot.get(d, 0) + int(p.planned_quantity)
    sql = """SELECT lt.warehouse_id, lt.item_id, i.name AS item, i.unit, lt.lead_time_days, lt.safety_days,
                    lt.reorder_threshold, v.on_hand
             FROM item_lead_times lt
             JOIN items i ON i.item_id = lt.item_id
             JOIN v_stock_on_hand v ON v.warehouse_id = lt.warehouse_id AND v.item_id = lt.item_id
             WHERE 1=1"""
    params: list[str] = []
    if warehouse_id:
        sql += " AND lt.warehouse_id = ?"
        params.append(warehouse_id)
    if item_id:
        sql += " AND lt.item_id = ?"
        params.append(item_id)
    out: list[PairForecast] = []
    for r in conn.execute(sql + " ORDER BY lt.warehouse_id, lt.item_id", params).fetchall():
        key = (r["warehouse_id"], r["item_id"])
        out.append(
            forecast_pair(
                warehouse_id=r["warehouse_id"],
                item_id=r["item_id"],
                item=r["item"],
                unit=r["unit"],
                on_hand=int(r["on_hand"]),
                rate=demand.get(key, 0) / window_days,
                plan_by_date=plan_map.get(key, {}),
                lead_time_days=int(r["lead_time_days"]),
                safety_days=int(r["safety_days"]),
                reorder_threshold=int(r["reorder_threshold"]),
                horizon_days=horizon_days,
                today=today,
            )
        )
    return out


def forecast_table(
    conn: sqlite3.Connection,
    warehouse_id: str | None = None,
    item_id: str | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    today: date | None = None,
) -> pd.DataFrame:
    rows = []
    for f in forecast_pairs(conn, warehouse_id, item_id, window_days, horizon_days, today):
        if f.stockout_date is None:
            stockout_text = f"None within {horizon_days} days"
            action = "Below reorder threshold" if f.below_threshold else "OK"
        else:
            stockout_text = f.stockout_date.isoformat()
            action = "ORDER NOW / OVERDUE" if f.order_now else "Order by date"
        rows.append(
            {
                "warehouse_id": f.warehouse_id,
                "item_id": f.item_id,
                "item": f.item,
                "unit": f.unit,
                "on_hand": f.on_hand,
                "recent_daily_demand": round(f.daily_demand, 2),
                "planned_in_horizon": f.planned_in_horizon,
                "stockout_date": stockout_text,
                "lead_time_days": f.lead_time_days,
                "reorder_by": f.reorder_by.isoformat() if f.reorder_by else "",
                "suggested_replenishment": f.suggested_replenishment,
                "below_threshold": f.below_threshold,
                "reorder_threshold": f.reorder_threshold,
                "action": action,
            }
        )
    return pd.DataFrame(rows)


def balance_projection(f: PairForecast, horizon_days: int, today: date) -> pd.DataFrame:
    """Projected end-of-day balance for one pair over the horizon (chart data)."""
    balances = projected_balances(f.on_hand, f.series[:horizon_days])
    return pd.DataFrame(
        {
            "date": [today + timedelta(days=i) for i in range(len(balances))],
            "projected_balance": [round(b, 1) for b in balances],
            "demand": [round(x, 1) for x in f.series[:horizon_days]],
        }
    )


def recent_ledger(conn: sqlite3.Connection, warehouse_id: str, limit: int = 25) -> pd.DataFrame:
    return db.query_df(
        """SELECT m.movement_id, m.moved_at, m.item_id, i.name AS item, m.movement_type, m.reason, m.quantity,
                  i.unit, m.reference, m.recorded_by
           FROM stock_movements m JOIN items i ON i.item_id = m.item_id
           WHERE m.warehouse_id = ?
           ORDER BY m.moved_at DESC, m.movement_id DESC LIMIT ?""",
        (warehouse_id, limit),
        conn,
    )


# -- consequential actions ---------------------------------------------------
def receive_stock(
    conn: sqlite3.Connection,
    warehouse_id: str,
    item_id: str,
    quantity: int,
    confirmed_by: str | None,
    reason: str = "supplier_delivery",
    reference: str | None = None,
) -> int:
    """Record a receipt in the ledger after staff confirmation. Returns the movement id."""
    staff_id = require_confirmation(conn, confirmed_by, allowed_roles=ALLOWED_ROLES)
    if reason not in RECEIPT_REASONS:
        raise ValueError(f"reason must be one of {RECEIPT_REASONS}")
    return stock_ledger.record_receipt(
        conn, warehouse_id, item_id, int(quantity), staff_id, reason=reason, reference=reference, channel=CHANNEL
    )


def dispatch_stock(
    conn: sqlite3.Connection,
    warehouse_id: str,
    site_id: str,
    item_id: str,
    quantity: int,
    confirmed_by: str | None,
    plan_id: str | None = None,
) -> str:
    """Dispatch stock to a site after staff confirmation. Returns the dispatch id.

    With a plan_id the plan must be open, belong to this warehouse and item, and be for this
    site; it is marked 'dispatched' in the same transaction. Refuses to go below zero on hand.
    """
    staff_id = require_confirmation(conn, confirmed_by, allowed_roles=ALLOWED_ROLES)
    plan = None
    if plan_id:
        plan = db.row_dict(conn, "distribution_plans", "plan_id", plan_id)
        if plan is None:
            raise ValueError(f"Unknown plan {plan_id}")
        if plan["status"] != "planned" or conn.execute("SELECT 1 FROM dispatches WHERE plan_id = ?", (plan_id,)).fetchone():
            raise ValueError(f"Plan {plan_id} is not open (status {plan['status']}, or already dispatched)")
        if (plan["warehouse_id"], plan["item_id"], plan["site_id"]) != (warehouse_id, item_id, site_id):
            raise ValueError(f"Plan {plan_id} is for {plan['warehouse_id']}, {plan['item_id']}, {plan['site_id']}")
    dispatch_id = stock_ledger.record_dispatch(
        conn, warehouse_id, site_id, item_id, int(quantity), staff_id, plan_id=plan_id, channel=CHANNEL
    )
    if plan is not None:
        conn.execute("UPDATE distribution_plans SET status = 'dispatched' WHERE plan_id = ?", (plan_id,))
        db.audit(
            conn,
            actor_id=staff_id,
            channel=CHANNEL,
            action="update",
            entity_table="distribution_plans",
            entity_id=str(plan_id),
            before={"status": "planned"},
            after={"status": "dispatched", "dispatch_id": dispatch_id},
            reason="dispatched from warehouse stock",
        )
    return dispatch_id
