import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import json  # noqa: E402
from datetime import datetime  # noqa: E402

import p01_requests as p01  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from shared import classifier, clock, db, ui  # noqa: E402
from shared.call_notes import CATEGORIES  # noqa: E402
from shared.confirm import ConfirmationRequired  # noqa: E402

ui.page_header(project_number=1)


@st.cache_resource(show_spinner="Loading the call-note classifier from Project 03...")
def get_model():  # noqa: ANN201 - sklearn pipeline
    with db.transaction() as conn:
        return classifier.load_or_train(conn)


def flash(message: str) -> None:
    st.session_state["p01_flash"] = message
    st.rerun()


def run_step(action, success: str) -> None:  # noqa: ANN001 - callable taking a connection
    try:
        with db.transaction() as conn:
            action(conn)
    except (p01.RequestError, ConfirmationRequired) as exc:
        st.error(f"{exc} Nothing was changed.")
        return
    flash(success)


CAT_OPTIONS = list(CATEGORIES)
model = get_model()
staff_id = ui.staff_selector(roles=("operator", "caseworker", "coordinator"))
now = clock.now()

with db.transaction() as conn:
    threshold = classifier.threshold_from_settings(conn)
    sla = p01.sla_hours(conn)
    m = p01.metrics(conn, now)
    open_df = p01.open_requests(conn, now)
    unassigned_df = p01.unassigned_requests(conn, now)
    overdue_df = p01.overdue_callbacks(conn, now)
    all_ids = [r["request_id"] for r in db.query_all("SELECT request_id FROM service_requests ORDER BY request_id DESC", conn=conn)]
    areas = db.query_all("SELECT area_id, name_en FROM areas ORDER BY area_id", conn=conn)
    caseworkers = [
        r["staff_id"]
        for r in db.query_all("SELECT staff_id FROM staff WHERE role = 'caseworker' AND active = 1 ORDER BY staff_id", conn=conn)
    ]
    scenario = json.loads(db.get_setting("demo.scenarios", "{}", conn) or "{}").get("p01", {})

if msg := st.session_state.pop("p01_flash", None):
    st.success(msg)

ui.metric_row(
    [
        ("Open requests (count)", m["open"]),
        ("Unassigned (count)", m["unassigned"], "Open requests with no caseworker assigned."),
        (
            "Overdue callbacks (count)",
            m["overdue"],
            f"Callback due time has passed, not done, request not closed. SLA {sla:g} h after assignment.",
        ),
        ("Closed in last 7 days (count)", m["closed_7d"]),
    ]
)


def list_view(df: pd.DataFrame, extra: list[str] | None = None) -> pd.DataFrame:
    def short(ts: object) -> str:
        return str(ts)[:16].replace("T", " ") if isinstance(ts, str) else ""

    out = pd.DataFrame(
        {
            "Request": df["request_id"],
            "Next action": df["next_action"],
            "Priority": df["priority"],
            "Status": df["status"].str.replace("_", " "),
            "Category": df["category"].map(lambda c: p01.category_label(c) if isinstance(c, str) else "(not confirmed)"),
            "Assigned to": df["assigned_to"].fillna(""),
            "Callback due": df["callback_due_at"].map(short),
        }
    )
    if extra and "hours" in extra:
        out["Hours overdue (h)"] = df["hours_overdue"]
    out["Linked calls (count)"] = df["calls"]
    out["Area"] = df["area"]
    out["Created"] = df["created_at"].map(short)
    return out


tab_open, tab_unassigned, tab_overdue, tab_detail, tab_intake = st.tabs(
    [
        f"Open requests ({m['open']})",
        f"Unassigned ({m['unassigned']})",
        f"Overdue callbacks ({m['overdue']})",
        "Request detail and actions",
        "New request (intake)",
    ]
)

with tab_open:
    ui.section(
        "All open requests with the next action",
        "Next action rule, first match wins: no category → Confirm category; unassigned → Assign to a caseworker; "
        "no callback → Schedule callback; callback past due → Call back now; callback due → Call back by; "
        "callback done → Close or record outcome.",
    )
    ui.table(list_view(open_df), height=420)

