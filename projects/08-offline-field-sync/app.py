import html
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p08_device as dev  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from shared import call_notes, clock, confirm, db, sync, ui  # noqa: E402

ui.page_header(project_number=8)

with db.transaction() as _conn:
    scen = dev.scenario(_conn)
    device_rows = {d["device_id"]: d for d in dev.devices(_conn)}
    areas = {r["area_id"]: r["name_en"] for r in _conn.execute("SELECT area_id, name_en FROM areas ORDER BY area_id")}

# -- sidebar: device, connection switch, acting staff -------------------------
device_ids = list(device_rows)
default_device = scen.get("device_id", device_ids[0])
device_id = st.sidebar.selectbox(
    "Field device",
    device_ids,
    index=device_ids.index(default_device) if default_device in device_ids else 0,
    format_func=lambda d: f"{d} · {device_rows[d]['assigned_to']}",
    key="p08_device",
)
online_key = f"p08_online_{device_id}"
st.session_state[online_key] = bool(device_rows[device_id]["online"])


def _switch() -> None:
    with db.transaction() as conn:
        dev.switch_connection(conn, device_id, bool(st.session_state[online_key]))


st.sidebar.toggle("Connection (simulated)", key=online_key, on_change=_switch, help="Turn off to simulate losing the connection.")
st.sidebar.caption("Off: the device only queues changes. On: the device can sync.")
acting = ui.staff_selector(
    roles=("caseworker", "coordinator", "field_worker"), key="p08_staff", label="Acting staff (office edits, conflict choices)"
)

if "p08_flash" in st.session_state:
    kind, text = st.session_state.pop("p08_flash")
    getattr(st, kind)(text)

with db.transaction() as _conn:
    status = sync.queue_status(_conn, device_id)
    queue = dev.queue_rows(_conn, device_id)
    device_records = dev.device_record_ids(_conn, device_id)
    conflicts = [dev.conflict_view(_conn, c["conflict_id"]) for c in sync.open_conflicts(_conn)]
    history = dev.resolved_history(_conn)

online = status["online"]
conn_badge = ui.badge("ONLINE", "ok") if online else ui.badge("NO CONNECTION", "bad")
last_sync_label = clock.parse(status["last_sync_at"]).strftime("%Y-%m-%d %H:%M") if status["last_sync_at"] else "never"
st.markdown(
    f"**{html.escape(device_id)}** connection: {conn_badge} &nbsp; · &nbsp; **Last sync:** {html.escape(last_sync_label)}",
    unsafe_allow_html=True,
)
counts = status["counts"]
ui.metric_row(
    [
        ("Pending in queue (changes)", counts["pending"]),
        ("Synced (changes)", counts["synced"]),
        ("In conflict (changes)", counts["conflict"]),
        ("Resolved (changes)", counts["resolved"]),
    ]
)

with st.expander("How sync and conflict detection work", expanded=False):
    st.markdown(
        """
**Rule-based version check (optimistic concurrency), a straightforward calculation.**
Every record has a version number. When the device edits its copy, the queued change carries the
**base version**: the server version the device's copy came from. At sync time the server compares it
with its current version:

* base version = server version: nobody else changed the record, the edit is saved as the next version;
* base version ≠ server version: the record was edited in two places. The record is **left unchanged**,
  both versions are stored as a conflict, and a caseworker or coordinator must choose
  (keep device, keep server, or field by field) with a reason and a confirmation.

New records captured offline get a device-scoped ID (INT-DEV01-0001) and become version 1 on the server.
Nothing is merged or overwritten automatically.
        """
    )


def record_form_fields(prefix: str, values: dict[str, Any]) -> dict[str, Any]:
    """The intake fields as form widgets, prefilled with values."""
    needs = list(call_notes.CATEGORIES)
    area_ids = list(areas)
    out: dict[str, Any] = {}
    out["head_name"] = st.text_input("Head of household", value=values.get("head_name") or "", key=f"{prefix}_head_name")
    c1, c2 = st.columns(2)
    out["area_id"] = c1.selectbox(
        "Area",
        area_ids,
        index=area_ids.index(values["area_id"]) if values.get("area_id") in area_ids else 0,
        format_func=lambda a: f"{a} · {areas[a]}",
        key=f"{prefix}_area_id",
    )
    out["household_size"] = int(
        c2.number_input(
            "Household size (people)",
            min_value=1,
            max_value=30,
            value=int(values.get("household_size") or 1),
            key=f"{prefix}_household_size",
        )
    )
    c3, c4 = st.columns(2)
    out["primary_need"] = c3.selectbox(
        "Primary need",
        needs,
        index=needs.index(values["primary_need"]) if values.get("primary_need") in needs else 0,
        format_func=lambda n: call_notes.CATEGORY_LABELS.get(n, n),
        key=f"{prefix}_primary_need",
    )
    out["urgency"] = c4.selectbox(
        "Urgency",
        list(sync.URGENCIES),
        index=list(sync.URGENCIES).index(values["urgency"]) if values.get("urgency") in sync.URGENCIES else 1,
        key=f"{prefix}_urgency",
    )
    out["notes"] = st.text_area("Notes", value=values.get("notes") or "", key=f"{prefix}_notes", height=80)
    return out


