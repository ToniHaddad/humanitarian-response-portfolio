"""Offline field sync: the server side, plus the device-side queue used in local demo mode.

Project 08 calls these functions directly in local demo mode. The Project 20 API exposes
the same functions as endpoints, so both paths share one set of rules:

* A device holds changes in its local queue (`sync_queue`) while it has no connection.
* When the connection returns, each queued change is applied with `apply_change`.
* An update whose `base_version` is not the record's current server version means the
  record was edited in two places. That is a conflict: the record is left unchanged, a
  `sync_conflicts` row holds both versions, and nothing is merged or overwritten silently.
* A conflict is resolved only by `resolve_conflict`, with a staff choice (keep the device
  version, keep the server version, or choose field by field), a reason, and staff
  confirmation (`shared.confirm.require_confirmation`).

Every function takes an open connection and does not commit; call it inside
`db.transaction()` (or commit yourself). Every change writes an audit row.

Server-side functions (API endpoints in Project 20):
    apply_change(conn, device_id=..., record_id=..., operation=..., payload=..., base_version=..., actor_id=...)
    server_update(conn, record_id, changes, actor_id=...)      # an edit made on the server (office) side
    open_conflicts(conn) / conflict_detail(conn, conflict_id)
    resolve_conflict(conn, conflict_id, choice=..., confirmed_by=..., reason=..., field_choices=...)
    record_snapshot(conn, record_id) / next_record_id(conn) / next_device_record_id(conn, device_id)

Device-side functions (local demo mode; a real device would keep its queue locally):
    set_online(conn, device_id, online, actor_id=...)
    enqueue_change(conn, device_id=..., operation=..., payload=..., record_id=..., base_version=...)
    sync_device(conn, device_id, actor_id=...)                 # refuses while the device is offline
    queue_status(conn, device_id)
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from shared import clock, confirm, db

# The versioned fields of an intake record (the same keys the seed writes to record_versions.data_json).
FIELDS: tuple[str, ...] = ("head_name", "area_id", "household_size", "primary_need", "urgency", "notes")
URGENCIES: tuple[str, ...] = ("low", "normal", "high", "urgent")
CHOICES: tuple[str, ...] = ("keep_device", "keep_server", "field_by_field")
RESOLVER_ROLES: tuple[str, ...] = ("caseworker", "coordinator")
CHANNEL = "sync"

_INT_RE = re.compile(r"^INT-(\d+)$")


class SyncError(Exception):
    """A change or resolution that cannot be applied (offline device, bad input, stale conflict)."""


@dataclass
class ApplyResult:
    """Outcome of applying one queued change on the server."""

    record_id: str
    operation: str
    outcome: str  # 'applied' or 'conflict'
    version: int  # server version after the call (unchanged on a conflict)
    conflict_id: str | None = None
    queue_id: int | None = None
    message: str = ""
    renamed_from: str | None = None  # set when a created record's ID was already taken on the server

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SyncReport:
    """Result of syncing one device's pending queue."""

    device_id: str
    synced_at: str
    results: list[ApplyResult] = field(default_factory=list)

    @property
    def applied(self) -> int:
        return sum(1 for r in self.results if r.outcome == "applied")

    @property
    def conflicts(self) -> int:
        return sum(1 for r in self.results if r.outcome == "conflict")

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "synced_at": self.synced_at,
            "applied": self.applied,
            "conflicts": self.conflicts,
            "results": [r.to_dict() for r in self.results],
        }


# -- reading ---------------------------------------------------------------
def next_record_id(conn: sqlite3.Connection) -> str:
    """Next free intake record ID (INT-0001 style), also skipping IDs held in device queues."""
    highest = 0
    rows = conn.execute("SELECT record_id FROM intake_records UNION SELECT record_id FROM sync_queue").fetchall()
    for (value,) in rows:
        match = _INT_RE.match(str(value))
        if match:
            highest = max(highest, int(match.group(1)))
    return f"INT-{highest + 1:04d}"


def next_device_record_id(conn: sqlite3.Connection, device_id: str) -> str:
    """Device-scoped ID for a record created offline (INT-DEV03-0002), so it cannot collide with server IDs."""
    prefix = f"INT-{device_id.replace('-', '')}-"
    highest = 0
    rows = conn.execute(
        "SELECT record_id FROM intake_records WHERE record_id LIKE ? UNION SELECT record_id FROM sync_queue WHERE record_id LIKE ?",
        (f"{prefix}%", f"{prefix}%"),
    ).fetchall()
    for (value,) in rows:
        tail = str(value)[len(prefix) :]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}{highest + 1:04d}"


