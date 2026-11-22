import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p12_reconciliation as rec  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from shared import charts, clock, confirm, db, ui  # noqa: E402

ui.page_header(project_number=12)

if "p12_flash" in st.session_state:
    st.success(st.session_state.pop("p12_flash"))

conn = db.connect()
scenario = rec.default_scenario(conn)
site_rows = rec.sites(conn)
item_rows = rec.parcel_items(conn)
site_ids = [s["site_id"] for s in site_rows]
site_names = {s["site_id"]: s["name"] for s in site_rows}
item_ids = [i["item_id"] for i in item_rows]
item_names = {i["item_id"]: i["name"] for i in item_rows}

# -- pickers -----------------------------------------------------------------
ui.section("Site, period and item")
c1, c2, c3 = st.columns([2, 2, 2])
site_default = scenario.get("site_id")
site_id = c1.selectbox(
    "Site",
    site_ids,
    index=site_ids.index(site_default) if site_default in site_ids else 0,
    format_func=lambda s: f"{s} · {site_names[s]}",
    key="p12_site",
)
period_default = (
    date.fromisoformat(scenario["period_start"]) if scenario else clock.today(),
    date.fromisoformat(scenario["period_end"]) if scenario else clock.today(),
)
period = c2.date_input("Period (start and end, inclusive)", value=period_default, key="p12_period")
item_default = scenario.get("item_id")
item_id = c3.selectbox(
    "Parcel item",
    item_ids,
    index=item_ids.index(item_default) if item_default in item_ids else 0,
    format_func=lambda i: f"{i} · {item_names[i]}",
    key="p12_item",
)
st.caption("Parcels only. Ready-to-eat meals are a different unit and are not part of this reconciliation.")

if not isinstance(period, (tuple, list)) or len(period) != 2:
    st.info("Choose both a start date and an end date.")
    st.stop()
start, end = period[0].isoformat(), period[1].isoformat()

# -- figures -----------------------------------------------------------------
fig = rec.figures(conn, site_id, item_id, start, end)
ui.section("Figures", f"{item_id}, in parcels, {start} to {end}")
ui.metric_row(
    [
        ("Planned (parcels)", fig["planned"]),
        ("Dispatched (parcels)", fig["dispatched"]),
        ("Distributed (parcels)", fig["distributed"]),
        ("Returned (parcels)", fig["returned"]),
        (
            "Unaccounted for (parcels)",
            fig["unaccounted"],
            "dispatched - distributed - returned; negative means more handed out than dispatched",
        ),
    ]
)
if fig["unaccounted"] < 0:
    st.warning(f"Negative: {-fig['unaccounted']} more parcel(s) were distributed or returned than were dispatched.")
st.info(rec.explain(item_id, fig))

# Both parcel items as separate rows, never added together.
rows = []
for i in item_ids:
    f = rec.figures(conn, site_id, i, start, end)
    rows.append(
        {
            "Item": f"{i} · {item_names[i]}",
            "Planned": f["planned"],
            "Dispatched": f["dispatched"],
            "Distributed": f["distributed"],
            "Returned": f["returned"],
            "Unaccounted": f["unaccounted"],
            "Undispatched vs plan": f["not_dispatched_against_plan"],
        }
    )
st.caption("All figures are parcels, one row per parcel item.")
ui.table(pd.DataFrame(rows))

chart_df = pd.DataFrame(
    {
        "Figure": ["Planned", "Dispatched", "Distributed", "Returned", "Unaccounted"],
        "Parcels": [fig["planned"], fig["dispatched"], fig["distributed"], fig["returned"], fig["unaccounted"]],
    }
)
st.plotly_chart(charts.bar(chart_df, "Figure", "Parcels", "Figure", f"Parcels ({item_id}, count)"), width="stretch")

# -- discrepancies -----------------------------------------------------------
disc = rec.discrepancies(conn, site_id, item_id, start, end)
ui.section("Discrepancies", "Households recorded more than once, records without a matching dispatch, and any unaccounted quantity.")
if not disc:
    st.success("No discrepancies for this site, period and item.")