def flash(kind: str, text: str) -> None:
    st.session_state["p08_flash"] = (kind, text)
    st.rerun()


device_col, server_col = st.columns(2, gap="large")

# -- device panel -------------------------------------------------------------
with device_col:
    ui.section(f"Device {device_id}", "What the field worker's tablet holds. Works with or without a connection.")
    if st.button(
        "Sync now" if online else "Sync now (no connection)",
        type="primary",
        disabled=not online,
        key="p08_sync",
        help=None if online else "The device has no connection: changes stay in the local queue.",
    ):
        try:
            with db.transaction() as conn:
                report = dev.sync_now(conn, device_id)
            parts = [r.message for r in report.results] or ["nothing was pending"]
            kind = "warning" if report.conflicts else "success"
            flash(kind, f"Synced {device_id}: {report.applied} applied, {report.conflicts} conflict(s). " + " · ".join(parts))
        except sync.SyncError as exc:
            st.warning(str(exc))
    if not online:
        st.info(f"{device_id} has no connection; changes stay in the local queue until the connection returns.")

    with st.expander("Capture a new intake record", expanded=False):
        with st.form("p08_capture", clear_on_submit=True):
            new_fields = record_form_fields("p08_new", {"urgency": "normal"})
            captured = st.form_submit_button("Save to device queue", key="p08_capture_submit")
        if captured:
            try:
                with db.transaction() as conn:
                    q = dev.capture_record(conn, device_id, new_fields)
                flash("success", f"{q['record_id']} saved on {device_id} and queued (pending create).")
            except sync.SyncError as exc:
                st.error(f"Not saved: {exc}")

    st.markdown("**Edit a record on the device**")
    if not device_records:
        st.caption("This device holds no server records yet.")
    else:
        default_rec = scen.get("record_id")
        rec_id = st.selectbox(
            "Record on the device",
            device_records,
            index=device_records.index(default_rec) if default_rec in device_records else 0,
            key="p08_dev_record",
        )
        with db.transaction() as _conn:
            copy = dev.device_copy(_conn, device_id, rec_id)
        if copy:
            pend = f", {copy['pending_edits']} pending edit(s) on top" if copy["pending_edits"] else ""
            st.caption(f"Device copy of {rec_id} is based on server version v{copy['base_version']}{pend}.")
            with st.form(f"p08_dev_edit_{rec_id}"):
                dev_fields = record_form_fields(f"p08_dev_{rec_id}_{copy['base_version']}_{copy['pending_edits']}", copy["data"])
                dev_submit = st.form_submit_button("Save edit to device queue", key="p08_dev_edit_submit")
            if dev_submit:
                try:
                    with db.transaction() as conn:
                        q = dev.edit_on_device(conn, device_id, rec_id, dev_fields)
                    flash(
                        "success", f"Edit of {rec_id} queued on {device_id} (base version v{q['base_version']}): {', '.join(q['changes'])}."
                    )
                except sync.SyncError as exc:
                    st.error(f"Not saved: {exc}")

    st.markdown("**Local queue**")
    if queue:
        ui.table(pd.DataFrame(queue), height=240)
    else:
        st.caption("The queue is empty.")

# -- server panel -------------------------------------------------------------
with server_col:
    ui.section("Server copy", "The office's copy of a record. An edit here while the device is offline produces a two-place edit.")
    with db.transaction() as _conn:
        server_ids = [r["record_id"] for r in _conn.execute("SELECT record_id FROM intake_records ORDER BY record_id")]
    default_srv = scen.get("record_id")
    srv_id = st.selectbox(
        "Record on the server",
        server_ids,
        index=server_ids.index(default_srv) if default_srv in server_ids else 0,
        key="p08_srv_record",
    )
    with db.transaction() as _conn:
        srv = db.row_dict(_conn, "intake_records", "record_id", srv_id) or {}
        srv_open = [c for c in conflicts if c["record_id"] == srv_id]
    st.caption(f"{srv_id} is at server version v{srv.get('version')}, last saved {srv.get('updated_at')} by {srv.get('updated_by')}.")
    if srv_open:
        st.warning(
            f"{srv_id} has an open conflict ({', '.join(c['conflict_id'] for c in srv_open)}): it keeps this server version until staff choose."
        )
    with st.form(f"p08_srv_edit_{srv_id}"):
        srv_fields = record_form_fields(f"p08_srv_{srv_id}_{srv.get('version')}", srv)
        srv_submit = st.form_submit_button("Save edit on server", key="p08_srv_edit_submit")
    if srv_submit:
        try:
            with db.transaction() as conn:
                version = dev.edit_on_server(conn, srv_id, srv_fields, actor_id=acting or "system")
            flash("success", f"{srv_id} saved on the server as version v{version}.")
        except sync.SyncError as exc:
            st.error(f"Not saved: {exc}")

