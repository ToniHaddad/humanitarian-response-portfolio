import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p06_matching as matching  # noqa: E402
import p06_merge as merging  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from shared import confirm, db, ui  # noqa: E402

ui.page_header(project_number=6)

acting = ui.staff_selector(roles=merging.ROLES, key="p06_staff", label="Acting as (caseworker or coordinator)")

with st.expander("How entries are standardized and scored", expanded=False):
    st.markdown(
        f"""
**Standardization** (shown next to every original entry):
trim and collapse spaces, case-fold; Arabic diacritics and tatweel removed; أ إ آ → ا, ة → ه, ى → ي;
a hyphenated *al-* / *el-* prefix dropped; Arabic transliterated letter by letter; then a **consonant skeleton**
per word (vowels a e i o u y w removed, q read as k, doubled letters collapsed, final h dropped).
*Mohammad*, *Muhammad* and *محمد* all become **mhmd**.

**Score (0 to 100)** = name {matching.WEIGHTS["name"]:.0f} × name similarity
+ birth year {matching.WEIGHTS["birth_year"]:.0f} (same 1, one year apart 0.5)
+ gender {matching.WEIGHTS["gender"]:.0f} + area {matching.WEIGHTS["area"]:.0f}
+ household size {matching.WEIGHTS["size"]:.0f} (same 1, one apart 0.5).
Name similarity compares first name with first name and family with family (rapidfuzz ratio on the skeleton
or the transliteration, whichever is higher) and keeps the weaker of the two.
A pair is flagged when the score is **{matching.THRESHOLD:.0f} or more** and the names are at least
**{matching.NAME_GATE:.0%}** similar. Only active households are compared, only within one birth year of each
other or with the same family-name skeleton, and a pair already decided (merged or not a duplicate) is never
suggested again.
        """
    )

if "p06_flash" in st.session_state:
    st.success(st.session_state.pop("p06_flash"))

with db.transaction() as _conn:
    candidates = matching.candidate_pairs(_conn)
    entries = matching.standardized_entries(_conn)
    past = merging.history(_conn)
    areas = {r["area_id"]: r["name_en"] for r in _conn.execute("SELECT area_id, name_en FROM areas")}

ui.metric_row(
    [
        ("Active households (count)", len(entries)),
        ("Potential duplicate pairs (count)", len(candidates)),
        ("Decisions recorded (count)", len(past)),
    ]
)

# -- standardized entries -------------------------------------------------
with st.expander("Standardized form of every active entry", expanded=False):
    st.caption("The original entry, the cleaned form a merge writes back, the transliteration and the matching key.")
    ui.table(
        pd.DataFrame(entries).rename(
            columns={
                "household_id": "Household",
                "original": "As entered",
                "cleaned": "Cleaned",
                "transliteration": "Transliteration",
                "skeleton": "Matching key (skeleton)",
                "changed_by_cleaning": "Cleaning changed it",
            }
        ),
        height=320,
    )

# -- candidate list -------------------------------------------------------
ui.section("Potential duplicates", "Best match first. Pick a pair to compare the two records side by side.")
if not candidates:
    st.info("No potential duplicates left to review.")
