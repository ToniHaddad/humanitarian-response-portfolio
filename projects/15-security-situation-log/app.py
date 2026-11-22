import sys
from datetime import datetime, time, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p15_security as p15  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from shared import charts, clock, db, security, ui  # noqa: E402

ui.page_header(project_number=15)

NOW = clock.now()
STATUS_COLORS = {"verified": "#0072B2", "unverified": "#E69F00", "discounted": "#7F7F7F"}


def level_label(level: int | None) -> str:
    return "No decision" if level is None else f"{level} {security.LEVEL_NAMES[level]}"


acting = ui.staff_selector(("duty_officer", "field_worker"), key="p15_staff", label="Acting as (logging and verifying)")
st.sidebar.caption(f"Demo clock: {NOW:%Y-%m-%d %H:%M}")

with st.expander("How the suggested advisory level is worked out", expanded=False):
    st.markdown(
        """
Rule version 1, the same rule for every area:

- **Only verified incidents count.** Unverified and discounted reports are ignored.
- Look back **7 days** from now, by the time the incident occurred.
- Score = sum of severity (1 to 5). Incidents in the **last 48 hours count in full (weight 1.0)**, older ones count **half (0.5)**.
- Score **3 or more**: level 1 Caution. **6 or more**: level 2 Restricted. **12 or more**: level 3 No movement. Otherwise level 0 Normal.
- The duty officer **confirms** the suggestion or **overrides** it with a written reason.
- An area at level 2 or higher blocks convoy stops in the convoy planner (Project 16).
        """
    )

if "p15_flash" in st.session_state:
    st.success(st.session_state.pop("p15_flash"))

with db.transaction() as _conn:
    board = p15.status_board(_conn, NOW)

# -- status board ---------------------------------------------------------
ui.section("Area status board", "Current advisory, what the rule suggests now, and the reports still waiting for checking.")
differing = [r["area_id"] for r in board if r["differs"]]
ui.metric_row(
    [
        ("Areas", len(board)),
        ("Restricted or worse (count)", sum(1 for r in board if (r["current_level"] or 0) >= security.BLOCKING_LEVEL)),
        ("Differs from advisory (areas)", len(differing)),
        ("Unverified reports in window (count)", sum(r["unverified_in_window"] for r in board)),
    ]
)
board_df = pd.DataFrame(
    [
        {
            "Area": f"{r['area_id']} {r['area']}",
            "Current advisory": r["current_name"] + (" (override)" if r["is_override"] else ""),
            "Override reason": r["override_reason"] or "",
            "Decided by": r["decided_by"] or "",
            "Decided at": r["decided_at"] or "",
            "Rule suggests now": f"{r['suggested_level']} {r['suggested_name']}",
            "Score (weighted severity)": r["score"],
            "Verified in 7 days (count)": r["verified_in_window"],
            "Unverified, kept apart (count)": r["unverified_in_window"],
            "Differs": "DIFFERS" if r["differs"] else "",
        }
        for r in board
    ]
)
ui.table(board_df)
if differing:
    st.warning("Suggestion differs from the current advisory in: " + ", ".join(differing) + ". Decide below.")

# -- advisory decision ----------------------------------------------------
ui.section("Advisory decision", "Duty officers confirm the suggested level or override it with a reason.")
by_id = {r["area_id"]: r for r in board}
area_choice = st.selectbox("Area", list(by_id), format_func=lambda a: f"{a} {by_id[a]['area']}", key="p15_decide_area")
row = by_id[area_choice]
with db.transaction() as _conn:
    sug = p15.suggestion_for_area(_conn, area_choice, NOW)
st.markdown(
    f"Current advisory: **{level_label(row['current_level'])}**. Rule suggests: **{level_label(row['suggested_level'])}** "
    f"(score {row['score']}, verified incidents used: {', '.join(sug['incidents']) or 'none'})."
)
mode = st.radio("Decision", ["Confirm the suggested level", "Override with another level"], key="p15_mode", horizontal=True)
final_level = row["suggested_level"]
reason = ""
if mode.startswith("Override"):
    final_level = st.selectbox(
        "Final level", list(security.LEVEL_NAMES), index=row["suggested_level"], format_func=level_label, key="p15_final_level"
    )
    reason = st.text_area("Reason for the override (required)", key="p15_reason")
summary = (
    f"Area **{area_choice} {by_id[area_choice]['area']}**: set the advisory to **{level_label(final_level)}** "
    f"({'override, reason: ' + (reason.strip() or 'MISSING') if mode.startswith('Override') else 'confirms the rule suggestion'})."
)
staff = ui.confirm_action("p15_advisory", summary, roles=p15.DUTY_ROLES, button_label="Record advisory decision", default_staff="DO-01")
if staff:
    try:
        with db.transaction() as conn:
            adv_id = p15.decide_advisory(conn, area_choice, int(final_level), confirmed_by=staff, override_reason=reason)
        st.session_state["p15_flash"] = f"Recorded {adv_id}: {area_choice} is now {level_label(int(final_level))}."
        st.rerun()
    except Exception as exc:  # reason missing, not a duty officer, etc.: show, never crash the page
        st.error(str(exc))

