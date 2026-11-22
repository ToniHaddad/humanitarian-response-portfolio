import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p13_shifts as sh  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from shared import db, ui  # noqa: E402
from shared.confirm import ConfirmationRequired  # noqa: E402

ui.page_header(project_number=13)
st.caption(
    "Roles planned: driver, packing, registration and distribution. Interpreter roles are excluded by design and are not offered here."
)

conn = db.connect()
if "p13_flash" in st.session_state:
    st.success(st.session_state.pop("p13_flash"))

slots = sh.role_status(conn)
over = sh.overlaps(conn)
invalid = sh.invalid_assignments(conn)
proposals = sh.suggest(conn)
unfilled = sum(s["unfilled"] for s in slots)

ui.metric_row(
    [
        ("Shifts", len({s["shift_id"] for s in slots})),
        ("Unfilled slots (people)", unfilled, "Required count minus suggested or confirmed assignments."),
        ("Overlapping assignment pairs", len(over)),
        ("Assignments to review", len(invalid), "Volunteer lacks the training or a covering availability window."),
        ("Suggested (people)", len(proposals)),
    ]
)

# -- flags --------------------------------------------------------------------
ui.section("Unfilled roles", "Per shift and role. Cancelled assignments do not count as filled.")
slot_df = pd.DataFrame(slots)
slot_df["when"] = slot_df["shift_date"] + " " + slot_df["start_time"] + "-" + slot_df["end_time"]
ui.table(
    slot_df[["shift_id", "when", "site_id", "role", "required", "filled", "confirmed", "unfilled"]].rename(
        columns={
            "shift_id": "Shift",
            "when": "When",
            "site_id": "Site",
            "role": "Role",
            "required": "Needed (people)",
            "filled": "Assigned (people)",
            "confirmed": "Confirmed (people)",
            "unfilled": "Unfilled (people)",
        }
    )
)

ui.section("Overlapping assignments", "The same volunteer on two assignments whose times overlap on one date.")
if over:
    st.error(f"{len(over)} overlap(s) found.")
    over_df = pd.DataFrame(over)
    ui.table(
        pd.DataFrame(
            {
                "Volunteer": over_df["volunteer_id"],
                "Date": over_df["shift_date"],
                "First assignment": "#"
                + over_df["assignment_a"].astype(str)
                + " "
                + over_df["shift_a"]
                + " "
                + over_df["role_a"]
                + " "
                + over_df["time_a"]
                + " ("
                + over_df["status_a"]
                + ")",
                "Second assignment": "#"
                + over_df["assignment_b"].astype(str)
                + " "
                + over_df["shift_b"]
                + " "
                + over_df["role_b"]
                + " "
                + over_df["time_b"]
                + " ("
                + over_df["status_b"]
                + ")",
            }
        )
    )
else:
    st.success("No overlapping assignments.")

if invalid:
    ui.section("Assignments to review", "Existing assignments where the volunteer lacks the training or a covering availability window.")
    ui.table(
        pd.DataFrame(invalid)[["assignment_id", "volunteer_id", "shift_id", "role", "problem"]].rename(
            columns={"assignment_id": "Assignment", "volunteer_id": "Volunteer", "shift_id": "Shift", "role": "Role", "problem": "Problem"}
        )
    )

# -- suggestions -----------------------------------------------------------------
ui.section(
    "Suggested assignments",
    "Rule-based: eligible volunteers are active, trained, available for the whole shift, not overlapping, and under their weekly "
    "maximum. Ranked by distance from home area to site, then shifts that week, then ID.",
)
if proposals:
    sug_df = pd.DataFrame(proposals)[["shift_id", "role", "volunteer_id", "distance_km", "shifts_that_week", "why"]]
    sug_df["why"] = sug_df["why"].map(lambda t: t if len(t) <= 40 else t[:39].rstrip() + "…")
    ui.table(
        sug_df.rename(
            columns={
                "shift_id": "Shift",
                "role": "Role",
                "volunteer_id": "Volunteer",
                "distance_km": "Distance (km)",
                "shifts_that_week": "Shifts that week (count)",
                "why": "Why (short)",
            }
        )
    )
    labels = {i: f"{p['shift_id']} · {p['role']} · {p['volunteer_id']}" for i, p in enumerate(proposals)}
    pick = st.selectbox("Suggestion to accept", list(labels), format_func=lambda i: labels[i], key="p13_pick")
    status = st.radio("Record as", ["suggested", "confirmed"], horizontal=True, key="p13_status")
    chosen = proposals[pick]
    st.caption(f"Why {chosen['volunteer_id']} for {chosen['shift_id']} {chosen['role']}: {chosen['why']}")
    staff = ui.confirm_action(
        "accept",
        f"Record **{chosen['volunteer_id']}** for **{chosen['shift_id']} {chosen['role']}** as *{status}*?",
        roles=("coordinator",),
        button_label="Accept suggestion",
    )
    if staff:
        try:
            with db.transaction() as c:
                sh.accept_suggestion(c, chosen["shift_id"], chosen["role"], chosen["volunteer_id"], staff, status)
            st.session_state["p13_flash"] = f"Recorded {chosen['volunteer_id']} for {chosen['shift_id']} {chosen['role']} as {status}."
            st.rerun()
        except (ConfirmationRequired, ValueError) as exc:
            st.error(str(exc))
else:
    st.info("No eligible volunteers for any remaining unfilled slot.")

# -- confirm or cancel -----------------------------------------------------------------
ui.section("Confirm or cancel an assignment")
rows = sh.assignments(conn)
if rows:
    alabels = {
        r["assignment_id"]: f"#{r['assignment_id']} · {r['shift_id']} {r['shift_date']} {r['start_time']}-{r['end_time']} · "
        f"{r['role']} · {r['volunteer_id']} · {r['status']}"
        for r in rows
    }
    aid = st.selectbox("Assignment", list(alabels), format_func=lambda i: alabels[i], key="p13_assign")
    current = next(r for r in rows if r["assignment_id"] == aid)
    options = ["cancel"] + (["confirm"] if current["status"] == "suggested" else [])
    action = st.radio("Action", options, horizontal=True, key="p13_action")
    staff = ui.confirm_action(
        "change",
        f"{action.capitalize()} assignment **{alabels[aid]}**?",
        roles=("coordinator",),
        button_label=f"{action.capitalize()} assignment",
    )
    if staff:
        try:
            with db.transaction() as c:
                if action == "cancel":
                    sh.cancel_assignment(c, int(aid), staff)
                else:
                    sh.confirm_assignment(c, int(aid), staff)
            st.session_state["p13_flash"] = f"Assignment #{aid} {'cancelled' if action == 'cancel' else 'confirmed'}."
            st.rerun()
        except (ConfirmationRequired, ValueError) as exc:
            st.error(str(exc))
