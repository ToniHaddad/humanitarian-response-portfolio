import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p14_fleet as fl  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from shared import charts, clock, db, fleet, ui  # noqa: E402
from shared.confirm import ConfirmationRequired  # noqa: E402

ui.page_header(project_number=14)

conn = db.connect()
STAFF_ROLES = (*fl.UPDATE_ROLES, "admin")

if "p14_flash" in st.session_state:
    st.success(st.session_state.pop("p14_flash"))


def run_update(action, message: str) -> None:  # noqa: ANN001
    """Run a domain function in one transaction; show its refusal or error in plain words."""
    try:
        with db.transaction() as c:
            result = action(c)
    except (ConfirmationRequired, ValueError) as exc:
        st.error(f"Nothing was changed. {exc}")
        return
    st.session_state["p14_flash"] = message.format(result=result)
    st.rerun()


tab_fleet, tab_pos, tab_trip, tab_update = st.tabs(["Fleet and readiness", "Last known positions", "Trip log per convoy", "Update records"])

# -- fleet and readiness ---------------------------------------------------
with tab_fleet:
    on = st.date_input("Check readiness on", value=clock.today(), help="Readiness is judged on this date.")
    table = fl.fleet_table(conn, on)
    counts = table["Status"].value_counts()
    ui.metric_row(
        [
            ("Ready (vehicles)", int(counts.get("Ready", 0))),
            ("Due for service (vehicles)", int(counts.get("Due for service", 0))),
            ("Not ready (vehicles)", int(counts.get("Not ready", 0))),
        ]
    )
    shown_fleet = table[
        ["Vehicle", "Status", "Type", "Open defects", "Why", "Next service by date", "Next service by distance (km)", "Odometer (km)"]
    ].copy()
    shown_fleet["Why"] = shown_fleet["Why"].map(lambda t: t if len(t) <= 45 else t[:44].rstrip() + "…")
    ui.table(
        shown_fleet.rename(
            columns={
                "Why": "Why (short)",
                "Next service by date": "Service due (date)",
                "Next service by distance (km)": "Service due at (km)",
                "Odometer (km)": "Odometer now (km)",
            }
        ),
        key="p14_fleet_table",
    )
    ui.section("Why is a vehicle marked this way?", "Rule: shared/fleet.py. Blocking reasons make a vehicle not ready; warnings do not.")
    vehicle_id = st.selectbox("Vehicle", table["Vehicle"].tolist(), key="p14_explain_vehicle")
    result = fl.explain(conn, vehicle_id, on)
    kind = {"ready": "ok", "due_for_service": "warn", "not_ready": "bad"}[result.status]
    st.markdown(f"**{vehicle_id}** on {on.isoformat()}: " + ui.badge(fl.STATUS_LABELS[result.status], kind), unsafe_allow_html=True)
    for reason in result.reasons:
        st.error(reason)
    for warning in result.warnings:
        st.warning(warning)
    if not result.reasons and not result.warnings:
        st.success("No service, document or defect problems on this date.")
    with st.expander(f"All dates, load and odometer for {vehicle_id}"):
        detail = table[table["Vehicle"] == vehicle_id].iloc[0].drop(["Vehicle", "Why"])
        ui.table(pd.DataFrame({"Item": detail.index, "Value": [str(v) for v in detail.values]}), key="p14_vehicle_detail")

# -- positions -------------------------------------------------------------
with tab_pos:
    st.caption(
        "Position reports arrive from the Project 20 API and simulator, which write them with shared.fleet.record_position. "
        "This page only reads them. Positions are kilometres on the invented 100 x 100 grid."
    )

    @st.fragment(run_every="30s")
    def positions_panel() -> None:
        c = db.connect()
        try:
            now = clock.now()
            limit = fleet.stale_limit_minutes(c)
            pos = fl.position_table(c, now, limit)
        finally:
            c.close()
        st.button("Refresh now", key="p14_refresh")
        st.markdown(f"Stale limit: **{limit:g} minutes** (setting `stale_position_minutes`). Updated at {now:%H:%M:%S}.")
        shown = pos.copy()
        newest = shown["last_report_at"].dropna().max() if shown["last_report_at"].notna().any() else None
        if fleet.is_stale(newest, now, limit):
            st.warning(
                "The newest position report is old. The position simulator only runs when the portfolio is launched with its "
                "services (python run.py); opened alone, this page shows the last reports that were written."
            )
        st.markdown("Newest report in the fleet: " + ui.freshness_badge(newest, limit, now), unsafe_allow_html=True)
        n_stale = int(shown["stale"].sum())
        ui.metric_row([("Vehicles tracked (count)", len(shown)), ("Stale positions (vehicles)", n_stale)])
        for row in shown[shown["stale"]].itertuples():
            st.markdown(f"**{row.vehicle_id}** " + ui.freshness_badge(row.last_report_at, limit, now), unsafe_allow_html=True)
        table = shown.assign(Flag=shown["stale"].map({True: "STALE", False: "ok"}))[
            ["vehicle_id", "Flag", "minutes_since_report", "last_report_at", "x_km", "y_km", "speed_kmh", "convoy_id", "source"]
        ]
        table = table.rename(
            columns={
                "vehicle_id": "Vehicle",
                "minutes_since_report": "Report age (min)",
                "last_report_at": "Last report at",
                "x_km": "East (km)",
                "y_km": "North (km)",
                "speed_kmh": "Speed (km/h)",
                "convoy_id": "Convoy",
                "source": "Source",
            }
        )
        ui.table(table, key="p14_pos_table")
        have = pos.dropna(subset=["x_km"]).copy()
        have["State"] = have["stale"].map({True: "STALE", False: "Fresh"})
        if not have.empty:
            fig = charts.grid_map(
                have, label="vehicle_id", color="State", hover=["minutes_since_report"], title="Last known positions", height=480
            )
            st.plotly_chart(fig, width="stretch", key="p14_pos_map")

    positions_panel()

