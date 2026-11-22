import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import html  # noqa: E402

import p04_referrals as ref  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from shared import db, ui  # noqa: E402
from shared.call_notes import CATEGORIES, CATEGORY_LABELS  # noqa: E402

ui.page_header(project_number=4)
acting = ui.staff_selector(roles=("caseworker", "coordinator"))
ROLES = ("caseworker", "coordinator")
INFER = "(infer from the need text)"
TOP_N = 5


def _households() -> list[dict]:
    return db.query_all(
        """SELECT h.household_id, h.head_name, h.household_size, a.name_en FROM households h
           JOIN areas a ON a.area_id = h.area_id WHERE h.status = 'active' ORDER BY h.household_id"""
    )


def _explanation_html(m: ref.Match) -> str:
    """Explanation in left-to-right text with each Arabic keyword isolated so it reads right-to-left."""
    text = html.escape(m.explanation)
    for kw in m.keywords_found:
        if ui.has_arabic(kw):
            text = text.replace(html.escape(kw), f'<bdi dir="rtl">{html.escape(kw)}</bdi>')
    return f'<div dir="ltr">{text}</div>'


tab_find, tab_list = st.tabs(["Find a service", "Referrals"])

# -- Find a service --------------------------------------------------------
with tab_find:
    ui.section(
        "1. Family and stated need", "Pick a household, or start from an open service request (it fills the household and the need)."
    )
    households = _households()
    by_id = {h["household_id"]: h for h in households}
    mode = st.radio("Start from", ["Household", "Open service request"], index=1, horizontal=True, key="p04_mode")
    request_id: str | None = None
    default_need = ""
    if mode == "Open service request":
        with db.connect() as c:
            reqs = ref.open_requests(c)
        rmap = {r["request_id"]: r for r in reqs}
        request_id = st.selectbox(
            "Open request",
            list(rmap),
            format_func=lambda r: f"{r} · {rmap[r]['household_id']} · {rmap[r]['status']} · {rmap[r]['notes'][:50]}",
            key="p04_request",
        )
        household_id = rmap[request_id]["household_id"]
        default_need = rmap[request_id]["notes"]
    else:
        household_id = st.selectbox(
            "Household",
            list(by_id),
            format_func=lambda h: f"{h} · {by_id[h]['head_name']} · {by_id[h]['name_en']} · size {by_id[h]['household_size']}",
            key="p04_household",
        )
    need = st.text_area("Stated need (English or Arabic)", value=default_need, key=f"p04_need_{request_id or 'free'}")
    if ui.has_arabic(need):
        ui.show_text(need)
    cat_choice = st.selectbox(
        "Category", [INFER, *CATEGORIES], format_func=lambda c: c if c == INFER else CATEGORY_LABELS[c], key="p04_cat"
    )

    if household_id and need.strip():
        with db.connect() as c:
            result = ref.find_matches(c, household_id, need, None if cat_choice == INFER else cat_choice)
        area = result["area"]
        used = result["category"]
        ui.section(
            "2. Ranked matches",
            f"Family location: {area['name_en']} centre (area centroid only). Category used: {CATEGORY_LABELS.get(used, 'none found') if used else 'none found'}.",
        )
        matches = result["matches"][:TOP_N]
        if not matches:
            st.warning("No service with free capacity was found.")
        else:
            ui.table(
                pd.DataFrame(
                    [
                        {
                            "Rank": i,
                            "Provider": m.provider_name,
                            "Service": m.service_name,
                            "Score (0-1)": m.score,
                            "Distance (km)": m.distance_km,
                            "Places left (count)": m.remaining,
                        }
                        for i, m in enumerate(matches, 1)
                    ]
                )
            )
            for i, m in enumerate(matches, 1):
                with st.expander(f"{i}. {m.service_name} at {m.provider_name} (score {m.score:.2f})", expanded=(i == 1)):
                    st.markdown(_explanation_html(m), unsafe_allow_html=True)
                    st.caption(
                        "Score parts (0-1): "
                        + ", ".join(f"{k} {v:.2f}" for k, v in m.components.items())
                        + f". Weights: category {ref.W_CATEGORY}, keywords {ref.W_KEYWORDS}, distance {ref.W_DISTANCE}, capacity {ref.W_CAPACITY}."
                    )
                    st.markdown(ui.bidi_html(m.provider_name_ar), unsafe_allow_html=True)
                    if st.button("Create referral from this match", key=f"p04_create_{m.provider_id}_{m.service_name}"):
                        with db.transaction() as c:
                            new_id = ref.create_referral(c, household_id, need, m, acting or "", request_id)
                        st.success(f"Referral {new_id} created with status proposed. Open the Referrals tab to approve it.")
        if result["excluded"]:
            ui.section("Excluded services")
            ui.table(
                pd.DataFrame(
                    [{"Provider": m.provider_name, "Service": m.service_name, "Reason": m.excluded_reason} for m in result["excluded"]]
                )
            )
    else:
        st.info("Enter the stated need to see matches.")

