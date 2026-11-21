"""The stock ledger (Projects 10, 12, 16, 17, 20).

stock_movements is the only source of on-hand stock: received minus dispatched.
A dispatch writes one dispatches row and one matching stock_movements row together.
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from shared import clock, db


def on_hand(conn: sqlite3.Connection, warehouse_id: str | None = None, item_id: str | None = None) -> pd.DataFrame:
    sql = "SELECT * FROM v_stock_on_hand WHERE 1=1"
    params: list[str] = []
    if warehouse_id:
        sql += " AND warehouse_id = ?"
        params.append(warehouse_id)
    if item_id:
        sql += " AND item_id = ?"
        params.append(item_id)
    return db.query_df(sql + " ORDER BY warehouse_id, item_id", params, conn)


def on_hand_qty(conn: sqlite3.Connection, warehouse_id: str, item_id: str) -> int:
    row = conn.execute("SELECT on_hand FROM v_stock_on_hand WHERE warehouse_id = ? AND item_id = ?", (warehouse_id, item_id)).fetchone()
    return int(row[0]) if row else 0


def record_receipt(
    conn: sqlite3.Connection,
    warehouse_id: str,
    item_id: str,
    quantity: int,
    staff_id: str,
    reason: str = "supplier_delivery",
    reference: str | None = None,
    at: str | None = None,
    channel: str = "app",
) -> int:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    cur = conn.execute(
        """INSERT INTO stock_movements(warehouse_id, item_id, moved_at, movement_type, reason, quantity, reference, recorded_by)
           VALUES (?,?,?,?,?,?,?,?)""",
        (warehouse_id, item_id, clock.normalize(at), "received", reason, quantity, reference, staff_id),
    )
    movement_id = int(cur.lastrowid)
    db.audit(
        conn,
        actor_id=staff_id,
        channel=channel,
        action="create",
        entity_table="stock_movements",
        entity_id=str(movement_id),
        after={"warehouse_id": warehouse_id, "item_id": item_id, "type": "received", "quantity": quantity, "reason": reason},
    )
    return movement_id


class InsufficientStock(Exception):
    pass


def record_dispatch(
    conn: sqlite3.Connection,
    warehouse_id: str,
    site_id: str,
    item_id: str,
    quantity: int,
    staff_id: str,
    plan_id: str | None = None,
    convoy_id: str | None = None,
    at: str | None = None,
    channel: str = "app",
    allow_negative: bool = False,
) -> str:
    """Create a dispatch and its ledger movement in one step. Returns the dispatch_id."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if not allow_negative and on_hand_qty(conn, warehouse_id, item_id) < quantity:
        raise InsufficientStock(f"Only {on_hand_qty(conn, warehouse_id, item_id)} {item_id} on hand at {warehouse_id}")
    when = clock.normalize(at)
    dispatch_id = db.next_id(conn, "dispatches", "dispatch_id", "DSP-", 4)
    conn.execute(
        """INSERT INTO dispatches(dispatch_id, plan_id, convoy_id, warehouse_id, site_id, item_id, quantity, dispatched_at, dispatched_by)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (dispatch_id, plan_id, convoy_id, warehouse_id, site_id, item_id, quantity, when, staff_id),
    )
    conn.execute(
        """INSERT INTO stock_movements(warehouse_id, item_id, moved_at, movement_type, reason, quantity, dispatch_id, reference, recorded_by)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (warehouse_id, item_id, when, "dispatched", "dispatch", quantity, dispatch_id, site_id, staff_id),
    )
    db.audit(
        conn,
        actor_id=staff_id,
        channel=channel,
        action="create",
        entity_table="dispatches",
        entity_id=dispatch_id,
        after={
            "warehouse_id": warehouse_id,
            "site_id": site_id,
            "item_id": item_id,
            "quantity": quantity,
            "plan_id": plan_id,
            "convoy_id": convoy_id,
        },
        at=when,
    )
    return dispatch_id