def record_snapshot(conn: sqlite3.Connection, record_id: str) -> dict[str, Any] | None:
    """The record's versioned fields plus 'version', or None if the server has no such record."""
    row = conn.execute(f"SELECT {', '.join(FIELDS)}, version FROM intake_records WHERE record_id = ?", (record_id,)).fetchone()
    return dict(row) if row else None


def version_data(conn: sqlite3.Connection, record_id: str, version: int) -> dict[str, Any] | None:
    """The saved data of one version of a record (from record_versions), or None."""
    row = conn.execute("SELECT data_json FROM record_versions WHERE record_id = ? AND version = ?", (record_id, version)).fetchone()
    return json.loads(row[0]) if row else None


def differing_fields(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """Versioned fields whose values differ between two versions of a record."""
    return [f for f in FIELDS if a.get(f) != b.get(f)]


def open_conflicts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return db.query_all(
        "SELECT * FROM sync_conflicts WHERE status = 'open' ORDER BY detected_at, conflict_id",
        conn=conn,
    )


def conflict_detail(conn: sqlite3.Connection, conflict_id: str) -> dict[str, Any]:
    """A conflict with both versions decoded and the list of differing fields."""
    row = db.row_dict(conn, "sync_conflicts", "conflict_id", conflict_id)
    if row is None:
        raise SyncError(f"Unknown conflict {conflict_id}")
    device = json.loads(row["device_data_json"])
    server = json.loads(row["server_data_json"])
    row["device_data"] = device
    row["server_data"] = server
    row["differing_fields"] = differing_fields(device, server)
    return row


# -- validation ------------------------------------------------------------
def _clean(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    unknown = set(payload) - set(FIELDS) - {"household_id"}
    if unknown:
        raise SyncError(f"Unknown field(s): {', '.join(sorted(unknown))}")
    data = dict(payload)
    if "household_size" in data and data["household_size"] is not None:
        try:
            data["household_size"] = int(data["household_size"])
        except (TypeError, ValueError) as exc:
            raise SyncError("household_size must be a whole number") from exc
        if data["household_size"] <= 0:
            raise SyncError("household_size must be at least 1")
    if data.get("urgency") is not None and data["urgency"] not in URGENCIES:
        raise SyncError(f"urgency must be one of {', '.join(URGENCIES)}")
    if data.get("area_id") is not None and not conn.execute("SELECT 1 FROM areas WHERE area_id = ?", (data["area_id"],)).fetchone():
        raise SyncError(f"Unknown area {data['area_id']}")
    if data.get("household_id") and not conn.execute("SELECT 1 FROM households WHERE household_id = ?", (data["household_id"],)).fetchone():
        raise SyncError(f"Unknown household {data['household_id']}")
    if "head_name" in data and not str(data["head_name"] or "").strip():
        raise SyncError("head_name cannot be empty")
    return data


def _device(conn: sqlite3.Connection, device_id: str) -> dict[str, Any]:
    row = db.row_dict(conn, "sync_devices", "device_id", device_id)
    if row is None:
        raise SyncError(f"Unknown device {device_id}")
    return row


def _write_version(
    conn: sqlite3.Connection, record_id: str, version: int, data: dict[str, Any], origin: str, device_id: str | None, staff_id: str
) -> None:
    conn.execute(
        "INSERT INTO record_versions(record_id, version, data_json, origin, device_id, saved_at, saved_by) VALUES (?,?,?,?,?,?,?)",
        (record_id, version, json.dumps({f: data.get(f) for f in FIELDS}, ensure_ascii=False), origin, device_id, clock.iso(), staff_id),
    )


def _update_record(conn: sqlite3.Connection, record_id: str, new_data: dict[str, Any], staff_id: str) -> int:
    current = record_snapshot(conn, record_id)
    assert current is not None
    version = int(current["version"]) + 1
    sets = ", ".join(f"{f} = ?" for f in FIELDS)
    conn.execute(
        f"UPDATE intake_records SET {sets}, version = ?, updated_at = ?, updated_by = ? WHERE record_id = ?",
        (*[new_data.get(f) for f in FIELDS], version, clock.iso(), staff_id, record_id),
    )
    return version


def _mark_queue(conn: sqlite3.Connection, queue_id: int | None, status: str, conflict_id: str | None, record_id: str) -> None:
    if queue_id is None:
        return
    conn.execute(
        "UPDATE sync_queue SET status = ?, synced_at = ?, conflict_id = ?, record_id = ? WHERE queue_id = ?",
        (status, clock.iso(), conflict_id, record_id, queue_id),
    )


# -- server side -----------------------------------------------------------
def apply_change(
    conn: sqlite3.Connection,
    *,
    device_id: str,
    record_id: str,
    operation: str,
    payload: dict[str, Any],
    base_version: int | None,
    actor_id: str | None = None,
    queue_id: int | None = None,
) -> ApplyResult:
    """Apply one change sent by a device. Never overwrites a newer server version.

    create: inserts the record (a taken ID gets the next free ID; see renamed_from).
    update: applied only when base_version equals the server's current version; otherwise a
            sync_conflicts row is opened and the record is left exactly as it was.
    actor_id defaults to the staff member the device is assigned to. queue_id, when given,
    marks that queue row synced / conflict.
    """
    device = _device(conn, device_id)
    staff_id = actor_id or device["assigned_to"]
    data = _clean(conn, payload)

    if operation == "create":
        renamed_from = None
        if record_snapshot(conn, record_id) is not None:
            renamed_from, record_id = record_id, next_record_id(conn)
        if not str(data.get("head_name") or "").strip():
            raise SyncError("A new record needs head_name")
        now = clock.iso()
        conn.execute(
            """INSERT INTO intake_records(record_id, source, household_id, head_name, area_id, household_size, primary_need,
                                          urgency, notes, raw_note, language, version, created_at, created_by, updated_at, updated_by)
               VALUES (?, 'field_device', ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 1, ?, ?, ?, ?)""",
            (
                record_id,
                data.get("household_id"),
                data["head_name"],
                data.get("area_id"),
                data.get("household_size"),
                data.get("primary_need"),
                data.get("urgency"),
                data.get("notes"),
                now,
                staff_id,
                now,
                staff_id,
            ),
        )
        _write_version(conn, record_id, 1, data, "device", device_id, staff_id)
        db.audit(
            conn,
            actor_id=staff_id,
            channel=CHANNEL,
            action="create",
            entity_table="intake_records",
            entity_id=record_id,
            after={**{f: data.get(f) for f in FIELDS}, "device_id": device_id, "renamed_from": renamed_from},
        )
        _mark_queue(conn, queue_id, "synced", None, record_id)
        if renamed_from:
            conn.execute(
                "UPDATE sync_queue SET record_id = ? WHERE device_id = ? AND record_id = ? AND status = 'pending'",
                (record_id, device_id, renamed_from),
            )
        msg = f"Created {record_id}" + (f" (device ID {renamed_from} was already taken)" if renamed_from else "")
        return ApplyResult(record_id, operation, "applied", 1, queue_id=queue_id, message=msg, renamed_from=renamed_from)

    if operation != "update":
        raise SyncError(f"Unknown operation {operation!r}")
    current = record_snapshot(conn, record_id)
    if current is None:
        raise SyncError(f"Record {record_id} does not exist on the server")
    if base_version is None:
        raise SyncError("An update needs the base_version the device edited from")
    server_version = int(current["version"])
    base = version_data(conn, record_id, int(base_version)) or {f: current[f] for f in FIELDS}
    device_data = {**{f: base.get(f) for f in FIELDS}, **{k: v for k, v in data.items() if k in FIELDS}}

    if int(base_version) != server_version:
        conflict_id = db.next_id(conn, "sync_conflicts", "conflict_id", "CNF-", 4)
        server_data = {f: current[f] for f in FIELDS}
        conn.execute(
            """INSERT INTO sync_conflicts(conflict_id, record_id, device_id, base_version, server_version, device_data_json,
                                          server_data_json, detected_at, status)
               VALUES (?,?,?,?,?,?,?,?, 'open')""",
            (
                conflict_id,
                record_id,
                device_id,
                int(base_version),
                server_version,
                json.dumps(device_data, ensure_ascii=False),
                json.dumps(server_data, ensure_ascii=False),
                clock.iso(),
            ),
        )
        db.audit(
            conn,
            actor_id=staff_id,
            channel=CHANNEL,
            action="conflict_detected",
            entity_table="sync_conflicts",
            entity_id=conflict_id,
            before=server_data,
            after=device_data,
            reason=f"Device edited version {base_version}; server is at version {server_version}.",
        )
        _mark_queue(conn, queue_id, "conflict", conflict_id, record_id)
        return ApplyResult(
            record_id,
            operation,
            "conflict",
            server_version,
            conflict_id=conflict_id,
            queue_id=queue_id,
            message=f"{record_id} was changed on the server (v{server_version}) after the device's copy (v{base_version}): staff must choose.",
        )

    version = _update_record(conn, record_id, device_data, staff_id)
    _write_version(conn, record_id, version, device_data, "device", device_id, staff_id)
    db.audit(
        conn,
        actor_id=staff_id,
        channel=CHANNEL,
        action="update",
        entity_table="intake_records",
        entity_id=record_id,
        before={f: current[f] for f in FIELDS},
        after=device_data,
    )
    _mark_queue(conn, queue_id, "synced", None, record_id)
    return ApplyResult(record_id, operation, "applied", version, queue_id=queue_id, message=f"Updated {record_id} to v{version}")


def server_update(conn: sqlite3.Connection, record_id: str, changes: dict[str, Any], *, actor_id: str, channel: str = CHANNEL) -> int:
    """An edit made on the server (office) side. Returns the new version."""
    current = record_snapshot(conn, record_id)
    if current is None:
        raise SyncError(f"Record {record_id} does not exist on the server")
    data = _clean(conn, {k: v for k, v in changes.items() if k in FIELDS})
    new_data = {**{f: current[f] for f in FIELDS}, **data}
    version = _update_record(conn, record_id, new_data, actor_id)
    _write_version(conn, record_id, version, new_data, "server", None, actor_id)
    db.audit(
        conn,
        actor_id=actor_id,
        channel=channel,
        action="update",
        entity_table="intake_records",
        entity_id=record_id,
        before={f: current[f] for f in FIELDS},
        after=new_data,
    )
    return version


def resolve_conflict(
    conn: sqlite3.Connection,
    conflict_id: str,
    *,
    choice: str,
    confirmed_by: str | None,
    reason: str,
    field_choices: dict[str, str] | None = None,
    channel: str = CHANNEL,
) -> dict[str, Any]:
    """Resolve an open conflict with a staff choice. Returns the saved data and version.

    choice: 'keep_device', 'keep_server', or 'field_by_field' (field_choices maps every
    differing field to 'device' or 'server'). Requires staff confirmation (caseworker or
    coordinator) and a reason. Refuses if the server record changed again since the
    conflict was detected, so a newer edit is never overwritten.
    """
    staff_id = confirm.require_confirmation(conn, confirmed_by, RESOLVER_ROLES)
    if choice not in CHOICES:
        raise SyncError(f"choice must be one of {', '.join(CHOICES)}")
    if not (reason or "").strip():
        raise SyncError("A reason is required to resolve a conflict")
    detail = conflict_detail(conn, conflict_id)
    if detail["status"] != "open":
        raise SyncError(f"{conflict_id} is already resolved")
    current = record_snapshot(conn, detail["record_id"])
    assert current is not None
    if int(current["version"]) != int(detail["server_version"]):
        raise SyncError(
            f"{detail['record_id']} changed again on the server (now v{current['version']}); sync the device again to see the latest version."
        )
    device, server = detail["device_data"], detail["server_data"]
    if choice == "keep_device":
        resolved = dict(device)
    elif choice == "keep_server":
        resolved = dict(server)
    else:
        field_choices = field_choices or {}
        missing = [f for f in detail["differing_fields"] if field_choices.get(f) not in ("device", "server")]
        if missing:
            raise SyncError(f"Choose device or server for: {', '.join(missing)}")
        resolved = {f: (device if field_choices.get(f) == "device" else server).get(f) for f in FIELDS}

    version = int(current["version"])
    if differing_fields(resolved, server):
        version = _update_record(conn, detail["record_id"], resolved, staff_id)
        _write_version(conn, detail["record_id"], version, resolved, "conflict_resolution", detail["device_id"], staff_id)
    now = clock.iso()
    conn.execute(
        """UPDATE sync_conflicts SET status = 'resolved', resolution = ?, resolved_data_json = ?, resolved_by = ?,
                  resolved_at = ?, reason = ? WHERE conflict_id = ?""",
        (choice, json.dumps(resolved, ensure_ascii=False), staff_id, now, reason.strip(), conflict_id),
    )
    conn.execute("UPDATE sync_queue SET status = 'resolved' WHERE conflict_id = ?", (conflict_id,))
    db.audit(
        conn,
        actor_id=staff_id,
        channel=channel,
        action="resolve",
        entity_table="sync_conflicts",
        entity_id=conflict_id,
        before=server,
        after={"resolution": choice, "data": resolved, "version": version, "field_choices": field_choices},
        reason=reason.strip(),
    )
    return {"conflict_id": conflict_id, "record_id": detail["record_id"], "resolution": choice, "data": resolved, "version": version}


# -- device side (local demo mode) ------------------------------------------
def set_online(conn: sqlite3.Connection, device_id: str, online: bool, *, actor_id: str | None = None) -> None:
    """The 'connection' switch: online devices can sync, offline devices only queue."""
    device = _device(conn, device_id)
    conn.execute("UPDATE sync_devices SET online = ? WHERE device_id = ?", (1 if online else 0, device_id))
    db.audit(
        conn,
        actor_id=actor_id or device["assigned_to"],
        channel=CHANNEL,
        action="connection_on" if online else "connection_off",
        entity_table="sync_devices",
        entity_id=device_id,
        before={"online": device["online"]},
        after={"online": 1 if online else 0},
    )


def enqueue_change(
    conn: sqlite3.Connection,
    *,
    device_id: str,
    operation: str,
    payload: dict[str, Any],
    record_id: str | None = None,
    base_version: int | None = None,
) -> dict[str, Any]:
    """Hold a change in the device's local queue (works with or without a connection).

    For 'create' without a record_id, a device-scoped ID is used (next_device_record_id). For 'update', base_version is
    the server version the device's copy came from.
    """
    _device(conn, device_id)
    data = _clean(conn, payload)
    if operation == "create":
        record_id = record_id or next_device_record_id(conn, device_id)
        if not str(data.get("head_name") or "").strip():
            raise SyncError("A new record needs head_name")
    elif operation == "update":
        if not record_id or base_version is None:
            raise SyncError("An update needs record_id and base_version")
    else:
        raise SyncError(f"Unknown operation {operation!r}")
    cur = conn.execute(
        """INSERT INTO sync_queue(device_id, record_id, operation, payload_json, base_version, queued_at, status)
           VALUES (?,?,?,?,?,?, 'pending')""",
        (device_id, record_id, operation, json.dumps(data, ensure_ascii=False), base_version, clock.iso()),
    )
    return {"queue_id": cur.lastrowid, "record_id": record_id, "operation": operation, "status": "pending"}


def sync_device(conn: sqlite3.Connection, device_id: str, *, actor_id: str | None = None) -> SyncReport:
    """Send every pending queued change of a device, oldest first. Refuses while offline."""
    device = _device(conn, device_id)
    if not device["online"]:
        raise SyncError(f"{device_id} has no connection; changes stay in the local queue.")
    report = SyncReport(device_id=device_id, synced_at=clock.iso())
    pending = conn.execute("SELECT * FROM sync_queue WHERE device_id = ? AND status = 'pending' ORDER BY queue_id", (device_id,)).fetchall()
    for q in pending:
        report.results.append(
            apply_change(
                conn,
                device_id=device_id,
                record_id=q["record_id"],
                operation=q["operation"],
                payload=json.loads(q["payload_json"]),
                base_version=q["base_version"],
                actor_id=actor_id,
                queue_id=q["queue_id"],
            )
        )
    conn.execute("UPDATE sync_devices SET last_sync_at = ? WHERE device_id = ?", (report.synced_at, device_id))
    return report


def queue_status(conn: sqlite3.Connection, device_id: str) -> dict[str, Any]:
    """Online flag, last sync time and queue counts by status for one device."""
    device = _device(conn, device_id)
    counts = {s: 0 for s in ("pending", "synced", "conflict", "resolved")}
    for status, n in conn.execute("SELECT status, COUNT(*) FROM sync_queue WHERE device_id = ? GROUP BY status", (device_id,)):
        counts[status] = n
    return {"device_id": device_id, "online": bool(device["online"]), "last_sync_at": device["last_sync_at"], "counts": counts}
