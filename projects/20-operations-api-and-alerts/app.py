import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p20_ops as p20  # noqa: E402
import streamlit as st  # noqa: E402

from shared import alerts, auth, clock, db, fleet, ports, scheduler, simulator, ui  # noqa: E402

ui.page_header(project_number=20)
auth.require_staff_login()

APP_CHANNEL = "app:20"
staff_id = ui.staff_selector(key="p20_staff")
with p20.read() as _c:
    role = db.staff_role(_c, staff_id) if staff_id else None
st.sidebar.caption(f"Role: {(role or 'none').replace('_', ' ')}")
st.sidebar.caption(f"Clock: {clock.now():%Y-%m-%d %H:%M:%S}")


def allowed(action: str) -> bool:
    return auth.role_allowed(role, action)


def needs(action: str) -> str:
    return "Allowed roles: " + ", ".join(r.replace("_", " ") for r in auth.ROLES[action])


def done(message: str) -> None:
    st.session_state["p20_flash"] = message
    st.rerun()


if "p20_flash" in st.session_state:
    st.success(st.session_state.pop("p20_flash"))

with p20.read() as conn:
    counts = p20.alert_counts(conn)
    services_df, jobs_df, healthy = p20.health(conn)

ui.metric_row(
    [
        ("Open alerts (count)", counts["open"]),
        ("Acknowledged alerts (count)", counts["acknowledged"]),
        ("Jobs overdue (count)", int(jobs_df["overdue"].sum())),
        ("Services silent or not ok (count)", int((services_df["silent"] | (services_df["status"] != "ok")).sum())),
    ]
)

tab_alerts, tab_jobs, tab_health, tab_audit, tab_api = st.tabs(["Alerts", "Jobs and simulator", "Health", "Audit log", "API"])

# -- alerts ---------------------------------------------------------------------
with tab_alerts:
    ui.section(
        "Alert rules",
        "Evaluated on a schedule by the alert_evaluation job, or now with the button. Thresholds can be changed here or through the API.",
    )
    with p20.read() as conn:
        st.dataframe(p20.rules_frame(conn), hide_index=True, width="stretch")
    if st.button("Evaluate alert rules now", type="primary", disabled=not allowed("evaluate"), help=needs("evaluate")):
        with db.transaction() as conn:
            result = alerts.evaluate_rules(conn, channel=APP_CHANNEL)
        done(f"Alert rules evaluated: {result.summary()}.")

    with st.expander("Change a rule's threshold or switch it off"):
        with p20.read() as conn:
            rules = {r["rule_id"]: r for r in alerts.list_rules(conn)}
        rule_id = st.selectbox("Rule", list(rules), format_func=lambda r: f"{r} · {rules[r]['name']}", key="p20_rule")
        rule = rules[rule_id]
        new_threshold = st.number_input("Threshold", min_value=0.0, value=float(rule["threshold"]), step=1.0, key=f"p20_thr_{rule_id}")
        new_enabled = st.checkbox("Enabled", value=bool(rule["enabled"]), key=f"p20_en_{rule_id}")
        if st.button("Save rule", disabled=not allowed("rules"), help=needs("rules")):
            with db.transaction() as conn:
                alerts.update_rule(conn, rule_id, str(staff_id), threshold=new_threshold, enabled=new_enabled, channel=APP_CHANNEL)
            done(f"{rule_id} saved: threshold {new_threshold:g}, {'enabled' if new_enabled else 'disabled'}.")

    ui.section("Alert history")
    choice = st.selectbox("Show", list(p20.STATUS_FILTERS), key="p20_status")
    with p20.read() as conn:
        frame = p20.alerts_frame(conn, p20.STATUS_FILTERS[choice])
        options = p20.open_alert_options(conn)
    st.caption(f"{len(frame)} alerts")
    st.dataframe(frame, hide_index=True, width="stretch")

    ui.section(
        "Acknowledge an alert",
        "Acknowledging records who has taken charge. The alert stays until its condition clears, then resolves on the next evaluation.",
    )
    if not options:
        st.info("No open alerts to acknowledge.")
    else:
        alert_id = st.selectbox("Open alert", list(options), format_func=lambda a: options[a], key="p20_ack_pick")
        confirmed = ui.confirm_action(
            "p20_ack",
            f"Acknowledge alert **{options[alert_id].rstrip('.')}**?",
            roles=auth.ROLES["acknowledge"],
            button_label="Acknowledge alert",
            default_staff=staff_id,
        )
        if confirmed:
            try:
                with db.transaction() as conn:
                    alerts.acknowledge(conn, alert_id, confirmed, channel=APP_CHANNEL)
                done(f"Alert #{alert_id} acknowledged by {confirmed}.")
            except (ValueError, LookupError) as exc:
                st.error(str(exc))