# -- timeline -------------------------------------------------------------
ui.section("Incident timeline", "Verified, unverified and discounted reports are shown separately. Only verified incidents feed the rule.")
days = st.slider("Days shown", 3, 30, 7, key="p15_days")
since = (NOW - timedelta(days=days)).strftime(clock.ISO)
inc_df = db.query_df(
    """SELECT i.incident_id, i.occurred_at, i.area_id, i.incident_type, i.severity, i.source, i.verification_status,
              i.verified_by, i.verified_at, i.description
       FROM incidents i WHERE i.occurred_at >= ? ORDER BY i.occurred_at DESC""",
    (since,),
)
if not inc_df.empty:
    inc_df["occurred_at"] = pd.to_datetime(inc_df["occurred_at"])
tabs = st.tabs(["Verified", "Unverified (not counted)", "Discounted (not counted)"])
for tab, status in zip(tabs, ("verified", "unverified", "discounted"), strict=True):
    with tab:
        part = inc_df[inc_df["verification_status"] == status] if not inc_df.empty else inc_df
        st.caption(f"{len(part)} {status} incident(s) in the last {days} days.")
        if part.empty:
            continue
        fig = px.scatter(
            part,
            x="occurred_at",
            y="area_id",
            size="severity",
            color_discrete_sequence=[STATUS_COLORS[status]],
            hover_data=["incident_id", "incident_type", "severity", "source"],
        )
        charts.apply_defaults(fig, "Time occurred", "Area", height=330)
        st.plotly_chart(fig, width="stretch", key=f"p15_chart_{status}")
        ui.table(part.drop(columns=[] if status != "unverified" else ["verified_by", "verified_at"]))

# -- verify or discount ---------------------------------------------------
ui.section("Check reports", "A duty officer verifies or discounts an unverified report. Use the sidebar to choose who is acting.")
pending = db.query_all(
    "SELECT incident_id, area_id, incident_type, severity, source, occurred_at, description FROM incidents "
    "WHERE verification_status = 'unverified' AND occurred_at >= ? ORDER BY occurred_at DESC",
    ((NOW - timedelta(days=security.LOOKBACK_DAYS)).strftime(clock.ISO),),
)
if not pending:
    st.info("No unverified reports in the last 7 days.")
else:
    labels = {
        r[
            "incident_id"
        ]: f"{r['incident_id']} · {r['area_id']} · {r['incident_type']} · severity {r['severity']} · {r['source']} · {r['occurred_at']}"
        for r in pending
    }
    chosen = st.selectbox("Unverified report", list(labels), format_func=lambda i: labels[i], key="p15_pending")
    new_status = st.radio("Outcome", ["verified", "discounted"], horizontal=True, key="p15_outcome")
    if st.button("Save outcome", key="p15_save_outcome"):
        try:
            with db.transaction() as conn:
                p15.set_verification(conn, chosen, new_status, confirmed_by=acting or "")
            st.session_state["p15_flash"] = f"{chosen} marked {new_status}. The board above now uses it."
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

# -- log a new incident ---------------------------------------------------
ui.section("Log an incident", "New reports start as unverified and do not change any suggestion until a duty officer verifies them.")
with st.form("p15_log_form", clear_on_submit=False):
    c1, c2, c3 = st.columns(3)
    f_area = c1.selectbox("Area", list(by_id), format_func=lambda a: f"{a} {by_id[a]['area']}", key="p15_f_area")
    f_type = c2.selectbox("Type", p15.INCIDENT_TYPE_LIST, key="p15_f_type")
    f_sev = c3.slider("Severity (1 to 5)", 1, 5, 3, key="p15_f_sev")
    c4, c5, c6 = st.columns(3)
    f_source = c4.selectbox("Source", p15.SOURCES, key="p15_f_source")
    f_date = c5.date_input("Date occurred", value=NOW.date(), key="p15_f_date")
    f_time = c6.time_input("Time occurred", value=time(NOW.hour, NOW.minute), key="p15_f_time")
    f_desc = st.text_area("Description", key="p15_f_desc")
    submitted = st.form_submit_button("Log incident (unverified)")
if submitted:
    try:
        with db.transaction() as conn:
            new_id = p15.log_incident(
                conn,
                area_id=f_area,
                incident_type=f_type,
                severity=f_sev,
                source=f_source,
                occurred_at=datetime.combine(f_date, f_time),
                description=f_desc,
                reported_by=acting or "",
            )
        st.success(f"Logged {new_id} as unverified.")
    except Exception as exc:
        st.error(str(exc))
