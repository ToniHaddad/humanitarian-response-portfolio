import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p19_search as p19  # noqa: E402
import streamlit as st  # noqa: E402

from shared import db, ui  # noqa: E402

ui.page_header(project_number=19)

with db.transaction() as conn:
    index = p19.Index(conn)
    reports = p19.list_reports(conn)

st.sidebar.caption("Read-only app: nothing here changes data, so there is no confirmation step.")
threshold = st.sidebar.slider(
    "Relevance threshold (score from 0 to 1)",
    0.0,
    0.6,
    p19.DEFAULT_THRESHOLD,
    0.01,
    help="A passage is shown only when its score reaches this value.",
)
top_k = st.sidebar.slider("Most passages to show (count)", 1, 10, p19.DEFAULT_TOP_K)

question = st.text_input(
    "Ask a question across past situation reports",
    value=str(st.query_params.get("q", "")),
    placeholder="e.g. heating fuel for elderly people in a cold wave",
)
result = index.search(question, threshold=threshold, top_k=top_k)

if result.status == "found":
    st.success(result.message)
    for n, hit in enumerate(result.hits, start=1):
        with st.container(border=True):
            st.markdown(f"**{n}. {hit.title}** ({hit.sitrep_id}), report date **{hit.report_date}**")
            st.caption(f"Relevance score {hit.score:.3f} (0 to 1) | section: {hit.heading}")
            ui.show_text(hit.text)
            with st.expander(f"Open the full source report {hit.sitrep_id}"):
                with db.transaction() as conn:
                    report = p19.get_report(conn, hit.sitrep_id)
                if report:
                    st.markdown(f"**{report['title']}** | {report['report_date']} | area {report['area_id']}")
                    ui.show_text(report["body_md"])
elif result.status == "nothing_found":
    st.warning(result.message)
else:
    st.info(result.message)

ui.section("Browse the library", f"{len(reports)} fictional situation reports, newest first.")
choice = st.selectbox(
    "Open a report",
    [""] + [r["sitrep_id"] for r in reports],
    format_func=lambda s: (
        "Choose a report" if not s else next(f"{r['report_date']} | {r['title']}" for r in reports if r["sitrep_id"] == s)
    ),
)
if choice:
    with db.transaction() as conn:
        report = p19.get_report(conn, choice)
    if report:
        st.markdown(f"**{report['title']}** | {report['report_date']} | area {report['area_id']}")
        ui.show_text(report["body_md"])