# -- jobs and simulator ---------------------------------------------------------
with tab_jobs:
    ui.section("Scheduled jobs", "In server mode the scheduler runs these on their interval. Locally, use Run now.")
    with p20.read() as conn:
        jobs = p20.jobs_list(conn)
    for job in jobs:
        c1, c2, c3 = st.columns([3, 3, 1])
        c1.markdown(f"**{job['job_name']}** · every {job['interval_seconds']} s  \n{job['description']}")
        last = job["last_run_status"] or "never run"
        flag = ui.badge("OVERDUE", "bad") if job["overdue"] else ui.badge("on time", "ok")
        c2.markdown(
            f"Last run: {last} {job['last_run_at'] or ''}  \nLast success: {job['last_success_at'] or 'none'} {flag}",
            unsafe_allow_html=True,
        )
        if c3.button("Run now", key=f"p20_run_{job['job_name']}", disabled=not allowed("jobs"), help=needs("jobs")):
            run = scheduler.run_job(job["job_name"], "manual", requested_by=staff_id, channel=APP_CHANNEL)
            done(f"{run['job_name']} run #{run['job_run_id']}: {run['status']}, {run['detail']}")

    ui.section("Position simulator", "Moves every simulated vehicle a little each step. VEH-07 is not simulated, so it goes stale.")
    with p20.read() as conn:
        sim_on = simulator.is_enabled(conn)
        limit = fleet.stale_limit_minutes(conn)
    st.markdown("Simulator: " + (ui.badge("RUNNING", "ok") if sim_on else ui.badge("STOPPED", "warn")), unsafe_allow_html=True)
    s1, s2 = st.columns(2)
    label = "Stop the simulator" if sim_on else "Start the simulator"
    if s1.button(label, disabled=not allowed("simulator"), help=needs("simulator")):
        with db.transaction() as conn:
            simulator.set_enabled(conn, not sim_on, str(staff_id), channel=APP_CHANNEL)
        done(f"Position simulator {'stopped' if sim_on else 'started'}.")
    with s2.form("p20_stale_form", border=False):
        minutes = st.number_input("Stale-position limit (minutes)", min_value=1.0, max_value=1440.0, value=float(limit), step=1.0)
        if st.form_submit_button("Save limit", disabled=not allowed("settings"), help=needs("settings")):
            with db.transaction() as conn:
                alerts.set_stale_limit(conn, minutes, str(staff_id), channel=APP_CHANNEL)
            done(f"Stale-position limit set to {minutes:g} minutes (setting and stale-position rule).")
    with p20.read() as conn:
        st.dataframe(p20.positions_frame(conn), hide_index=True, width="stretch")
        st.markdown("**Recent job runs**")
        st.dataframe(p20.recent_runs(conn), hide_index=True, width="stretch")

# -- health ---------------------------------------------------------------------
with tab_health:
    ui.section(
        "Health",
        f"A job is overdue with no success within {scheduler.OVERDUE_FACTOR:g} x its interval; a service is silent after {scheduler.SERVICE_SILENT_MINUTES:g} minutes.",
    )
    st.markdown("Overall: " + (ui.badge("HEALTHY", "ok") if healthy else ui.badge("NEEDS ATTENTION", "bad")), unsafe_allow_html=True)
    st.markdown("**Services**")
    st.dataframe(services_df, hide_index=True, width="stretch")
    st.markdown("**Scheduled jobs and last successful run**")
    st.dataframe(jobs_df, hide_index=True, width="stretch")

# -- audit ----------------------------------------------------------------------
with tab_audit:
    ui.section("Audit log", "Every change, newest first: who, which role, through which channel, and the values before and after.")
    with p20.read() as conn:
        channels = p20.audit_channels(conn)
        channel = st.selectbox("Channel", ["All", *channels], key="p20_audit_channel")
        st.dataframe(p20.audit_frame(conn, None if channel == "All" else channel), hide_index=True, width="stretch")

# -- api ------------------------------------------------------------------------
with tab_api:
    base = ports.api_url()
    up = p20.api_reachable()
    st.markdown("API: " + (ui.badge("REACHABLE", "ok") if up else ui.badge("NOT RUNNING", "warn")), unsafe_allow_html=True)
    if not up:
        st.caption("Start it from the repository root with: python -m shared.api")
    st.markdown(
        f"- Offline reference (no internet needed): [{base}/reference]({base}/reference)\n- Swagger UI: [{base}/docs]({base}/docs)\n- OpenAPI JSON: [{base}/openapi.json]({base}/openapi.json)"
    )
    st.markdown("Read endpoints are open. Every write needs the header `X-Staff-Id`; in server mode also `Authorization: Bearer <token>`.")
    st.dataframe(
        [{"action": k, "allowed roles": ", ".join(v)} for k, v in auth.ROLES.items()],
        hide_index=True,
        width="stretch",
    )
    st.code(f'curl -X POST {base}/alerts/evaluate -H "X-Staff-Id: CRD-01"', language="bash")
