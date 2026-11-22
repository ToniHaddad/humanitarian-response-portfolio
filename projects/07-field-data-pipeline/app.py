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

from shared import charts, db, pipeline, ui  # noqa: E402

ui.page_header(project_number=7)
staff = ui.staff_selector() or "system"

# -- Documented schema -----------------------------------------------------
ui.section("Documented schema", "Every export is checked against these columns and rules.")
col_a, col_b = st.columns(2)
with col_a:
    ui.table(pd.DataFrame({"column": list(pipeline.COLUMNS), "description": list(pipeline.COLUMNS.values())}))
with col_b:
    ui.table(pd.DataFrame({"rule": list(pipeline.RULES), "rejects a row when": list(pipeline.RULES.values())}))

# -- Run now ---------------------------------------------------------------
ui.section(
    "Run the pipeline",
    "Running is safe to repeat: it only adds validated rows, and a row already loaded is counted as a duplicate and adds nothing.",
)
include_broken = st.checkbox("Also load the structurally broken sample (shows a failed run)", key="include_broken")
if st.button("Run now", type="primary"):
    with db.transaction() as conn:
        summary = stats.run_now(conn, actor_id=staff, include_broken=include_broken)
    st.session_state["last_summary"] = summary.to_dict()

uploaded = st.file_uploader("Or ingest one CSV export", type=["csv"])
if uploaded is not None and st.button("Ingest uploaded file"):
    with db.transaction() as conn:
        result = pipeline.ingest_bytes(conn, uploaded.name, uploaded.getvalue(), trigger="manual", actor_id=staff)
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
        ui.table(pd.DataFrame(last["runs"]).drop(columns=["source_id"]))

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
    ui.table(history)

    # -- Error rates --------------------------------------------------------
    ui.section("Error rates", "Rows quarantined as a percentage of rows read, over all runs.")
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
    ui.table(by_rule)
    ui.table(by_source)

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
    ui.table(quarantine)
finally:
    conn.close()