else:

    def short(text: str, n: int = 50) -> str:
        return text if len(text) <= n else text[: n - 1].rstrip() + "…"

    ui.table(
        pd.DataFrame(
            [
                {
                    "Status": "resolved" if d["resolved"] else "unresolved",
                    "Type": d["type"].replace("_", " "),
                    "Key": d["key"],
                    "What": short(d["description"]),
                    "Latest note": short(d["note"] or ""),
                    "By": d["note_staff"] or "",
                }
                for d in disc
            ]
        )
    )
    with st.expander("Full text of each discrepancy and its latest note"):
        for d in disc:
            st.markdown(f"**{d['key']}** ({'resolved' if d['resolved'] else 'unresolved'}): {d['description']}")
            if d["note"]:
                st.markdown(f"> {d['note']} ({d['note_staff']}, {d['note_at']})")
    st.markdown("**Record a resolution note**")
    keys = [d["key"] for d in disc]
    key = st.selectbox("Discrepancy", keys, key="p12_note_key")
    note = st.text_area("Resolution note", key="p12_note_text")
    resolved = st.checkbox("Mark as resolved", key="p12_note_resolved")
    chosen = next(d for d in disc if d["key"] == key)
    noter = ui.confirm_action(
        "p12_note",
        f"Save a resolution note for **{key}**, marked **{'resolved' if resolved else 'not resolved'}**. "
        "The latest note decides whether the item counts as unresolved in the approved report.",
        button_label="Save note",
    )
    if noter:
        try:
            with db.transaction() as wconn:
                rec.add_note(wconn, site_id, start, end, key, chosen["type"], note, resolved, noter)
            st.session_state["p12_flash"] = f"Note saved for {key}, confirmed by {noter}."
            st.rerun()
        except (ValueError, confirm.ConfirmationRequired) as exc:
            st.error(f"Nothing was saved. {exc}")

# -- approval ----------------------------------------------------------------
ui.section("Approve and export", "Sign-off needs a coordinator. The Markdown lists every unresolved item.")
unresolved = [d["key"] for d in disc if not d["resolved"]]
st.markdown(f"Unresolved now: {', '.join(unresolved) if unresolved else 'none'}")
approver = ui.confirm_action(
    "p12_approve",
    f"Approve the reconciliation for **{site_id}**, {start} to {end}, {item_id}: "
    f"{fig['unaccounted']} parcel(s) unaccounted for, {len(unresolved)} unresolved item(s).",
    roles=rec.APPROVER_ROLES + ("admin",),
    button_label="Approve report",
)
if approver:
    try:
        with db.transaction() as wconn:
            report_id = rec.approve_report(wconn, site_id, item_id, start, end, confirmed_by=approver)
        st.session_state["p12_last_report"] = report_id
    except Exception as exc:  # ConfirmationRequired and similar: nothing was written
        st.error(str(exc))

reports = rec.earlier_reports(conn, site_id)
last = st.session_state.get("p12_last_report")
if last:
    latest = next((r for r in reports if r["report_id"] == last), None)
    if latest:
        st.success(f"Report {last} approved by {latest['approved_by']}.")
        st.download_button(
            "Download approved report (Markdown)", latest["markdown"], file_name=f"{last}.md", mime="text/markdown", key="p12_dl_last"
        )
        with st.expander("Preview"):
            st.markdown(latest["markdown"])

if reports:
    ui.section("Approved reports for this site")
    ui.table(pd.DataFrame([{k: r[k] for k in ("report_id", "period_start", "period_end", "approved_by", "approved_at")} for r in reports]))
    for r in reports[:5]:
        st.download_button(
            f"Download {r['report_id']}",
            r["markdown"],
            file_name=f"{r['report_id']}.md",
            mime="text/markdown",
            key=f"p12_dl_{r['report_id']}",
        )
