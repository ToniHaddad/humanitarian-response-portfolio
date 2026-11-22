import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import html  # noqa: E402

import p05_drafts as drafts  # noqa: E402
import p05_extract as ex  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from shared import db, ui  # noqa: E402
from shared.call_notes import CATEGORIES, CATEGORY_LABELS  # noqa: E402
from shared.confirm import ConfirmationRequired  # noqa: E402

ui.page_header(project_number=5)
ui.staff_selector(roles=drafts.ALLOWED_ROLES)

SAMPLES = {"English sample note": "note_en.txt", "Arabic sample note": "note_ar.txt"}


def read_sample(name: str) -> str:
    return (HERE / "sample_data" / name).read_text(encoding="utf-8").strip()


def flash(message: str) -> None:
    st.session_state["p05_flash"] = message
    st.rerun()


if "p05_flash" in st.session_state:
    st.success(st.session_state.pop("p05_flash"))

with db.transaction() as conn:
    areas = drafts.load_areas(conn)
    open_list = drafts.open_drafts(conn)
    recent_d = drafts.recent_drafts(conn)
    recent_r = drafts.recent_records(conn)

staff_id = st.session_state.get("acting_staff")

# -- 1. paste or load a note ----------------------------------------------------
ui.section("1. Field note", "Paste a note in Arabic or English, or load a sample, then extract a draft intake form.")
cols = st.columns(len(SAMPLES) + 1)
for col, (label, filename) in zip(cols, SAMPLES.items(), strict=False):
    if col.button(f"Load {label.lower()}", key=f"load_{filename}"):
        st.session_state["p05_note"] = read_sample(filename)
note_text = st.text_area("Pasted field note", key="p05_note", height=140, placeholder="Paste a field note here...")
if cols[-1].button("Extract draft form", type="primary"):
    try:
        with db.transaction() as conn:
            new_id = drafts.create_draft(conn, note_text, staff_id or "")
    except drafts.DraftError as exc:
        st.error(str(exc))
    else:
        st.session_state["p05_current"] = new_id
        flash(f"Draft {new_id} created. Review the flagged fields below.")

# -- 2. review the draft ----------------------------------------------------------
ui.section("2. Review and correct the draft", "Original note on one side, editable fields on the other. Flagged fields are highlighted.")
if not open_list:
    st.info("No open drafts. Load a sample note and press 'Extract draft form'.")
