"""Project 08 workflow steps over the shared sync engine (shared/sync.py).

The sync rules themselves (queue, base-version check, conflicts, resolution) live in
shared.sync and are shared with the Project 20 API. This module adds what the app needs
around them in local demo mode:

* The device's own copy of a record. A real device keeps a local copy; here it is derived
  from the shared database: the record as it was at the device's last sync (the highest
  saved version at or before `last_sync_at`, or a version this device saved itself),
  with the device's still-pending edits laid on top.
* Capture and edit on the device (queued with `enqueue_change`, audited), an edit on the
  server copy (`server_update`), the connection switch, "Sync now", and read models for
  the queue table, the side-by-side conflict view and the resolution history.

Every function takes an open connection and does not commit.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from shared import db, sync

CHANNEL = "app:08"
FIELD_LABELS: dict[str, str] = {
    "head_name": "Head of household",
    "area_id": "Area",
    "household_size": "Household size (people)",
    "primary_need": "Primary need",
    "urgency": "Urgency",
    "notes": "Notes",
}


def scenario(conn: sqlite3.Connection) -> dict[str, Any]:
    """Walkthrough IDs from the seed (record, device, server version, offline device)."""
    return json.loads(db.get_setting("demo.scenarios", "{}", conn=conn) or "{}").get("p08", {})


def devices(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return db.query_all("SELECT device_id, assigned_to, online, last_sync_at FROM sync_devices ORDER BY device_id", conn=conn)


def device(conn: sqlite3.Connection, device_id: str) -> dict[str, Any]:
    row = db.row_dict(conn, "sync_devices", "device_id", device_id)
    if row is None:
        raise sync.SyncError(f"Unknown device {device_id}")
    return row


# -- the device's local copy --------------------------------------------------
def known_version(conn: sqlite3.Connection, device_id: str, record_id: str) -> int | None:
    """The server version of a record the device last received: the highest version saved at or
    before its last sync, or saved by the device itself. None if the device has never seen it."""
    last_sync = device(conn, device_id)["last_sync_at"]
    row = conn.execute(
        """SELECT MAX(version) FROM record_versions
           WHERE record_id = ? AND ((? IS NOT NULL AND saved_at <= ?) OR (origin = 'device' AND device_id = ?))""",
        (record_id, last_sync, last_sync, device_id),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _pending_updates(conn: sqlite3.Connection, device_id: str, record_id: str) -> list[dict[str, Any]]:
    return db.query_all(
        "SELECT * FROM sync_queue WHERE device_id = ? AND record_id = ? AND operation = 'update' AND status = 'pending' ORDER BY queue_id",
        (device_id, record_id),
        conn=conn,
    )


def device_copy(conn: sqlite3.Connection, device_id: str, record_id: str) -> dict[str, Any] | None:
    """What the device shows for a record: {'data', 'base_version', 'pending_edits'}, or None.

    The base version is the one a pending edit was made from (so a second offline edit keeps the
    same base), otherwise the version the device last received.
    """
    pending = _pending_updates(conn, device_id, record_id)
    base = int(pending[0]["base_version"]) if pending else known_version(conn, device_id, record_id)
    if base is None:
        return None
    data = sync.version_data(conn, record_id, base)
    if data is None:
        return None
    for q in pending:
        data.update({k: v for k, v in json.loads(q["payload_json"]).items() if k in sync.FIELDS})
    return {"record_id": record_id, "data": data, "base_version": base, "pending_edits": len(pending)}


def device_record_ids(conn: sqlite3.Connection, device_id: str) -> list[str]:
    """Server records the device holds a copy of (it has received at least one version)."""
    return [
        r["record_id"]
        for r in db.query_all("SELECT record_id FROM intake_records ORDER BY record_id", conn=conn)
        if known_version(conn, device_id, r["record_id"]) is not None
    ]


# -- device actions -----------------------------------------------------------
def switch_connection(conn: sqlite3.Connection, device_id: str, online: bool, *, actor_id: str | None = None) -> None:
    """The simulated connection switch."""
    sync.set_online(conn, device_id, online, actor_id=actor_id)


def capture_record(conn: sqlite3.Connection, device_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Capture a new intake record on the device: it waits in the local queue as a pending create."""
    dev = device(conn, device_id)
    payload = {k: v for k, v in fields.items() if k in sync.FIELDS and v not in (None, "")}
    queued = sync.enqueue_change(conn, device_id=device_id, operation="create", payload=payload)
    db.audit(
        conn,
        actor_id=dev["assigned_to"],
        channel=CHANNEL,
        action="queue_create",
        entity_table="sync_queue",
        entity_id=str(queued["queue_id"]),
        after={**payload, "record_id": queued["record_id"], "device_id": device_id},
    )
    return queued


