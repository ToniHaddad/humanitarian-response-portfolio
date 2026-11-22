import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p07_stats as stats  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from shared import charts, confirm, db, pipeline, ui  # noqa: E402


def short(text: object, limit: int = 70) -> str:
    """Trim long cell text for display only; the stored value is untouched."""
    value = "" if text is None else str(text)
    return value if len(value) <= limit else value[: limit - 1] + "…"


RUN_COLUMNS = {
    "started_at": "Started",
    "file_name": "File",
    "source_id": "Source",
    "status": "Status",
    "rows_read": "Read (rows)",
    "rows_loaded": "Loaded (rows)",
    "rows_duplicate": "Duplicate (rows)",
    "rows_quarantined": "Quarantined (rows)",
    "trigger": "Trigger",
    "run_id": "Run",
    "error_message": "Error",
}

ui.page_header(project_number=7)
staff = ui.staff_selector() or "system"

# -- Documented schema -----------------------------------------------------
ui.section("Documented schema", "Every export is checked against these columns and rules.")
ui.table(pd.DataFrame({"Column": list(pipeline.COLUMNS), "What it must contain": list(pipeline.COLUMNS.values())}))
ui.table(pd.DataFrame({"Rule": list(pipeline.RULES), "Rejects a row when": list(pipeline.RULES.values())}))

# -- Run now ---------------------------------------------------------------
ui.section(
    "Run the pipeline",
    "Running is safe to repeat: it only adds validated rows, and a row already loaded is counted as a duplicate and adds nothing.",
)
include_broken = st.checkbox("Also load the structurally broken sample (shows a failed run)", key="include_broken")
run_staff = ui.confirm_action(
    "p07_run_now",
    "Run the pipeline now: every sample export is validated and its valid rows are **loaded into the shared database** "
    "(rows already loaded are skipped as duplicates).",
    roles=stats.ROLES,
    button_label="Run now",
    default_staff=staff,
)
if run_staff:
    try:
        with db.transaction() as conn:
            summary = stats.run_now(conn, actor_id=run_staff, include_broken=include_broken, confirmed_by=run_staff)
        st.session_state["last_summary"] = summary.to_dict()
    except confirm.ConfirmationRequired as exc:
        st.error(f"Nothing was loaded: {exc}")

uploaded = st.file_uploader("Or ingest one CSV export", type=["csv"])
upload_staff = (
    ui.confirm_action(
        "p07_ingest_upload",
        f"Ingest **{uploaded.name}**: its valid rows are loaded into the shared database.",
        roles=stats.ROLES,
        button_label="Ingest uploaded file",
        default_staff=staff,
    )
    if uploaded is not None
    else None
)
if uploaded is not None and upload_staff:
    try:
        with db.transaction() as conn:
            result = stats.ingest_upload(conn, uploaded.name, uploaded.getvalue(), confirmed_by=upload_staff)
    except confirm.ConfirmationRequired as exc:
        st.error(f"Nothing was loaded: {exc}")
    else:
        st.session_state["last_summary"] = {
            "status": result.status,
            "detail": f"{result.file_name}: {result.rows_loaded} loaded, {result.rows_duplicate} duplicate, "
            f"{result.rows_quarantined} quarantined",
            "rows_read": result.rows_read,
            "rows_loaded": result.rows_loaded,
            "rows_duplicate": result.rows_duplicate,
            "rows_quarantined": result.rows_quarantined,
            "runs": [result.to_dict()],
        }

last = st.session_state.get("last_summary")
if last:
    st.success(f"Run finished: {last['status']}. {last['detail']}")
    ui.metric_row(
        [
            ("Rows read (count)", last["rows_read"]),
            ("Rows loaded (count)", last["rows_loaded"]),
            ("Duplicates skipped (count)", last["rows_duplicate"]),
            ("Rows quarantined (count)", last["rows_quarantined"]),
        ]
    )
    if last["runs"]:
        last_runs = pd.DataFrame(last["runs"]).rename(columns=RUN_COLUMNS)
        last_runs["Error"] = last_runs["Error"].map(short)
        ui.table(last_runs[["File", "Status", "Read (rows)", "Loaded (rows)", "Duplicate (rows)", "Quarantined (rows)", "Run", "Error"]])