else:
    ids = [d["draft_id"] for d in open_list]
    default = st.session_state.get("p05_current")
    choice = st.selectbox("Open draft", ids, index=ids.index(default) if default in ids else 0)
    draft = next(d for d in open_list if d["draft_id"] == choice)
    extracted, flags = draft["extracted"], draft["flags"]
    reasons = flags.get("reasons", {})
    missing, uncertain = set(flags.get("missing", [])), set(flags.get("uncertain", []))

    left, right = st.columns(2)
    with left:
        st.markdown(f"**Original note** ({'Arabic' if draft['language'] == 'ar' else 'English'}, {draft['draft_id']})")
        ui.show_text(draft["raw_note"])
        st.markdown(
            f"{ui.badge(f'{len(missing)} missing', 'bad' if missing else 'ok')} {ui.badge(f'{len(uncertain)} uncertain', 'warn' if uncertain else 'ok')}",
            unsafe_allow_html=True,
        )

    def field_label(name: str, label: str) -> None:
        """Field heading with a coloured flag and the reason, when flagged."""
        if name in missing:
            flag = ui.badge("MISSING", "bad")
        elif name in uncertain:
            flag = ui.badge("CHECK", "warn")
        else:
            flag = ""
        st.markdown(f"**{html.escape(label)}** {flag}", unsafe_allow_html=True)
        if name in missing or name in uncertain:
            st.caption(reasons.get(name, "Flagged."))

    k = draft["draft_id"]
    area_ids = [""] + [a["area_id"] for a in areas]
    area_names = {a["area_id"]: f"{a['area_id']} · {a['name_en']} / {a['name_ar']}" for a in areas}
    with right:
        field_label("head_name", "Head of household")
        head_name = st.text_input("Head of household", value=extracted.get("head_name") or "", key=f"hn_{k}", label_visibility="collapsed")
        field_label("household_id", "Household ID (optional)")
        household_id = st.text_input("Household ID", value=extracted.get("household_id") or "", key=f"hh_{k}", label_visibility="collapsed")
        field_label("area_id", "Area")
        area_id = st.selectbox(
            "Area",
            area_ids,
            index=area_ids.index(extracted.get("area_id") or ""),
            format_func=lambda a: area_names.get(a, "(choose an area)"),
            key=f"ar_{k}",
            label_visibility="collapsed",
        )
        field_label("household_size", "Household size (people)")
        household_size = st.number_input(
            "Household size",
            min_value=0,
            max_value=60,
            value=int(extracted.get("household_size") or 0),
            key=f"hs_{k}",
            label_visibility="collapsed",
        )
        field_label("primary_need", "Primary need")
        needs = [""] + list(CATEGORIES)
        primary_need = st.selectbox(
            "Primary need",
            needs,
            index=needs.index(extracted.get("primary_need") or ""),
            format_func=lambda c: CATEGORY_LABELS.get(c, "(choose a need)"),
            key=f"pn_{k}",
            label_visibility="collapsed",
        )
        field_label("urgency", "Urgency")
        urgency = st.selectbox(
            "Urgency",
            list(ex.URGENCY_LEVELS),
            index=ex.URGENCY_LEVELS.index(extracted.get("urgency") or "normal"),
            key=f"ur_{k}",
            label_visibility="collapsed",
        )
        notes = st.text_area("Notes", value=extracted.get("notes") or "", key=f"nt_{k}", height=90)

    corrected = dict(
        head_name=head_name,
        household_id=household_id,
        area_id=area_id,
        household_size=household_size,
        primary_need=primary_need,
        urgency=urgency,
        notes=notes,
    )
    still_empty = [
        n
        for n, v in (
            ("head of household", head_name.strip()),
            ("area", area_id),
            ("household size", household_size),
            ("primary need", primary_need),
        )
        if not v
    ]
    if still_empty:
        st.warning("Still empty (required before saving): " + ", ".join(still_empty) + ".")

    # -- 3. save or discard -------------------------------------------------------
    ui.section("3. Save or discard")
    summary = (
        f"Save draft **{k}** as a new intake record: **{head_name or '(no name)'}**, area **{area_id or '(none)'}**, "
        f"size **{household_size}**, need **{CATEGORY_LABELS.get(primary_need, '(none)')}**, urgency **{urgency}**."
    )
    save_col, discard_col = st.columns(2)
    with save_col:
        confirmed = ui.confirm_action(
            f"save_{k}", summary, roles=drafts.ALLOWED_ROLES, button_label="Save intake record", default_staff=staff_id
        )
    with discard_col:
        confirmed_discard = ui.confirm_action(
            f"discard_{k}",
            f"Discard draft **{k}**. It stays in the log as discarded.",
            roles=drafts.ALLOWED_ROLES,
            button_label="Discard draft",
            default_staff=staff_id,
        )
    if confirmed:
        try:
            with db.transaction() as conn:
                record_id = drafts.save_draft(conn, k, corrected, confirmed_by=confirmed)
        except (drafts.DraftError, ConfirmationRequired) as exc:
            st.error(f"{exc} Nothing was saved.")
        else:
            flash(f"Saved intake record {record_id} (version 1) from draft {k}.")
    if confirmed_discard:
        try:
            with db.transaction() as conn:
                drafts.discard_draft(conn, k, confirmed_by=confirmed_discard)
        except (drafts.DraftError, ConfirmationRequired) as exc:
            st.error(f"{exc} Nothing was changed.")
        else:
            flash(f"Draft {k} discarded.")

# -- recent -------------------------------------------------------------------------
ui.section("Recent drafts and saved records")
d_col, r_col = st.columns(2)
with d_col:
    st.markdown("**Drafts**")
    ui.table(pd.DataFrame(recent_d))
with r_col:
    st.markdown("**Saved records (data entry)**")
    ui.table(pd.DataFrame(recent_r))
