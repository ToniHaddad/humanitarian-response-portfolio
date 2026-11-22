import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p02_calls as p02  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from shared import charts, clock, confirm, db, ui  # noqa: E402

ui.page_header(project_number=2)


def done(message: str) -> None:
    """Keep a success message across the rerun that refreshes the figures and timeline."""
    st.session_state["p02_flash"] = message
    st.rerun()


if "p02_flash" in st.session_state:
    st.success(st.session_state.pop("p02_flash"))

operator = ui.staff_selector(roles=("operator",), key="p02_operator", label="Operator (logging calls)")
handler = ui.staff_selector(roles=("caseworker", "coordinator"), key="p02_handler", label="Caseworker or coordinator (complaints)")
st.sidebar.caption(f"Demo clock: {clock.now():%Y-%m-%d %H:%M}")

# ---------------------------------------------------------------- figures
ui.section("Figures", "Calls come only from the calls table, complaints only from the complaints table.")
period_label = st.radio("Period", list(p02.PERIODS), horizontal=True, key="p02_period")
days = p02.PERIODS[period_label]
with db.connect() as conn:
    fig = p02.period_figures(conn, days)
    topics = p02.recurring_topics(conn, days)
    area_rows = db.query_all("SELECT area_id, name_en FROM areas ORDER BY area_id", conn=conn)