else:
    ui.table(
        pd.DataFrame(
            [
                {
                    "Score (0-100)": c["score"],
                    "Household A": c["household_a"],
                    "Name A": c["name_a"],
                    "Household B": c["household_b"],
                    "Name B": c["name_b"],
                    "Why flagged": c["reasons"],
                }
                for c in candidates
            ]
        ),
        height=260,
    )

    labels = {f"{c['household_a']}|{c['household_b']}": c for c in candidates}
    pair_key = st.selectbox(
        "Pair to review",
        list(labels),
        format_func=lambda k: (
            f"{labels[k]['household_a']} ↔ {labels[k]['household_b']} · score {labels[k]['score']:.0f} · {labels[k]['name_a']} / {labels[k]['name_b']}"
        ),
        key="p06_pair",
    )
    pair = labels[pair_key]
    a_id, b_id = pair["household_a"], pair["household_b"]
    with db.transaction() as _conn:
        a = db.row_dict(_conn, "households", "household_id", a_id) or {}
        b = db.row_dict(_conn, "households", "household_id", b_id) or {}
        counts_a = merging.child_counts(_conn, a_id)
        counts_b = merging.child_counts(_conn, b_id)
    rows = matching.compare_fields(a, b)

    # -- side by side -----------------------------------------------------
    ui.section(f"{a_id} and {b_id} side by side", "Green: same. Blue: same after standardization. Orange: conflicting values.")
    style = {
        "same": ("background:#E3F4EC", ui.badge("same", "ok")),
        "same after standardization": ("background:#E6F0FA", ui.badge("same after standardization", "ok")),
        "conflict": ("background:#FFF1D6", ui.badge("conflict", "warn")),
    }

    def cell(field: str, value: str) -> str:
        shown = ui.bidi_html(value)
        if field == "area_id" and value in areas:
            shown += f" <small>({html.escape(areas[value])})</small>"
        if field == "head_name":
            shown += (
                f"<br><small>cleaned: {ui.bidi_html(matching.clean_name(value))} · "
                f"transliteration: <code>{html.escape(matching.transliterate(value))}</code> · "
                f"matching key: <code>{html.escape(matching.skeleton(value))}</code></small>"
            )
        return shown

    body = "".join(
        f'<tr style="{style[r["status"]][0]}"><td><strong>{html.escape(r["label"])}</strong></td>'
        f"<td>{cell(r['field'], r['value_a'])}</td>"
        f"<td>{cell(r['field'], r['value_b'])}</td>"
        f"<td>{style[r['status']][1]}</td></tr>"
        for r in rows
    )
    linked = "".join(
        f"<tr><td>Linked {html.escape(t.replace('_', ' '))} (rows)</td><td>{counts_a[t]}</td><td>{counts_b[t]}</td><td></td></tr>"
        for t in counts_a
    )
    st.markdown(
        '<table style="width:100%;border-collapse:collapse" border="1" cellpadding="6">'
        f"<thead><tr><th>Field</th><th>{a_id}</th><th>{b_id}</th><th>Status</th></tr></thead>"
        f"<tbody>{body}{linked}</tbody></table>",
        unsafe_allow_html=True,
    )

    # -- decision ---------------------------------------------------------
    ui.section("Decision", "Merging keeps both rows: the other record is marked merged and its linked records move to the kept one.")
    kept_id = st.radio(
        "Record to keep",
        [a_id, b_id],
        format_func=lambda h: f"{h} · registered {(a if h == a_id else b).get('registered_at', '')}",
        horizontal=True,
        key=f"p06_keep_{pair_key}",
    )
    other_id = b_id if kept_id == a_id else a_id
    choices: dict[str, str] = {}
    conflicts = [r for r in rows if r["choosable"]]
    if conflicts:
        st.markdown("**For each field that differs, choose the value to keep** (the name is written back in its cleaned form)")
        for r in conflicts:
            options = [a_id, b_id]
            value_of = {a_id: r["value_a"], b_id: r["value_b"]}
            choice = st.radio(
                r["label"],
                options,
                index=options.index(kept_id),
                format_func=lambda h, v=value_of: f"{h}: {v[h]}",
                horizontal=True,
                key=f"p06_choice_{pair_key}_{r['field']}",
            )
            choices[r["field"]] = choice
    else:
        st.caption("No differing fields to choose.")
    reason = st.text_area("Reason for the decision (required)", key=f"p06_reason_{pair_key}")

    score_fraction = round(pair["score"] / 100.0, 3)
    left, right = st.columns(2)
    with left:
        staff = ui.confirm_action(
            "p06_merge",
            f"Merge **{other_id}** into **{kept_id}**. {other_id} stays in the database with status *merged*; "
            f"its linked records move to {kept_id}.",
            roles=merging.ROLES,
            button_label="Merge records",
            default_staff=acting,
        )
        if staff:
            try:
                with db.transaction() as conn:
                    rec = merging.merge_households(
                        conn, kept_id, other_id, confirmed_by=staff, field_choices=choices, reason=reason, match_score=score_fraction
                    )
                moved = sum(len(v) for v in json.loads(rec["moved_records_json"]).values())
                st.session_state["p06_flash"] = f"{rec['merge_id']}: {other_id} merged into {kept_id}; {moved} linked record(s) moved."
                st.rerun()
            except (merging.MergeError, confirm.ConfirmationRequired) as exc:
                st.error(f"Nothing was changed: {exc}")
    with right:
        staff_nd = ui.confirm_action(
            "p06_not_dup",
            f"Record that **{a_id}** and **{b_id}** are **different families**. Nothing is merged; the pair is not suggested again.",
            roles=merging.ROLES,
            button_label="Not a duplicate",
            default_staff=acting,
        )
        if staff_nd:
            try:
                with db.transaction() as conn:
                    rec = merging.record_not_duplicate(conn, a_id, b_id, confirmed_by=staff_nd, reason=reason, match_score=score_fraction)
                st.session_state["p06_flash"] = f"{rec['merge_id']}: {a_id} and {b_id} recorded as not a duplicate."
                st.rerun()
            except (merging.MergeError, confirm.ConfirmationRequired) as exc:
                st.error(f"Nothing was changed: {exc}")

# -- history --------------------------------------------------------------
ui.section("Decision history", "Every merge and not-duplicate decision, with who decided, when and why.")
if past:
    ui.table(
        pd.DataFrame(past)
        .rename(
            columns={
                "merge_id": "Decision",
                "decided_at": "When",
                "decision": "Outcome",
                "kept_household_id": "Kept / A",
                "other_household_id": "Merged / B",
                "match_score": "Score (0-1)",
                "decided_by": "By",
                "reason": "Reason",
                "records_moved": "Moved (rows)",
            }
        )[["When", "Decision", "Outcome", "Kept / A", "Merged / B", "Score (0-1)", "By", "Moved (rows)", "Reason"]]
        .assign(Reason=lambda d: d["Reason"].map(lambda t: t if len(str(t)) <= 70 else str(t)[:69] + "…"))
    )
else:
    st.info("No decisions recorded yet.")