# -- Referrals -------------------------------------------------------------
with tab_list:
    ui.section("Referrals", "Each referral moves in order: proposed, approved, provider response, outcome.")
    flash = st.session_state.pop("p04_flash", None)
    if flash:
        st.success(flash)
    status_pick = st.selectbox("Filter by status", ["all", *ref.STATUSES], key="p04_status")
    with db.connect() as c:
        rows = ref.list_referrals(c, None if status_pick == "all" else status_pick)
        all_rows = ref.list_referrals(c)
    counts = {s: sum(1 for r in all_rows if r["status"] == s) for s in ref.STATUSES}
    ui.metric_row([(s.replace("_", " ").capitalize() + " (count)", counts[s]) for s in ref.STATUSES])
    ui.table(
        pd.DataFrame(rows)[["referral_id", "household_id", "provider_name", "service_name", "status", "next_step", "match_score"]]
        if rows
        else pd.DataFrame(columns=["referral_id", "status"])
    )
    if rows:
        chosen = st.selectbox("Open a referral", [r["referral_id"] for r in rows], key="p04_open")
        r = next(x for x in rows if x["referral_id"] == chosen)
        st.markdown(f"**{r['referral_id']}** · {r['service_name']} at {r['provider_name']} · status **{r['status']}**")
        ui.show_text(r["need_text"], "Stated need")
        ui.show_text(r["match_explanation"], "Why this match")
        st.info(f"Next step: {r['next_step']}")
        if r["status"] == "proposed":
            staff = ui.confirm_action(
                f"approve_{chosen}",
                f"Approve referral {chosen} of {r['household_id']} to {r['service_name']} at {r['provider_name']}?",
                roles=ROLES,
                button_label="Approve referral",
                default_staff=acting,
            )
            if staff:
                with db.transaction() as c:
                    ref.approve(c, chosen, confirmed_by=staff)
                st.session_state["p04_flash"] = f"{chosen} approved by {staff}."
                st.rerun()
        elif r["status"] == "approved":
            with st.form(f"p04_resp_{chosen}"):
                resp = st.radio("Provider response", ["accepted", "declined"], horizontal=True)
                note = st.text_input("Note")
                if st.form_submit_button("Record provider response", type="primary"):
                    with db.transaction() as c:
                        ref.record_provider_response(c, chosen, resp, note, acting or "")
                    st.session_state["p04_flash"] = f"Provider response recorded: {resp}."
                    st.rerun()
        elif r["status"] == "accepted":
            got = st.radio("Did the family receive support?", ["Yes", "No"], horizontal=True, key=f"p04_got_{chosen}")
            note = st.text_input("Note", key=f"p04_outnote_{chosen}")
            staff = ui.confirm_action(
                f"outcome_{chosen}",
                f"Confirm that the family of {r['household_id']} {'received' if got == 'Yes' else 'did NOT receive'} support for {chosen}?",
                roles=ROLES,
                button_label="Confirm outcome",
                default_staff=acting,
            )
            if staff:
                with db.transaction() as c:
                    ref.confirm_outcome(c, chosen, got == "Yes", note, confirmed_by=staff)
                st.session_state["p04_flash"] = f"Outcome recorded for {chosen}."
                st.rerun()