st.caption(f"Period: {fig['start']} to {fig['end']} (both days included). Open complaints are counted now, not per period.")
ui.metric_row(
    [
        ("Calls received (count)", fig["calls_received"], "Every incoming call of every type, repeat calls included."),
        ("New complaints (count)", fig["new_complaints"], "Complaint rows opened in the period. Repeat calls never add one."),
        ("Open complaints (count)", fig["open_complaints"], "Complaints not closed, right now."),
    ]
)
left, right = st.columns(2)
with left:
    by_type = pd.DataFrame({"call_type": list(fig["calls_by_type"]), "calls": list(fig["calls_by_type"].values())})
    types_fig = charts.bar(by_type, "call_type", "calls", "Call type", "Calls (count)", title="Calls received by type")
    types_fig.update_yaxes(tickformat="d", dtick=max(1, -(-int(by_type["calls"].max() or 1) // 6)), rangemode="tozero")
    types_fig.update_xaxes(automargin=True, title_standoff=12)
    types_fig.update_layout(margin={"b": 110})
    st.plotly_chart(types_fig, width="stretch", key="p02_chart_types")
with right:
    st.markdown("**Recurring complaint topics**")
    st.caption(
        f"A topic is recurring when {p02.RECURRING_MIN_COMPLAINTS} or more complaints were opened in the period. "
        "Calls = calls in the period about complaints of that topic; repeat calls = those marked as repeats."
    )
    ui.table(topics.rename(columns={"complaints": "complaints (count)", "calls": "calls (count)", "repeat_calls": "repeat calls (count)"}))

# ---------------------------------------------------------------- log a call
ui.section("Log a call", "Every incoming call is logged here, including information inquiries.")
with db.connect() as conn:
    pending = p02.open_complaints(conn).set_index("complaint_id")
    open_reqs = p02.open_requests(conn)
area_names = {a["area_id"]: f"{a['area_id']} {a['name_en']}" for a in area_rows}
call_type = st.selectbox("Call type", p02.CALL_TYPES, format_func=lambda t: t.replace("_", " "), key="p02_call_type")

link_mode = None
if call_type == "complaint":
    link_mode = st.radio("Complaint link", ["New complaint", "Existing open complaint"], horizontal=True, key="p02_link_c")
elif call_type == "service_request":
    link_mode = st.radio("Request link", ["New service request", "Existing open request"], horizontal=True, key="p02_link_r")
elif call_type == "follow_up":
    link_mode = st.radio("Follow-up about", ["Existing complaint", "Existing request"], horizontal=True, key="p02_link_f")

with st.form("p02_log_call", clear_on_submit=True):
    c1, c2, c3 = st.columns(3)
    language = c1.selectbox("Language", ["ar", "en"], key="p02_lang")
    area_id = c2.selectbox("Area", list(area_names), format_func=lambda a: area_names[a], key="p02_area")
    duration = c3.number_input("Duration (seconds)", min_value=0, max_value=7200, value=180, step=30, key="p02_dur")
    household = st.text_input("Household ID (optional, e.g. HH-00042)", key="p02_hh")
    notes = st.text_area("Call notes", key="p02_notes")
    topic = description = complaint_pick = request_pick = None
    if link_mode == "New complaint":
        topic = st.selectbox("Complaint topic", p02.TOPICS, key="p02_topic")
        description = st.text_area("Complaint description (defaults to the call notes)", key="p02_desc")
    if link_mode in ("Existing open complaint", "Existing complaint"):
        complaint_pick = st.selectbox(
            "Existing open complaint",
            list(pending.index),
            format_func=lambda i: f"{i} · {pending.loc[i, 'topic']} · {pending.loc[i, 'calls']} calls",
            key="p02_pick_c",
        )
    if link_mode in ("Existing open request", "Existing request"):
        request_pick = st.selectbox("Existing open request", list(open_reqs["request_id"]), key="p02_pick_r")
    submitted = st.form_submit_button("Log call", type="primary")

if submitted:
    try:
        with db.transaction() as conn:
            result = p02.log_call(
                conn,
                operator_id=operator or "",
                call_type=call_type,
                language=language,
                area_id=area_id,
                notes=notes,
                household_id=household,
                duration_sec=int(duration),
                complaint_id=complaint_pick,
                new_complaint_topic=topic,
                new_complaint_description=description,
                request_id=request_pick,
                new_request=link_mode == "New service request",
            )
        parts = [f"Call {result['call_id']} logged."]
        kind = "repeat call" if result["is_repeat"] else "new"
        if result["complaint_id"]:
            parts.append(f"Complaint {result['complaint_id']} ({kind}).")
        if result["request_id"]:
            parts.append(f"Request {result['request_id']} ({kind}; its category is confirmed in Project 01).")
        done(" ".join(parts))
    except p02.CallError as exc:
        st.error(str(exc))

st.markdown("**Latest calls**")
with db.connect() as conn:
    latest = p02.recent_calls(conn)
ui.table(latest.drop(columns=["notes"]))

# ---------------------------------------------------------------- complaint handling
ui.section("Complaint handling", "Assign an owner, record follow-up, close with a resolution. Closing needs staff confirmation.")
with db.connect() as conn:
    all_ids = [r["complaint_id"] for r in db.query_all("SELECT complaint_id FROM complaints ORDER BY complaint_id DESC", conn=conn)]
selected = st.selectbox("Complaint", all_ids, key="p02_selected")
if selected:
    with db.connect() as conn:
        row = db.row_dict(conn, "complaints", "complaint_id", selected) or {}
        n_calls = p02.complaint_call_count(conn, selected)
        history = p02.timeline(conn, selected)
    ui.metric_row(
        [("Calls on this complaint (count)", n_calls), ("Status", row.get("status", "")), ("Owner", row.get("owner_id") or "none")]
    )
    ui.show_text(row.get("description"), f"{row.get('topic')} · opened {row.get('opened_at')}")
    if row.get("resolution"):
        ui.show_text(row["resolution"], "Resolution")
    st.markdown("**Timeline**")
    ui.table(history)

    if row.get("status") != "closed":
        a1, a2 = st.columns(2)
        with a1, st.form("p02_assign"):
            owners = [o["staff_id"] for o in ui.staff_options(p02.OWNER_ROLES)]
            owner = st.selectbox("Owner", owners, key="p02_owner")
            if st.form_submit_button("Assign owner"):
                try:
                    with db.transaction() as conn:
                        p02.assign_owner(conn, selected, owner, handler or owner)
                    done(f"{owner} now owns {selected}; status in progress.")
                except p02.CallError as exc:
                    st.error(str(exc))
        with a2, st.form("p02_followup", clear_on_submit=True):
            action = st.selectbox("Follow-up action", p02.FOLLOWUP_ACTIONS, format_func=lambda a: a.replace("_", " "), key="p02_action")
            note = st.text_input("Note", key="p02_fnote")
            if st.form_submit_button("Record follow-up"):
                try:
                    with db.transaction() as conn:
                        p02.record_followup(conn, selected, handler or "", action, note)
                    done("Follow-up recorded.")
                except p02.CallError as exc:
                    st.error(str(exc))
        resolution = st.text_input("Resolution (required to close)", key="p02_resolution")
        staff = ui.confirm_action(
            f"close_{selected}",
            f"Close complaint **{selected}** with this resolution: {resolution or '(none entered)'}",
            roles=p02.CLOSE_ROLES,
            button_label="Close complaint",
            default_staff=handler,
        )
        if staff:
            try:
                with db.transaction() as conn:
                    p02.close_complaint(conn, selected, resolution, confirmed_by=staff)
                done(f"{selected} closed.")
            except (p02.CallError, confirm.ConfirmationRequired) as exc:
                st.error(str(exc))
