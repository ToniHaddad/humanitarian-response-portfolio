import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p17_board as board  # noqa: E402
import p17_view as view  # noqa: E402
import streamlit as st  # noqa: E402

from shared import charts, clock, ui  # noqa: E402

ui.page_header(project_number=17)
st.markdown(view.CSS, unsafe_allow_html=True)

_boot = board.open_readonly()
REFRESH_SECONDS = board.refresh_seconds(_boot)
_boot.close()

STATE_COLOR = {"On convoy": "#0072B2", "Idle": "#009E73", "STALE": "#D55E00"}


def position_map(positions, areas):  # noqa: ANN001, ANN202
    """Vehicle positions on the invented grid; stale vehicles are large red crosses."""
    fig = charts.grid_map(
        positions.assign(label=positions["vehicle_id"]),
        label="label",
        color="state",
        hover=["convoy_id", "reported_at"],
        areas=areas,
        height=500,
        stagger_labels=True,
    )
    for trace in fig.data:
        color = STATE_COLOR.get(trace.name)
        if color:
            stale = trace.name == "STALE"
            trace.marker.color = color
            trace.marker.size = 26 if stale else 18
            trace.marker.symbol = "x" if stale else "circle"
            trace.marker.line = {"width": 4 if stale else 1, "color": color if stale else "white"}
            trace.textfont = {"size": 20, "color": color}
    fig.update_xaxes(range=[0, 100], constrain="domain")
    fig.update_yaxes(scaleanchor=None, constrain="domain")
    fig.update_layout(
        font={"size": 18}, margin={"l": 50, "r": 10, "t": 40, "b": 45}, legend={"orientation": "h", "y": 1.08, "x": 0, "font": {"size": 18}}
    )
    return fig


@st.fragment(run_every=REFRESH_SECONDS)
def live_board() -> None:
    """Reads everything again every REFRESH_SECONDS. Opens the database read-only: the board stores nothing."""
    conn = board.open_readonly()
    try:
        now = clock.now()
        st.markdown(view.header_bar(now, REFRESH_SECONDS), unsafe_allow_html=True)
        positions = board.vehicle_positions(conn, now)
        pos_fresh = board.position_freshness(conn, now)

        top = st.columns([5, 3.3, 3.7])
        with top[0]:
            with st.container(border=True):
                st.markdown(view.panel("Vehicle positions", pos_fresh, "", now, framed=False), unsafe_allow_html=True)
                st.plotly_chart(
                    position_map(positions, board.areas_frame(conn)), width="stretch", config={"staticPlot": True}, key="p17_map"
                )
                st.markdown(view.map_caption(positions, now), unsafe_allow_html=True)
        with top[1]:
            st.markdown(
                view.panel(
                    "Convoy status", board.convoy_freshness(conn, now), view.convoy_body(board.convoy_rows(conn, now), now), now, tall=True
                ),
                unsafe_allow_html=True,
            )
        with top[2]:
            summary = board.request_summary(conn, now)
            st.markdown(
                view.panel("Requests and callbacks", board.request_freshness(conn, now), view.request_body(summary), now, tall=True),
                unsafe_allow_html=True,
            )

        bottom = st.columns([3.3, 4.2, 4.5])
        with bottom[0]:
            st.markdown(
                view.panel(
                    "Movement advisories", board.advisory_freshness(conn, now), view.advisory_body(board.active_advisories(conn)), now
                ),
                unsafe_allow_html=True,
            )
        with bottom[1]:
            body = view.stock_body(board.stock_below_reorder(conn), board.open_alerts(conn))
            st.markdown(view.panel("Stock alerts", board.stock_freshness(conn, now), body, now), unsafe_allow_html=True)
        with bottom[2]:
            body = view.distribution_body(board.distributions_today(conn, now.date().isoformat()))
            st.markdown(
                view.panel("Today's distributions against plan", board.distribution_freshness(conn, now), body, now), unsafe_allow_html=True
            )
    finally:
        conn.close()


live_board()
