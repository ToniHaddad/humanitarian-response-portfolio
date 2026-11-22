"""Project 08: offline capture, local queue, sync, and conflicts (Part 4: sync conflicts are
detected and never resolved silently)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import p08_device as dev
import pytest

from shared import clock, confirm, db, sync


def scen(conn: sqlite3.Connection) -> dict:
    return json.loads(db.get_setting("demo.scenarios", conn=conn))["p08"]


def record(conn: sqlite3.Connection, record_id: str) -> dict:
    return dict(conn.execute("SELECT * FROM intake_records WHERE record_id = ?", (record_id,)).fetchone())


def make_conflict(conn: sqlite3.Connection) -> dict:
    """The walkthrough: device offline edits the record, the office edits it too, device syncs."""
    s = scen(conn)
    rid, device_id = s["record_id"], s["device_id"]
    dev.switch_connection(conn, device_id, False)
    copy = dev.device_copy(conn, device_id, rid)
    assert copy is not None
    dev.edit_on_device(conn, device_id, rid, {**copy["data"], "urgency": "urgent", "notes": "Family moved in with relatives."})
    server_size = record(conn, rid)["household_size"] + 1
    dev.edit_on_server(conn, rid, {"household_size": server_size}, actor_id="CW-01")
    dev.switch_connection(conn, device_id, True)
    report = dev.sync_now(conn, device_id)
    assert report.conflicts == 1
    return {"rid": rid, "device_id": device_id, "conflict_id": report.results[0].conflict_id, "server_size": server_size, "base": copy}


# -- scenario and device copy ---------------------------------------------
def test_scenario_ids(conn: sqlite3.Connection) -> None:
    s = scen(conn)
    assert record(conn, s["record_id"])["version"] == s["server_version"]
    assert sync.queue_status(conn, s["offline_device_with_pending_queue"])["online"] is False
    copy = dev.device_copy(conn, s["device_id"], s["record_id"])
    assert copy is not None and copy["base_version"] == s["server_version"] and copy["pending_edits"] == 0
    assert s["record_id"] in dev.device_record_ids(conn, s["device_id"])


def test_device_copy_does_not_see_unsynced_server_edit(conn: sqlite3.Connection) -> None:
    s = scen(conn)
    v = dev.edit_on_server(conn, s["record_id"], {"notes": "Office note"}, actor_id="CW-01")
    assert v == s["server_version"] + 1
    copy = dev.device_copy(conn, s["device_id"], s["record_id"])
    assert copy["base_version"] == s["server_version"]  # the device has not synced since
    assert copy["data"]["notes"] != "Office note"


# -- offline capture and sync ---------------------------------------------
def test_offline_capture_queues_and_sync_refuses_offline(conn: sqlite3.Connection) -> None:
    device_id = scen(conn)["device_id"]
    dev.switch_connection(conn, device_id, False)
    before = sync.queue_status(conn, device_id)
    q = dev.capture_record(
        conn,
        device_id,
        {"head_name": "Nadia Haddad", "area_id": "AR-03", "household_size": 5, "primary_need": "food", "urgency": "high", "notes": ""},
    )
    assert q["status"] == "pending" and q["record_id"] == "INT-DEV01-0001"
    status = sync.queue_status(conn, device_id)
    assert status["counts"]["pending"] == before["counts"]["pending"] + 1
    with pytest.raises(sync.SyncError):
        dev.sync_now(conn, device_id)
    assert sync.record_snapshot(conn, q["record_id"]) is None
    assert sync.queue_status(conn, device_id)["last_sync_at"] == before["last_sync_at"]
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE channel = 'app:08' AND action = 'queue_create'").fetchone()[0] == 1


def test_sync_online_creates_record_and_records_last_sync(conn: sqlite3.Connection) -> None:
    device_id = scen(conn)["device_id"]
    dev.switch_connection(conn, device_id, False)
    q = dev.capture_record(conn, device_id, {"head_name": "Karim Saade", "area_id": "AR-02", "household_size": 3, "urgency": "normal"})
    dev.switch_connection(conn, device_id, True)
    report = dev.sync_now(conn, device_id)
    assert report.applied == 1 and report.conflicts == 0
    rec = record(conn, q["record_id"])
    assert rec["version"] == 1 and rec["source"] == "field_device" and rec["created_by"] == "FW-01"
    assert sync.version_data(conn, q["record_id"], 1)["head_name"] == "Karim Saade"
    status = sync.queue_status(conn, device_id)
    assert status["last_sync_at"] == clock.iso() and status["counts"]["pending"] == 0
    row = conn.execute("SELECT status FROM sync_queue WHERE queue_id = ?", (q["queue_id"],)).fetchone()
    assert row["status"] == "synced"


def test_update_with_matching_base_applies_without_conflict(conn: sqlite3.Connection) -> None:
    s = scen(conn)
    rid, device_id = s["record_id"], s["device_id"]
    dev.switch_connection(conn, device_id, False)
    q = dev.edit_on_device(conn, device_id, rid, {"urgency": "low"})
    assert q["base_version"] == s["server_version"]
    assert dev.device_copy(conn, device_id, rid)["data"]["urgency"] == "low"  # the device shows its own edit
    dev.switch_connection(conn, device_id, True)
    report = dev.sync_now(conn, device_id)
    assert report.applied == 1 and report.conflicts == 0
    rec = record(conn, rid)
    assert rec["version"] == s["server_version"] + 1 and rec["urgency"] == "low"
    assert sync.open_conflicts(conn) == []
    # After the sync the device copy is the new server version.
    assert dev.device_copy(conn, device_id, rid)["base_version"] == rec["version"]


def test_edit_without_change_is_refused(conn: sqlite3.Connection) -> None:
    s = scen(conn)
    copy = dev.device_copy(conn, s["device_id"], s["record_id"])
    with pytest.raises(sync.SyncError):
        dev.edit_on_device(conn, s["device_id"], s["record_id"], copy["data"])
    with pytest.raises(sync.SyncError):
        dev.edit_on_server(conn, s["record_id"], sync.record_snapshot(conn, s["record_id"]), actor_id="CW-01")


def test_seeded_offline_device_queue_syncs_once_online(conn: sqlite3.Connection) -> None:
    device_id = scen(conn)["offline_device_with_pending_queue"]
    status = sync.queue_status(conn, device_id)
    assert status["online"] is False and status["counts"]["pending"] == 2
    with pytest.raises(sync.SyncError):
        dev.sync_now(conn, device_id)
    v30 = record(conn, "INT-0030")["version"]
    dev.switch_connection(conn, device_id, True)
    report = dev.sync_now(conn, device_id)
    assert report.applied == 2 and report.conflicts == 0
    assert record(conn, "INT-DEV03-0001")["version"] == 1
    r30 = record(conn, "INT-0030")
    assert r30["version"] == v30 + 1 and r30["urgency"] == "urgent"
    assert sync.queue_status(conn, device_id)["counts"]["pending"] == 0


# -- conflicts ------------------------------------------------------------
def test_two_place_edit_opens_conflict_and_leaves_server_unchanged(conn: sqlite3.Connection) -> None:
    s = scen(conn)
    c = make_conflict(conn)
    rec = record(conn, c["rid"])
    # The server keeps its own edit (v+1); the device edit was not written over it.
    assert rec["version"] == s["server_version"] + 1
    assert rec["household_size"] == c["server_size"]
    assert rec["notes"] != "Family moved in with relatives."
    q = conn.execute("SELECT * FROM sync_queue WHERE conflict_id = ?", (c["conflict_id"],)).fetchone()
    assert q["status"] == "conflict"
    view = dev.conflict_view(conn, c["conflict_id"])
    assert view["status"] == "open"
    assert set(view["differing_fields"]) == {"household_size", "urgency", "notes"}
    rows = {r["field"]: r for r in view["rows"]}
    assert rows["household_size"]["server"] == c["server_size"] and rows["household_size"]["device"] == c["base"]["data"]["household_size"]
    assert rows["notes"]["device"] == "Family moved in with relatives."
    assert not rows["head_name"]["differs"]
    assert (
        conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'conflict_detected' AND entity_id = ?", (c["conflict_id"],)).fetchone()[
            0
        ]
        == 1
    )


@pytest.mark.parametrize("staff", [None, "", "NOPE-99", "FW-01"])
def test_resolve_without_confirmation_is_refused(conn: sqlite3.Connection, staff: str | None) -> None:
    c = make_conflict(conn)
    before = record(conn, c["rid"])
    with pytest.raises(confirm.ConfirmationRequired):
        sync.resolve_conflict(conn, c["conflict_id"], choice="keep_device", confirmed_by=staff, reason="checked by phone")
    assert record(conn, c["rid"]) == before
    assert dev.conflict_view(conn, c["conflict_id"])["status"] == "open"


def test_resolve_without_reason_is_refused(conn: sqlite3.Connection) -> None:
    c = make_conflict(conn)
    with pytest.raises(sync.SyncError):
        sync.resolve_conflict(conn, c["conflict_id"], choice="keep_device", confirmed_by="CW-01", reason="  ")
    assert dev.conflict_view(conn, c["conflict_id"])["status"] == "open"


def test_field_by_field_needs_every_differing_field(conn: sqlite3.Connection) -> None:
    c = make_conflict(conn)
    with pytest.raises(sync.SyncError):
        sync.resolve_conflict(
            conn, c["conflict_id"], choice="field_by_field", confirmed_by="CW-01", reason="x", field_choices={"urgency": "device"}
        )
    assert dev.conflict_view(conn, c["conflict_id"])["status"] == "open"


def _check_resolved(conn: sqlite3.Connection, c: dict, choice: str, expected_version: int) -> dict:
    conflict = dict(conn.execute("SELECT * FROM sync_conflicts WHERE conflict_id = ?", (c["conflict_id"],)).fetchone())
    assert conflict["status"] == "resolved" and conflict["resolution"] == choice and conflict["resolved_by"] == "CRD-01"
    assert conn.execute("SELECT status FROM sync_queue WHERE conflict_id = ?", (c["conflict_id"],)).fetchone()[0] == "resolved"
    audit = conn.execute("SELECT * FROM audit_log WHERE action = 'resolve' AND entity_id = ?", (c["conflict_id"],)).fetchone()
    assert audit is not None and audit["actor_id"] == "CRD-01" and audit["reason"] == "Checked with the family"
    rec = record(conn, c["rid"])
    assert rec["version"] == expected_version
    return rec


def test_keep_device_saves_device_version(conn: sqlite3.Connection) -> None:
    s = scen(conn)
    c = make_conflict(conn)
    res = sync.resolve_conflict(conn, c["conflict_id"], choice="keep_device", confirmed_by="CRD-01", reason="Checked with the family")
    rec = _check_resolved(conn, c, "keep_device", s["server_version"] + 2)
    assert res["version"] == rec["version"]
    assert rec["notes"] == "Family moved in with relatives." and rec["urgency"] == "urgent"
    assert rec["household_size"] == c["base"]["data"]["household_size"]
    row = conn.execute("SELECT origin FROM record_versions WHERE record_id = ? AND version = ?", (c["rid"], rec["version"])).fetchone()
    assert row["origin"] == "conflict_resolution"


def test_keep_server_keeps_server_version(conn: sqlite3.Connection) -> None:
    s = scen(conn)
    c = make_conflict(conn)
    before = record(conn, c["rid"])
    versions_before = conn.execute("SELECT COUNT(*) FROM record_versions WHERE record_id = ?", (c["rid"],)).fetchone()[0]
    sync.resolve_conflict(conn, c["conflict_id"], choice="keep_server", confirmed_by="CRD-01", reason="Checked with the family")
    rec = _check_resolved(conn, c, "keep_server", s["server_version"] + 1)
    assert {k: rec[k] for k in sync.FIELDS} == {k: before[k] for k in sync.FIELDS}
    # Data did not change, so no new version is written.
    assert conn.execute("SELECT COUNT(*) FROM record_versions WHERE record_id = ?", (c["rid"],)).fetchone()[0] == versions_before


def test_field_by_field_saves_the_chosen_mix(conn: sqlite3.Connection) -> None:
    s = scen(conn)
    c = make_conflict(conn)
    choices = {"household_size": "server", "urgency": "device", "notes": "device"}
    sync.resolve_conflict(
        conn, c["conflict_id"], choice="field_by_field", confirmed_by="CRD-01", reason="Checked with the family", field_choices=choices
    )
    rec = _check_resolved(conn, c, "field_by_field", s["server_version"] + 2)
    assert rec["household_size"] == c["server_size"]
    assert rec["urgency"] == "urgent" and rec["notes"] == "Family moved in with relatives."
    data = sync.version_data(conn, c["rid"], rec["version"])
    assert data["household_size"] == c["server_size"] and data["notes"] == "Family moved in with relatives."
    assert dev.resolved_history(conn)[0]["Conflict"] == c["conflict_id"]


def test_conflicts_never_resolve_on_their_own(conn: sqlite3.Connection) -> None:
    c = make_conflict(conn)
    # Syncing again, and further device edits, never close the conflict.
    report = dev.sync_now(conn, c["device_id"])
    assert report.results == []
    assert [x["conflict_id"] for x in sync.open_conflicts(conn)] == [c["conflict_id"]]
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'resolve' AND entity_id = ?", (c["conflict_id"],)).fetchone()[0] == 0


# -- app ------------------------------------------------------------------
def resolve_button(at):  # noqa: ANN001, ANN201
    return next(b for b in at.button if b.label == "Resolve conflict")


def test_app_runs(app_test) -> None:  # noqa: ANN001
    at = app_test(8).run()
    assert not at.exception
    subheaders = [s.value for s in at.subheader]
    assert any("Conflicts waiting" in s for s in subheaders)
    assert any("Resolved conflicts" in s for s in subheaders)


def test_app_walkthrough_conflict_then_resolve(app_test, db_path: Path) -> None:  # noqa: ANN001
    at = app_test(8).run()
    with db.transaction(db_path) as c:
        s = scen(c)
        version = record(c, s["record_id"])["version"]
    rid, device_id = s["record_id"], s["device_id"]

    # 1. Connection off.
    at.toggle(key=f"p08_online_{device_id}").set_value(False).run()
    assert not at.exception
    with db.transaction(db_path) as c:
        assert sync.queue_status(c, device_id)["online"] is False
    assert at.button(key="p08_sync").disabled

    # 2. Edit on the device (queued with base version).
    prefix = f"p08_dev_{rid}_{version}_0"
    at.selectbox(key=f"{prefix}_urgency").set_value("urgent")
    at.text_area(key=f"{prefix}_notes").input("Family moved in with relatives.")
    at.button(key="p08_dev_edit_submit").click().run()
    assert not at.exception
    with db.transaction(db_path) as c:
        assert sync.queue_status(c, device_id)["counts"]["pending"] == 1

    # 3. Edit on the server.
    sprefix = f"p08_srv_{rid}_{version}"
    size = at.number_input(key=f"{sprefix}_household_size").value
    at.number_input(key=f"{sprefix}_household_size").set_value(size + 1)
    at.button(key="p08_srv_edit_submit").click().run()
    assert not at.exception
    with db.transaction(db_path) as c:
        assert record(c, rid)["version"] == version + 1

    # 4. Connection on, sync: a conflict is shown and the record keeps the server version.
    at.toggle(key=f"p08_online_{device_id}").set_value(True).run()
    at.button(key="p08_sync").click().run()
    assert not at.exception
    with db.transaction(db_path) as c:
        conflicts = sync.open_conflicts(c)
        assert len(conflicts) == 1 and conflicts[0]["record_id"] == rid
        assert record(c, rid)["version"] == version + 1
    cid = conflicts[0]["conflict_id"]
    assert any(cid in m.value for m in at.markdown)
    assert any("conflict" in w.value for w in at.warning)

    # 5. Resolve: without the tick nothing changes; with it, field by field saves.
    at.radio(key=f"p08_choice_{cid}").set_value("field_by_field").run()
    at.radio(key=f"p08_field_{cid}_household_size").set_value("server")
    at.radio(key=f"p08_field_{cid}_urgency").set_value("device")
    at.radio(key=f"p08_field_{cid}_notes").set_value("device")
    at.text_area(key=f"p08_reason_{cid}").input("Visit confirmed size; device notes are newer.").run()
    at.selectbox(key=f"confirm_staff_p08_resolve_{cid}").set_value("CW-01")
    resolve_button(at).click().run()
    assert not at.exception
    with db.transaction(db_path) as c:
        assert len(sync.open_conflicts(c)) == 1
    at.selectbox(key=f"confirm_staff_p08_resolve_{cid}").set_value("CW-01")
    at.checkbox(key=f"confirm_tick_p08_resolve_{cid}").check()
    resolve_button(at).click().run()
    assert not at.exception
    with db.transaction(db_path) as c:
        assert sync.open_conflicts(c) == []
        rec = record(c, rid)
        assert rec["version"] == version + 2 and rec["household_size"] == size + 1 and rec["urgency"] == "urgent"
        assert c.execute("SELECT resolved_by FROM sync_conflicts WHERE conflict_id = ?", (cid,)).fetchone()[0] == "CW-01"


def test_seeded_conflict_is_a_real_conflict(conn: sqlite3.Connection) -> None:
    """The seeded past conflict must have been a two-place edit: the device's base is older than the server version."""
    rows = conn.execute("SELECT base_version, server_version FROM sync_conflicts").fetchall()
    assert rows
    assert all(r["base_version"] < r["server_version"] for r in rows)