# -- trip log --------------------------------------------------------------
with tab_trip:
    convoys = fl.convoy_list(conn)
    labels = {c["convoy_id"]: f"{c['convoy_id']} · {c['name']} ({c['status']}, {c['planned_date']})" for c in convoys}
    convoy_id = st.selectbox("Convoy", list(labels), format_func=lambda k: labels[k], key="p14_convoy")
    log = fl.trip_log(conn, convoy_id)
    ui.section("Status events")
    ui.table(
        log["events"].rename(columns={"at": "At", "status": "Status", "staff_id": "By", "note": "Note"}),
        key="p14_events",
    )
    ui.section("Vehicles and drivers")
    ui.table(
        log["vehicles"].rename(
            columns={"vehicle_id": "Vehicle", "vehicle_type": "Type", "driver_id": "Driver", "licence_class": "Licence class"}
        ),
        key="p14_cv",
    )
    ui.section("Position reports", f"{len(log['reports'])} reports carrying this convoy id, oldest first.")
    if log["reports"].empty:
        st.info("No position reports carry this convoy id.")
    else:
        fig = px.line(log["reports"], x="x_km", y="y_km", color="vehicle_id", markers=True, color_discrete_sequence=charts.PALETTE)
        fig.update_xaxes(range=[0, 100], dtick=20)
        fig.update_yaxes(range=[0, 100], dtick=20)
        charts.apply_defaults(
            fig, "Distance east (km, invented grid)", "Distance north (km, invented grid)", "Route flown by report order", 420
        )
        st.plotly_chart(fig, width="stretch", key="p14_route")
        ui.table(
            log["reports"][["reported_at", "vehicle_id", "x_km", "y_km", "speed_kmh", "source"]].rename(
                columns={
                    "reported_at": "Reported at",
                    "vehicle_id": "Vehicle",
                    "x_km": "East (km)",
                    "y_km": "North (km)",
                    "speed_kmh": "Speed (km/h)",
                    "source": "Source",
                }
            ),
            height=260,
            key="p14_reports",
        )

# -- updates ---------------------------------------------------------------
with tab_update:
    vehicle_ids = db.query_all("SELECT vehicle_id FROM vehicles ORDER BY vehicle_id", conn=conn)
    vids = [v["vehicle_id"] for v in vehicle_ids]

    ui.section("Report a defect")
    d_vehicle = st.selectbox("Vehicle", vids, key="p14_d_vehicle")
    d_severity = st.selectbox("Severity", list(fl.SEVERITIES), index=1, key="p14_d_sev", help="Grounding makes the vehicle not ready.")
    d_text = st.text_input("What is wrong", key="p14_d_text")
    staff = ui.confirm_action(
        "report_defect", f"Report a **{d_severity}** defect on **{d_vehicle}**: {d_text or '(no description yet)'}", roles=STAFF_ROLES
    )
    if staff:
        run_update(lambda c: fl.report_defect(c, d_vehicle, d_severity, d_text, staff), "Defect {result} reported.")

    ui.section("Resolve a defect")
    opened = fl.open_defects(conn)
    if opened:
        dmap = {d["defect_id"]: f"{d['defect_id']} · {d['vehicle_id']} · {d['severity']} · {d['description']}" for d in opened}
        r_id = st.selectbox("Open defect", list(dmap), format_func=lambda k: dmap[k], key="p14_r_id")
        staff = ui.confirm_action("resolve_defect", f"Mark defect **{r_id}** as resolved.", roles=STAFF_ROLES)
        if staff:
            run_update(lambda c: fl.resolve_defect(c, r_id, staff), f"Defect {r_id} resolved.")
    else:
        st.info("No open defects.")

    ui.section("Record a service")
    s_vehicle = st.selectbox("Vehicle", vids, key="p14_s_vehicle")
    current = int(db.scalar("SELECT odometer_km FROM vehicles WHERE vehicle_id = ?", (s_vehicle,), conn))
    s_date = st.date_input("Service date", value=clock.today(), key="p14_s_date")
    s_odo = st.number_input("Odometer at service (km)", min_value=current, value=current, step=1, key=f"p14_s_odo_{s_vehicle}")
    staff = ui.confirm_action(
        "record_service", f"Record a service of **{s_vehicle}** on {s_date.isoformat()} at {int(s_odo)} km.", roles=STAFF_ROLES
    )
    if staff:
        run_update(lambda c: fl.record_service(c, s_vehicle, staff, s_date, int(s_odo)), f"Service recorded for {s_vehicle}.")

    ui.section("Stale-position limit", "Shared with Projects 17 and 20. Coordinator or admin only.")
    limit_now = fleet.stale_limit_minutes(conn)
    new_limit = st.number_input(
        "Minutes before a position counts as stale", min_value=1.0, max_value=1440.0, value=float(limit_now), step=1.0
    )
    staff = ui.confirm_action(
        "stale_limit", f"Change the stale-position limit from {limit_now:g} to {new_limit:g} minutes.", roles=("coordinator", "admin")
    )
    if staff:
        run_update(lambda c: fl.set_stale_limit(c, new_limit, staff), f"Stale limit is now {new_limit:g} minutes.")
