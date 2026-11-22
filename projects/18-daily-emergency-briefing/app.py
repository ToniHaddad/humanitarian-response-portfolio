import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p18_briefing as brf  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from shared import confirm, db, ui  # noqa: E402

ui.page_header(project_number=18)

conn = db.connect()
acting = ui.staff_selector(roles=brf.APPROVER_ROLES, key="p18_acting")

# -- create a draft ------------------------------------------------------------
ui.section("1. Generate a draft", "Figures are calculated from the records for the chosen day. Nothing is typed in by hand.")
day = st.date_input("Briefing date", value=date.fromisoformat(brf.default_day(conn)), key="p18_day")
if st.button("Generate draft briefing", key="p18_generate", disabled=acting is None):
    try:
        with db.transaction() as wconn:
            new_id = brf.create_draft(wconn, day.isoformat(), acting)
        st.session_state["p18_current"] = new_id
        st.success(f"Draft {new_id} created for {day.isoformat()}.")
    except (brf.BriefingError, confirm.ConfirmationRequired) as exc:
        st.error(str(exc))

briefings = brf.list_briefings(conn)
if not briefings:
    st.info("No briefings yet. Generate a draft above.")
    st.stop()
ids = [b["briefing_id"] for b in briefings]
labels = {b["briefing_id"]: f"{b['briefing_id']} · {b['briefing_date']} · {b['status']}" for b in briefings}
if st.session_state.get("p18_current") not in ids:
    st.session_state["p18_current"] = ids[0]
current = st.selectbox("Briefing", ids, format_func=lambda i: labels[i], key="p18_current")
b = brf.get_briefing(conn, current)
is_draft = b["status"] == "draft"

# -- figures and their sources -------------------------------------------------
ui.section("2. Figures and their source records", "Units are shown on every figure; parcels, meals and kits are never added together.")
src = pd.DataFrame(
    [
        {
            "Figure": s["figure_key"],
            "Value": int(s["figure_value"]),
            "Unit": s["unit"],
            "Source table": s["source_table"],
            "Source records": s["source_record_ids"],
        }
        for s in b["sources"]
    ]
)
ui.table(src)

# -- conflicts ------------------------------------------------------------------
ui.section("3. Conflicting totals")
if not b["conflicts"]:
    st.success("No field update conflicts with the records for this day.")
for c in b["conflicts"]:
    state = "reviewed" if c["decision"] else "NOT REVIEWED"
    st.markdown(
        f"{ui.badge('CONFLICT', 'bad' if not c['decision'] else 'warn')} **{c['update_id']}** at {c['site_id']} ({c['team']}): "
        f"field update says **{c['reported']}** {c['unit']}, records give **{c['computed']}** "
        f"(difference {c['difference']:+d}). Status: {state}.",
        unsafe_allow_html=True,
    )
    st.caption(f"Field update: {c['update_text']}  Records (`distributions`): {', '.join(c['record_ids']) or 'none'}")
    if c["decision"]:
        st.write(f"Decision: {brf.DECISIONS[c['decision']]}" + (f" Note: {c['note']}" if c["note"] else ""))
    if is_draft:
        with st.form(f"p18_review_{c['update_id']}"):
            keys = list(brf.DECISIONS)
            decision = st.radio(
                "Decision", keys, format_func=lambda k: brf.DECISIONS[k], index=keys.index(c["decision"]) if c["decision"] else 0
            )
            note = st.text_input("Note", value=c["note"])
            if st.form_submit_button("Save review"):
                try:
                    with db.transaction() as wconn:
                        brf.review_conflict(wconn, current, c["update_id"], decision, note, acting)
                    st.rerun()
                except (brf.BriefingError, confirm.ConfirmationRequired) as exc:
                    st.error(str(exc))

# -- edit -----------------------------------------------------------------------
ui.section("4. Edit the briefing text")
if is_draft:
    text = st.text_area("Briefing (Markdown)", value=b["content_md"], height=320, key=f"p18_text_{current}")
    if st.button("Save edits", key="p18_save"):
        try:
            with db.transaction() as wconn:
                brf.save_edit(wconn, current, text, acting)
            st.success("Edits saved.")
        except (brf.BriefingError, confirm.ConfirmationRequired) as exc:
            st.error(str(exc))
else:
    st.markdown(b["content_md"])

# -- approve and export ---------------------------------------------------------
ui.section("5. Approve and export")
if is_draft:
    pending = [c["update_id"] for c in b["conflicts"] if c["decision"] is None]
    if pending:
        st.warning("Review every conflict before approving. Not reviewed: " + ", ".join(pending))
    else:
        staff = ui.confirm_action(
            f"p18_approve_{current}",
            f"Approve briefing {current} for {b['briefing_date']}? It can no longer be edited, and the Markdown export becomes available.",
            roles=brf.APPROVER_ROLES,
            button_label="Approve briefing",
        )
        if staff:
            try:
                with db.transaction() as wconn:
                    brf.approve(wconn, current, staff)
                st.rerun()
            except (brf.BriefingError, confirm.ConfirmationRequired) as exc:
                st.error(str(exc))
else:
    st.success(f"Approved by {b['approved_by']} at {b['approved_at']}.")
    md = brf.export_markdown(conn, current)
    st.download_button("Download approved briefing (Markdown)", md, file_name=f"{current}.md", mime="text/markdown", key="p18_download")
    unresolved = brf.unresolved_items(b)
    if unresolved:
        st.warning(f"{len(unresolved)} unresolved item(s) are listed in the export.")
    with st.expander("Preview export"):
        st.markdown(md)