def edit_on_device(conn: sqlite3.Connection, device_id: str, record_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Edit the device's copy of a record: queues an update carrying the base version it came from."""
    dev = device(conn, device_id)
    copy = device_copy(conn, device_id, record_id)
    if copy is None:
        raise sync.SyncError(f"{device_id} has no copy of {record_id}")
    changes = {k: v for k, v in fields.items() if k in sync.FIELDS and v != copy["data"].get(k)}
    if not changes:
        raise sync.SyncError("Nothing changed on the device copy.")
    queued = sync.enqueue_change(
        conn, device_id=device_id, operation="update", payload=changes, record_id=record_id, base_version=copy["base_version"]
    )
    db.audit(
        conn,
        actor_id=dev["assigned_to"],
        channel=CHANNEL,
        action="queue_update",
        entity_table="sync_queue",
        entity_id=str(queued["queue_id"]),
        before={k: copy["data"].get(k) for k in changes},
        after={**changes, "record_id": record_id, "base_version": copy["base_version"], "device_id": device_id},
    )
    return {**queued, "base_version": copy["base_version"], "changes": changes}


def edit_on_server(conn: sqlite3.Connection, record_id: str, fields: dict[str, Any], *, actor_id: str) -> int:
    """An office edit of the server copy. Returns the new server version."""
    current = sync.record_snapshot(conn, record_id)
    if current is None:
        raise sync.SyncError(f"Record {record_id} does not exist on the server")
    changes = {k: v for k, v in fields.items() if k in sync.FIELDS and v != current.get(k)}
    if not changes:
        raise sync.SyncError("Nothing changed on the server copy.")
    return sync.server_update(conn, record_id, changes, actor_id=actor_id, channel=CHANNEL)


def sync_now(conn: sqlite3.Connection, device_id: str) -> sync.SyncReport:
    """Send the device's pending queue. Raises SyncError while the device has no connection."""
    return sync.sync_device(conn, device_id)


# -- read models --------------------------------------------------------------
def queue_rows(conn: sqlite3.Connection, device_id: str) -> list[dict[str, Any]]:
    rows = db.query_all("SELECT * FROM sync_queue WHERE device_id = ? ORDER BY queue_id DESC", (device_id,), conn=conn)
    return [
        {
            "Queue #": r["queue_id"],
            "Record": r["record_id"],
            "Operation": r["operation"],
            "Changes": ", ".join(f"{FIELD_LABELS.get(k, k)}: {v}" for k, v in json.loads(r["payload_json"]).items()),
            "Base version": r["base_version"],
            "Queued at": r["queued_at"],
            "Status": r["status"],
            "Sent at": r["synced_at"],
            "Conflict": r["conflict_id"],
        }
        for r in rows
    ]


def conflict_view(conn: sqlite3.Connection, conflict_id: str) -> dict[str, Any]:
    """A conflict with one row per field: base, device and server values, and whether they differ."""
    detail = sync.conflict_detail(conn, conflict_id)
    base = sync.version_data(conn, detail["record_id"], int(detail["base_version"])) or {}
    differing = set(detail["differing_fields"])
    detail["rows"] = [
        {
            "field": f,
            "label": FIELD_LABELS[f],
            "base": base.get(f),
            "device": detail["device_data"].get(f),
            "server": detail["server_data"].get(f),
            "differs": f in differing,
        }
        for f in sync.FIELDS
    ]
    return detail


def resolved_history(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = db.query_all(
        "SELECT * FROM sync_conflicts WHERE status = 'resolved' ORDER BY resolved_at DESC, conflict_id DESC LIMIT ?",
        (limit,),
        conn=conn,
    )
    return [
        {
            "Conflict": r["conflict_id"],
            "Record": r["record_id"],
            "Device": r["device_id"],
            "Device base version": r["base_version"],
            "Server version": r["server_version"],
            "Choice": (r["resolution"] or "").replace("_", " "),
            "Resolved by": r["resolved_by"],
            "Resolved at": r["resolved_at"],
            "Reason": r["reason"],
        }
        for r in rows
    ]