with tab_unassigned:
    ui.section("Unassigned requests", "Open requests that no caseworker holds yet. Open one under 'Request detail and actions'.")
    ui.table(list_view(unassigned_df))

with tab_overdue:
    ui.section("Overdue callbacks", f"Callback due before now ({clock.iso(now)}), not done, request not closed. Most overdue first.")
    if overdue_df.empty:
        st.info("No overdue callbacks.")
    else:
        ui.table(list_view(overdue_df, ["hours"]))

with tab_detail:
    first = (scenario.get("unassigned_request_ids") or [None])[0]
    default_index = all_ids.index(first) if first in all_ids else 0
    request_id = st.selectbox("Request", all_ids, index=default_index, key="p01_request")
    with db.transaction() as conn:
        row = p01.get_request(conn, request_id)
        prediction, stored = p01.stored_or_computed_suggestion(conn, model, row)
        n_calls = p01.calls_count(conn, request_id)
        events = p01.timeline(conn, request_id)
        area_name = db.scalar("SELECT name_en FROM areas WHERE area_id = ?", (row["area_id"],), conn=conn)
    action = p01.next_action(row, now)
    ui.metric_row(
        [
            ("Status", row["status"].replace("_", " ")),
            ("Category", p01.category_label(row["category"]) or "(not confirmed)"),
            ("Assigned to", row["assigned_to"] or "nobody"),
            ("Linked calls (count)", n_calls, "Calls logged in Project 02 that point to this request."),
        ]
    )
    st.markdown(
        f"**Area:** {row['area_id']} {area_name} · **Household:** {row['household_id'] or 'not recorded'} · "
        f"**Priority:** {row['priority']} · **Created:** {row['created_at']} · **Callback due:** {row['callback_due_at'] or 'not scheduled'}"
    )
    ui.show_text(row["notes"], "Operator's notes")
    st.markdown(f"**Next action:** {action}")

    if row["status"] == "closed":
        st.info(f"Closed {row['closed_at']} with outcome **{row['closure_outcome']}**.")
    elif not row["category"]:
        ui.section("Confirm category")
        source = "stored at intake" if stored else "computed now by the classifier"
        if prediction.category is None:
            st.warning(
                f"The model is not confident (confidence {prediction.confidence:.2f}, below the threshold {threshold:.2f}), "
                "so the suggestion is left blank. Choose the category yourself."
            )
            index = None
        else:
            st.info(f"Suggested: **{p01.category_label(prediction.category)}** (confidence {prediction.confidence:.2f}, {source}).")
            index = CAT_OPTIONS.index(prediction.category)
        chosen = st.selectbox(
            "Confirmed category",
            CAT_OPTIONS,
            index=index,
            format_func=p01.category_label,
            placeholder="Choose the category",
            key=f"p01_cat_{request_id}",
        )
        if st.button("Confirm category", key="p01_confirm_cat", type="primary"):
            run_step(
                lambda c: p01.confirm_category(c, model, request_id, chosen, staff_id or ""),
                f"{request_id}: category {p01.category_label(chosen)} confirmed.",
            )
    elif row["status"] == "new":
        ui.section("Assign to a caseworker")
        cw = st.selectbox("Caseworker", caseworkers, key="p01_cw")
        if st.button("Assign", key="p01_assign", type="primary"):
            run_step(lambda c: p01.assign(c, request_id, cw, staff_id or ""), f"{request_id} assigned to {cw}.")
    elif row["status"] == "assigned":
        ui.section("Schedule callback")
        latest = p01.default_callback_due(row["assigned_at"], sla)
        use_default = st.checkbox(f"Use the default due time {clock.iso(latest)} (assigned + {sla:g} h)", value=True, key="p01_use_default")
        due_at: datetime | None = None
        if not use_default:
            c1, c2 = st.columns(2)
            day = c1.date_input("Callback due date", value=latest.date(), key="p01_due_date")
            tm = c2.time_input("Callback due time", value=latest.time(), key="p01_due_time")
            due_at = datetime.combine(day, tm)
        if st.button("Schedule callback", key="p01_schedule", type="primary"):
            run_step(lambda c: p01.schedule_callback(c, request_id, staff_id or "", due_at), f"{request_id}: callback scheduled.")
    elif row["status"] == "callback_scheduled":
        ui.section("Record callback done")
        note = st.text_input("Callback note (optional)", key="p01_cb_note")
        if st.button("Record callback done", key="p01_cb_done", type="primary"):
            run_step(lambda c: p01.record_callback_done(c, request_id, staff_id or "", note), f"{request_id}: callback recorded.")
    elif row["status"] == "in_progress":
        ui.section("Close the request")
        outcome = st.selectbox("Closure outcome", p01.OUTCOMES, key="p01_outcome", format_func=lambda o: o.replace("_", " "))
        confirmer = ui.confirm_action(
            "p01_close",
            f"Close **{request_id}** with outcome **{outcome.replace('_', ' ')}**? A closed request leaves the open lists.",
            roles=p01.CLOSE_ROLES,
            button_label="Close request",
            default_staff=staff_id,
        )
        if confirmer:
            run_step(lambda c: p01.close(c, request_id, outcome, confirmer), f"{request_id} closed with outcome {outcome}.")

    ui.section(f"Timeline ({len(events)} events)")
    ui.table(events.rename(columns={"at": "Time", "event_type": "Event", "staff_id": "Staff", "detail": "Detail"}))

