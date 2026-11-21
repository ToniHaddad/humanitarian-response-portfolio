"""SQLite access shared by every project: connections, queries, IDs, settings, audit."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

from shared import clock, config


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open the shared database with foreign keys on and rows as sqlite3.Row."""
    target = Path(path) if path else config.db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    # WAL lets the many reading apps keep reading while the API, scheduler or
    # simulator commits. It is a property of the file, so setting it again is cheap.
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.OperationalError:
        pass  # another process holds a lock right now; the file keeps its current mode
    return conn


@contextmanager
def transaction(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Connection that commits on success and rolls back on any error.

    The write lock is taken at the start (BEGIN IMMEDIATE), so a read-then-write
    step such as next_id() or a stock check cannot race another writer: the second
    writer waits here instead of computing the same ID or passing the same check.
    Call next_id() and the stock functions only inside this context.
    """
    conn = connect(path)
    conn.isolation_level = None  # explicit transaction control
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        if conn.in_transaction:
            conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(config.SCHEMA_PATH.read_text(encoding="utf-8"))


def database_exists(path: Path | str | None = None) -> bool:
    target = Path(path) if path else config.db_path()
    if not target.exists():
        return False
    try:
        with sqlite3.connect(target) as conn:
            row = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='settings'").fetchone()
        return bool(row and row[0])
    except sqlite3.DatabaseError:
        return False


def query_df(sql: str, params: Sequence[Any] | dict[str, Any] = (), conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame."""
    own = conn is None
    conn = conn or connect()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        if own:
            conn.close()


def query_all(sql: str, params: Sequence[Any] | dict[str, Any] = (), conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    own = conn is None
    conn = conn or connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        if own:
            conn.close()


def query_one(sql: str, params: Sequence[Any] | dict[str, Any] = (), conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    rows = query_all(sql, params, conn)
    return rows[0] if rows else None


def scalar(sql: str, params: Sequence[Any] | dict[str, Any] = (), conn: sqlite3.Connection | None = None) -> Any:
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    finally:
        if own:
            conn.close()


_ID_RE = re.compile(r"(\d+)$")


def next_id(conn: sqlite3.Connection, table: str, column: str, prefix: str, width: int) -> str:
    """Next text ID such as CALL-000231: highest numeric suffix with this prefix, plus one."""
    rows = conn.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (f"{prefix}%",)).fetchall()
    highest = 0
    for (value,) in rows:
        match = _ID_RE.search(value)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"{prefix}{highest + 1:0{width}d}"


def get_setting(key: str, default: str | None = None, conn: sqlite3.Connection | None = None) -> str | None:
    value = scalar("SELECT value FROM settings WHERE key = ?", (key,), conn)
    return default if value is None else str(value)


def get_setting_float(key: str, default: float, conn: sqlite3.Connection | None = None) -> float:
    value = get_setting(key, None, conn)
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def set_setting(conn: sqlite3.Connection, key: str, value: str, staff_id: str | None, description: str | None = None) -> None:
    before = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if before is None:
        conn.execute(
            "INSERT INTO settings(key, value, description, updated_at, updated_by) VALUES (?,?,?,?,?)",
            (key, value, description or key, clock.iso(), staff_id),
        )
    else:
        conn.execute(
            "UPDATE settings SET value = ?, updated_at = ?, updated_by = ? WHERE key = ?",
            (value, clock.iso(), staff_id, key),
        )
    audit(
        conn,
        actor_id=staff_id or "system",
        channel="settings",
        action="update",
        entity_table="settings",
        entity_id=key,
        before={"value": before[0]} if before else None,
        after={"value": value},
    )


def staff_role(conn: sqlite3.Connection, staff_id: str) -> str:
    row = conn.execute("SELECT role FROM staff WHERE staff_id = ?", (staff_id,)).fetchone()
    return row[0] if row else "system"


def audit(
    conn: sqlite3.Connection,
    *,
    actor_id: str,
    channel: str,
    action: str,
    entity_table: str,
    entity_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
    actor_role: str | None = None,
    at: str | None = None,
) -> None:
    """Append one row to the audit log. Call it inside the same transaction as the change."""
    role = actor_role or (staff_role(conn, actor_id) if actor_id != "system" else "system")
    conn.execute(
        """INSERT INTO audit_log(at, actor_id, actor_role, channel, action, entity_table, entity_id,
                                 before_json, after_json, reason)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            at or clock.iso(),
            actor_id,
            role,
            channel,
            action,
            entity_table,
            entity_id,
            json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
            json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
            reason,
        ),
    )


def row_dict(conn: sqlite3.Connection, table: str, key_col: str, key: str) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT * FROM {table} WHERE {key_col} = ?", (key,)).fetchone()
    return dict(row) if row else None


def table_names(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