conn = db.connect()
try:
    ui.metric_row([("Assessments loaded in database (count)", stats.assessment_count(conn))])

    # -- Scheduled runs -----------------------------------------------------
    sched = stats.last_scheduled_run(conn)
    st.caption(
        "In server mode the Project 20 scheduler runs this same pipeline on a timer (job pipeline_ingest) and skips files "
        "already processed. Last scheduled run: "
        + (f"{sched['started_at']} · {sched['status']} · {sched['detail']}" if sched else "none yet")
    )

    # -- Run history --------------------------------------------------------
    ui.section("Run history", "Newest first. One row per file processed.")
    history = stats.run_history(conn)
    shown_history = history.rename(columns=RUN_COLUMNS)
    shown_history["Error"] = shown_history["Error"].map(short)
    ui.table(
        shown_history[
            [
                "Started",
                "File",
                "Source",
                "Status",
                "Read (rows)",
                "Loaded (rows)",
                "Duplicate (rows)",
                "Quarantined (rows)",
                "Trigger",
                "Run",
                "Error",
            ]
        ]
    )

    # -- Error rates --------------------------------------------------------
    ui.section(
        "Error rates",
        "Rows quarantined as a percentage of rows read. Each file counts once, from its latest run, so loading the same file again does not add to the rates.",
    )
    c1, c2 = st.columns(2)
    by_rule = stats.error_rates_by_rule(conn)
    by_source = stats.error_rates_by_source(conn)
    c1.plotly_chart(
        charts.bar(
            by_rule, "rule", "error_rate_pct", "Validation rule", "Rows quarantined (%)", title="Error rate by rule", horizontal=True
        ),
        width="stretch",
    )
    c2.plotly_chart(
        charts.bar(by_source, "source_id", "error_rate_pct", "Field team source", "Rows quarantined (%)", title="Error rate by source"),
        width="stretch",
    )
    ui.table(by_rule.rename(columns={"rule": "Rule", "rows_quarantined": "Quarantined (rows)", "error_rate_pct": "Error rate (%)"}))
    ui.table(
        by_source.rename(
            columns={
                "source_id": "Source",
                "rows_read": "Read (rows)",
                "rows_quarantined": "Quarantined (rows)",
                "error_rate_pct": "Error rate (%)",
            }
        )
    )

    # -- Quarantine ---------------------------------------------------------
    ui.section("Quarantine", "Rejected rows with the rule and the reason.")
    f1, f2, f3 = st.columns(3)
    run_choice = f1.selectbox("Run", ["All"] + list(history["run_id"]))
    rule_choice = f2.selectbox("Rule", ["All"] + list(pipeline.RULES))
    source_choice = f3.selectbox("Source", ["All"] + list(by_source["source_id"]))
    quarantine = stats.quarantine_rows(
        conn,
        run_id=None if run_choice == "All" else run_choice,
        rule=None if rule_choice == "All" else rule_choice,
        source_id=None if source_choice == "All" else source_choice,
    )
    st.caption(f"{len(quarantine)} quarantined row(s) shown.")
    shown_q = quarantine.rename(
        columns={"run_id": "Run", "file_name": "File", "source_id": "Source", "row_number": "Row no.", "rule": "Rule", "reason": "Reason"}
    )
    shown_q["Raw row (trimmed)"] = quarantine["raw_row"].map(lambda v: short(v, 60))
    ui.table(shown_q[["Run", "File", "Row no.", "Rule", "Reason", "Raw row (trimmed)"]])
    if len(quarantine):
        with st.expander("Full raw rows"):
            ui.table(pd.DataFrame({"Run": quarantine["run_id"], "Row no.": quarantine["row_number"], "Raw row": quarantine["raw_row"]}))
finally:
    conn.close()