with tab_intake:
    ui.section(
        "New request",
        f"The category suggestion comes from the Project 03 classifier. Below confidence {threshold:.2f} it is left blank and the operator must choose.",
    )
    notes = st.text_area("Operator's notes (Arabic or English)", key="p01_new_notes", height=110)
    if notes.strip():
        ui.show_text(notes, "Note as stored")
    c1, c2, c3 = st.columns(3)
    area_id = c1.selectbox(
        "Area", [a["area_id"] for a in areas], format_func=lambda a: f"{a} {dict((x['area_id'], x['name_en']) for x in areas)[a]}"
    )
    language = c2.selectbox(
        "Language", list(p01.LANGUAGES), index=1 if ui.has_arabic(notes) else 0, format_func={"en": "English", "ar": "Arabic"}.get
    )
    priority = c3.selectbox("Priority", list(p01.PRIORITIES), index=1, key="p01_new_priority")
    household = st.text_input("Household ID (optional, e.g. HH-00042)", key="p01_new_hh").strip().upper()
    new_category = None
    if notes.strip():
        with db.transaction() as conn:
            pred = p01.suggest(conn, model, notes)
        if pred.category is None:
            st.warning(
                f"No suggestion: the model is not confident (confidence {pred.confidence:.2f}, below {threshold:.2f}). "
                "Choose the category yourself."
            )
            idx = None
        else:
            st.info(f"Suggested category: **{p01.category_label(pred.category)}** (confidence {pred.confidence:.2f}). Accept or change it.")
            idx = CAT_OPTIONS.index(pred.category)
        new_category = st.selectbox(
            "Confirmed category",
            CAT_OPTIONS,
            index=idx,
            format_func=p01.category_label,
            placeholder="Choose the category",
            key=f"p01_new_cat_{abs(hash(notes)) % 10**8}",
        )
    if st.button("Save request", key="p01_save", type="primary"):
        holder: dict[str, str] = {}

        def _create(c) -> None:  # noqa: ANN001
            holder["id"] = p01.create_request(
                c,
                model,
                area_id=area_id,
                notes=notes,
                language=language,
                category=new_category,
                created_by=staff_id or "",
                priority=priority,
                household_id=household or None,
            )

        try:
            with db.transaction() as conn:
                _create(conn)
        except p01.RequestError as exc:
            st.error(f"{exc} Nothing was saved.")
        else:
            flash(f"Saved {holder['id']} with category {p01.category_label(new_category)} (status new).")
