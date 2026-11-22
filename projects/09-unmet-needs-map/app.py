import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p09_unmet  # noqa: E402
import streamlit as st  # noqa: E402

from shared import charts, clock, db, ui  # noqa: E402

ui.page_header(project_number=9)

today = clock.today()
d0, d1 = p09_unmet.default_range(today)
area_rows = db.query_all("SELECT area_id, name_en FROM areas ORDER BY area_id")
area_names = {a["area_id"]: f"{a['area_id']} {a['name_en']}" for a in area_rows}

with st.sidebar:
    st.header("Filters")
    picked = st.date_input("Request date range (both days included)", value=(d0, d1), key="p09_dates")
    aid_choice = st.selectbox(
        "Aid type",
        ["all", *p09_unmet.AID_TYPES],
        format_func=lambda a: "All aid types" if a == "all" else p09_unmet.AID_LABELS[a],
        key="p09_aid",
    )
    area_choice = st.selectbox(
        "Area", ["all", *area_names], format_func=lambda a: "All areas" if a == "all" else area_names[a], key="p09_area"
    )
    st.caption(f"Demo clock: {clock.now():%Y-%m-%d %H:%M}")

if not isinstance(picked, tuple) or len(picked) != 2:
    st.info("Pick both a start and an end date.")
    st.stop()
start, end = picked
aid = None if aid_choice == "all" else aid_choice
area = None if area_choice == "all" else area_choice

conn = db.connect()
try:
    tot = p09_unmet.totals(conn, start, end, aid, area)
    areas_df = p09_unmet.by_area(conn, start, end, aid, area)
    grid_df = p09_unmet.by_area_and_type(conn, start, end, area)
    weekly_df = p09_unmet.weekly(conn, start, end, aid, area)
finally:
    conn.close()

ui.metric_row(
    [
        ("Requests (count)", tot["requests"], "Requests created in the date range that are a real need."),
        ("Completed (count)", tot["completed"], "Closed as assisted, or a referral where the family received support."),
        ("Outstanding (count)", tot["outstanding"], "Requests minus completed."),
        ("Not counted (count)", tot["excluded"], "Closed as duplicate, withdrawn or not eligible: not a need."),
    ]
)

with st.expander("How the gap is calculated", expanded=True):
    st.markdown(
        """
- **Requests**: service requests created in the date range (first and last day included). Requests closed as
  *duplicate*, *withdrawn* or *not eligible* are not a need, so they are left out and shown as "Not counted".
- **Completed**: a counted request closed as *assisted*, or one with a referral where the family confirmed they
  received the support. A request is counted once, even with several referrals.
- **Outstanding (the gap)** = requests - completed. Open, in progress, referred-but-not-received and
  unreachable requests all stay outstanding.
- **Completion rate** = completed / requests. **Per 1,000 people** = outstanding / area population x 1,000.
- Aid type is the confirmed category. Requests with no confirmed category are shown as "Not yet categorised"
  and are never guessed. Food parcels handed out per household are **not** added: that would count the same help twice.
"""
    )
    if not areas_df.empty and areas_df["requests"].sum() > 0:
        top = areas_df.sort_values(["outstanding", "area_id"], ascending=[False, True]).iloc[0]
        label = p09_unmet.AID_LABELS[aid] if aid else None
        st.markdown("**Worked example (largest gap in the current selection):** " + p09_unmet.worked_example(top, label))

if tot["requests"] == 0:
    st.info("No requests in this selection. Widen the date range or clear a filter.")

ui.section(
    "Outstanding needs by area", "Each marker is one area total on an invented 100 x 100 km grid. Marker size = outstanding requests."
)
colour_by = st.radio("Colour markers by", ["Completion rate (%)", "Outstanding (count)"], horizontal=True, key="p09_colour")
map_df = areas_df.copy()
map_df["marker_size"] = map_df["outstanding"].clip(lower=0) + 0.5  # keeps zero-gap areas visible as small dots
colour_col = "completion_rate_pct" if colour_by.startswith("Completion") else "outstanding"
map_df[colour_col] = map_df[colour_col].fillna(0.0)
fig = charts.grid_map(
    map_df,
    label="name_en",
    size="marker_size",
    hover=["requests", "completed", "outstanding", "completion_rate_pct", "outstanding_per_1000"],
)
fig.update_traces(
    marker={
        "color": map_df[colour_col].tolist(),
        "colorscale": "RdYlGn" if colour_col == "completion_rate_pct" else "OrRd",
        "showscale": True,
        "colorbar": {"title": colour_by},
        "line": {"width": 1, "color": "#444"},
    },
    selector={"mode": "markers+text"},
)
fig.update_yaxes(range=[0, 110])  # headroom so a label above a point near the top edge is not clipped
st.plotly_chart(fig, width="stretch")

ui.section("Area table", "Click a column heading to sort.")
table = areas_df.rename(
    columns={
        "area_id": "Area ID",
        "name_en": "Area",
        "population_estimate": "Population (people)",
        "requests": "Requests (count)",
        "completed": "Completed (count)",
        "outstanding": "Outstanding (count)",
        "completion_rate_pct": "Completion rate (%)",
        "outstanding_per_1000": "Outstanding per 1,000 people",
        "excluded": "Not counted (count)",
    }
)[
    [
        "Area ID",
        "Area",
        "Population (people)",
        "Requests (count)",
        "Completed (count)",
        "Outstanding (count)",
        "Completion rate (%)",
        "Outstanding per 1,000 people",
        "Not counted (count)",
    ]
].sort_values("Outstanding (count)", ascending=False)
ui.table(table)

ui.section("Outstanding by area and aid type")
pivot = grid_df.pivot(index="area_id", columns="aid_type", values="outstanding").reindex(columns=list(p09_unmet.AID_TYPES))
row_labels = [area_names[a] for a in pivot.index]
st.plotly_chart(
    charts.heatmap(
        pivot.values.tolist(),
        [p09_unmet.AID_LABELS[c] for c in pivot.columns],
        row_labels,
        "Aid type",
        "Area",
        "Outstanding (count)",
    ),
    width="stretch",
)
with st.expander("Area x aid type table (requests, completed, outstanding)"):
    detail = grid_df.assign(area=grid_df["area_id"].map(area_names))[
        ["area", "aid_label", "requests", "completed", "outstanding", "completion_rate_pct"]
    ].rename(
        columns={
            "area": "Area",
            "aid_label": "Aid type",
            "requests": "Requests (count)",
            "completed": "Completed (count)",
            "outstanding": "Outstanding (count)",
            "completion_rate_pct": "Completion rate (%)",
        }
    )
    ui.table(detail, height=420)

ui.section(
    "Requests and completed assistance by week", "Weeks start on Monday. Completed = requests created that week that are now completed."
)
if weekly_df.empty:
    st.caption("Nothing to chart for this selection.")
else:
    long = weekly_df.melt(id_vars="week_start", value_vars=["requests", "completed"], var_name="Measure", value_name="count")
    long["Measure"] = long["Measure"].map({"requests": "Requests", "completed": "Completed"})
    weekly_fig = charts.bar(long, "week_start", "count", "Week starting (date)", "Requests (count)", color="Measure")
    weekly_fig.update_yaxes(range=[0, float(long["count"].max()) * 1.15 + 1])
    st.plotly_chart(weekly_fig, width="stretch")