# -- conflicts ----------------------------------------------------------------
ui.section(
    "Conflicts waiting for a staff choice",
    "A record edited on a device and on the server. Nothing saves until a caseworker or coordinator chooses.",
)
if not conflicts:
    st.success("No open conflicts.")


def _value(field: str, value: Any) -> str:
    if value is None or value == "":
        return "<em>(empty)</em>"
    shown = ui.bidi_html(str(value))
    if field == "area_id" and value in areas:
        shown += f" <small>({html.escape(areas[value])})</small>"
    if field == "primary_need":
        shown = html.escape(call_notes.CATEGORY_LABELS.get(str(value), str(value)))
    return shown


for c in conflicts:
    cid = c["conflict_id"]
    with st.container(border=True):
        st.markdown(
            f"**{cid}** · record **{c['record_id']}** · device {c['device_id']} edited from v{c['base_version']}, "
            f"server is at v{c['server_version']} · detected {c['detected_at']} {ui.badge('OPEN', 'warn')}",
            unsafe_allow_html=True,
        )
        body = "".join(
            f'<tr style="{"background:#FFF1D6" if r["differs"] else ""}">'
            f"<td><strong>{html.escape(r['label'])}</strong></td>"
            f"<td>{_value(r['field'], r['device'])}</td><td>{_value(r['field'], r['server'])}</td>"
            f"<td><small>{_value(r['field'], r['base'])}</small></td>"
            f"<td>{ui.badge('differs', 'warn') if r['differs'] else ui.badge('same', 'ok')}</td></tr>"
            for r in c["rows"]
        )
        st.markdown(
            '<table style="width:100%;border-collapse:collapse" border="1" cellpadding="6">'
            f"<thead><tr><th>Field</th><th>Device version ({html.escape(c['device_id'])})</th>"
            f"<th>Server version (v{c['server_version']})</th><th>Both started from (v{c['base_version']})</th><th></th></tr></thead>"
            f"<tbody>{body}</tbody></table>",
            unsafe_allow_html=True,
        )
        choice = st.radio(
            "Choice",
            list(sync.CHOICES),
            format_func=lambda x: {
                "keep_device": "Keep device version",
                "keep_server": "Keep server version",
                "field_by_field": "Choose field by field",
            }[x],
            horizontal=True,
            key=f"p08_choice_{cid}",
        )
        field_choices: dict[str, str] = {}
        if choice == "field_by_field":
            for r in c["rows"]:
                if r["differs"]:
                    field_choices[r["field"]] = st.radio(
                        r["label"],
                        ["device", "server"],
                        format_func=lambda s, row=r: f"{s}: {row[s]}",
                        horizontal=True,
                        key=f"p08_field_{cid}_{r['field']}",
                    )
        reason = st.text_area("Reason for the choice (required)", key=f"p08_reason_{cid}")
        staff = ui.confirm_action(
            f"p08_resolve_{cid}",
            f"Resolve **{cid}** for **{c['record_id']}**: {choice.replace('_', ' ')}. The chosen data is saved as the record's next version "
            "(nothing changes if the server version is kept).",
            roles=sync.RESOLVER_ROLES,
            button_label="Resolve conflict",
            default_staff=acting,
        )
        if staff:
            try:
                with db.transaction() as conn:
                    res = sync.resolve_conflict(
                        conn,
                        cid,
                        choice=choice,
                        confirmed_by=staff,
                        reason=reason,
                        field_choices=field_choices or None,
                        channel=dev.CHANNEL,
                    )
                flash(
                    "success",
                    f"{cid} resolved ({choice.replace('_', ' ')}) by {staff}: {res['record_id']} is at version v{res['version']}.",
                )
            except (sync.SyncError, confirm.ConfirmationRequired) as exc:
                st.error(f"Nothing was changed: {exc}")

# -- history ------------------------------------------------------------------
ui.section("Resolved conflicts", "Who chose what, when and why.")
if history:
    ui.table(pd.DataFrame(history))
else:
    st.caption("No resolved conflicts yet.")
