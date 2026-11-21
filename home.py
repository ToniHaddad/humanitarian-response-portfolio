"""Portfolio homepage: twenty cards in five groups, demo status, and demo data reset.

Run with `python run.py` (starts everything) or `streamlit run home.py --server.port 8500`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import html  # noqa: E402

import streamlit as st  # noqa: E402

from shared import config, db, seed, ui  # noqa: E402
from shared.projects import GROUPS, all_projects  # noqa: E402

ui.page_header(subtitle=ui.BRAND_TAGLINE + ". Twenty working demos over one shared database of fictional data.")

seed.ensure_database()
projects = all_projects()
built = sum(p.built for p in projects)

st.markdown(
    "These tools show how small, well-tested software can support an emergency response team: a hotline, "
    "caseworkers, warehouses, distribution sites, a fleet and an operations room. Each card says who uses the "
    "tool, what it helps them do, and how to open it. Everything runs on this computer with no network, "
    "no account and no paid service."
)
ui.metric_row(
    [
        ("Demos ready", f"{built} of {len(projects)}"),
        ("Mode", config.mode().capitalize()),
        ("Demo data generated", (db.get_setting("demo.anchor") or "unknown").replace("T", " ")),
    ]
)

CARD_CSS = """
<style>
.wrl-card { border: 1px solid #c9d3d2; border-radius: 10px; padding: 0.9rem 1rem; height: 100%; background: #fbfdfd; }
.wrl-card h4 { margin: 0 0 0.3rem 0; font-size: 1.12rem; }
.wrl-card .num { color: #0B6E69; font-weight: 700; margin-right: 0.35rem; }
.wrl-card p { margin: 0.25rem 0; font-size: 0.96rem; }
.wrl-card .lbl { color: #4b5757; font-weight: 600; }
.wrl-progress { display: inline-block; background: #ECECEC; color: #333; border: 1px dashed #555; border-radius: 999px;
                padding: 0.1rem 0.6rem; font-size: 0.85rem; font-weight: 600; }
</style>
"""
st.markdown(CARD_CSS, unsafe_allow_html=True)

for code, group_name in GROUPS.items():
    st.header(f"{code}. {group_name}")
    group_projects = [p for p in projects if p.group == code]
    cols = st.columns(2)
    for i, p in enumerate(group_projects):
        with cols[i % 2]:
            if p.built:
                how = f'Open the demo at <a href="{p.url}" target="_blank">{p.url}</a> or run <code>python run.py {p.code}</code>'
            else:
                how = f'<span class="wrl-progress">In progress</span> Launch command once built: <code>python run.py {p.code}</code>'
            st.markdown(
                f'<div class="wrl-card"><h4><span class="num">{p.code}</span>{html.escape(p.title)}</h4>'
                f'<p><span class="lbl">Who uses it:</span> {html.escape(p.user)}</p>'
                f'<p><span class="lbl">Task:</span> {html.escape(p.task)}</p>'
                f'<p><span class="lbl">How to open:</span> {how}</p></div>',
                unsafe_allow_html=True,
            )
            if p.built:
                st.link_button(f"Open {p.code} · {p.title}", p.url, width="stretch")
            st.write("")

st.divider()
st.header("Reset the demo data")
st.markdown(
    "Rebuilds the shared database from the seeded generator, so every demo returns to its starting state. "
    "The same command from a terminal: `python run.py --reset`."
)
confirmed_by = ui.confirm_action(
    "reset_demo",
    "This deletes every change made in the demos (calls, referrals, convoys, approvals) and regenerates the fictional data.",
    button_label="Reset demo data",
)
if confirmed_by:
    path = seed.reset_database()
    st.success(f"Demo data rebuilt by {confirmed_by}: {path.name}")
