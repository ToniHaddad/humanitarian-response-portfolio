"""Project 06: standardization, duplicate detection, and merges (Part 4: duplicate merges:
confirmation required, history preserved, no records lost)."""

from __future__ import annotations

import json
import sqlite3

import p06_matching as matching
import p06_merge as merging
import pytest

from shared import confirm, db


def scenarios(conn: sqlite3.Connection) -> dict:
    return json.loads(db.get_setting("demo.scenarios", conn=conn))["p06"]


def total_child_rows(conn: sqlite3.Connection) -> int:
    return sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t, _pk, _col in merging.household_child_tables(conn))


def household(conn: sqlite3.Connection, hid: str) -> dict:
    return dict(conn.execute("SELECT * FROM households WHERE household_id = ?", (hid,)).fetchone())


# -- standardization ------------------------------------------------------
def test_arabic_letter_normalization() -> None:
    assert matching.normalize_arabic("أحمد") == "احمد"
    assert matching.normalize_arabic("إلياس") == "الياس"
    assert matching.normalize_arabic("آمال") == "امال"
    assert matching.normalize_arabic("فاطمة") == "فاطمه"  # taa marbuta
    assert matching.normalize_arabic("ليلى") == "ليلي"  # alef maqsura
    assert matching.normalize_arabic("مُحَمَّد") == "محمد"  # diacritics
    assert matching.normalize_arabic("محـــمد") == "محمد"  # tatweel


def test_cleaning_trims_collapses_and_capitalizes() -> None:
    assert matching.clean_name("  mohammad   KHALIL ") == "Mohammad Khalil"
    assert matching.clean_name("al-najjar") == "Al-Najjar"
    assert matching.clean_name(" مُحَمَّد  خليل") == "محمد خليل"
    assert matching.match_form("Ahmad AL-NAJJAR") == "ahmad najjar"


@pytest.mark.parametrize(
    "names",
    [
        ("Mohammad", "Muhammad", "Mohamad", "Mhamad", "محمد", "مُحَمَّد"),
        ("Fatima", "Fatmeh", "Fatme", "فاطمة"),
        ("Youssef", "Yousef", "Yusuf", "يوسف"),
        ("Saleh", "Salih", "صالح"),
        ("Najjar", "Al-Najjar", "Najar", "نجار"),
        ("Rizk", "Rizq", "رزق"),
        ("Elias", "Ilyas", "الياس"),
        ("Ahmad", "Ahmed", "أحمد", "احمد"),
    ],
)
def test_transliteration_skeleton_equalities(names: tuple[str, ...]) -> None:
    keys = {matching.skeleton(n) for n in names}
    assert len(keys) == 1, keys


def test_transliteration_of_arabic() -> None:
    assert matching.transliterate("محمد خليل") == "mhmd khlil"
    assert matching.transliterate("فاطمة") == "fatma"
    assert matching.name_similarity("محمد خليل", "Muhammad Khaleel") == 1.0
    # A shared family name alone is not enough.
    assert matching.name_similarity("Ali Hamdan", "Walid Hamdan") < matching.NAME_GATE


def test_score_parts() -> None:
    a = dict(head_name="Omar Issa", head_birth_year=1980, head_gender="M", area_id="AR-01", household_size=4)
    assert matching.score_pair(a, dict(a, head_name="عمر عيسى"))["score"] == 100.0
    assert matching.score_pair(a, dict(a, head_birth_year=1981))["score"] == 90.0
    assert matching.score_pair(a, dict(a, area_id="AR-02"))["score"] == 90.0
    assert matching.score_pair(a, dict(a, household_size=5))["score"] == 95.0
    assert matching.score_pair(a, dict(a, head_gender="F"))["score"] == 90.0


# -- detection ------------------------------------------------------------
def test_all_planted_pairs_detected(conn: sqlite3.Connection) -> None:
    found = {frozenset((c["household_a"], c["household_b"])): c for c in matching.candidate_pairs(conn)}
    pairs = scenarios(conn)["duplicate_pairs"]
    assert len(pairs) == 8
    for p in pairs:
        key = frozenset((p["household_a"], p["household_b"]))
        assert key in found, p
        assert found[key]["score"] >= matching.THRESHOLD
    variations = {p["variation"] for p in pairs}
    assert variations == {"latin_variant", "arabic_script", "family_variant", "moved_area", "size_changed"}


def test_decided_and_merged_pairs_not_suggested(conn: sqlite3.Connection) -> None:
    sc = scenarios(conn)
    everything = matching.candidate_pairs(conn, threshold=0)
    ids = {frozenset((c["household_a"], c["household_b"])) for c in everything}
    nd = sc["judged_not_duplicate"]
    assert frozenset((nd["household_a"], nd["household_b"])) not in ids
    merged = sc["already_merged"]["merged"]
    assert all(merged not in pair for pair in ids)
    # Without the history row the not-duplicate pair would have been scored.
    conn.execute("DELETE FROM merge_history WHERE other_household_id = ?", (nd["household_b"],))
    again = {frozenset((c["household_a"], c["household_b"])) for c in matching.candidate_pairs(conn, threshold=0)}
    assert frozenset((nd["household_a"], nd["household_b"])) in again


def test_side_by_side_marks_matches_and_conflicts(conn: sqlite3.Connection) -> None:
    pair = next(p for p in scenarios(conn)["duplicate_pairs"] if p["variation"] == "moved_area")
    rows = {r["field"]: r for r in matching.compare_fields(household(conn, pair["household_a"]), household(conn, pair["household_b"]))}
    assert rows["head_name"]["status"] == "same after standardization"
    assert rows["head_name"]["choosable"]  # staff still pick which spelling to keep
    assert rows["area_id"]["status"] == "conflict" and rows["area_id"]["choosable"]
    assert rows["head_birth_year"]["status"] == "same"
    assert len(rows) == len(matching.FIELDS)


# -- merges ---------------------------------------------------------------
def arabic_pair(conn: sqlite3.Connection) -> tuple[str, str]:
    pair = [p for p in scenarios(conn)["duplicate_pairs"] if p["variation"] == "arabic_script"][1]
    return pair["household_a"], pair["household_b"]


def test_child_tables_cover_every_household_link(conn: sqlite3.Connection) -> None:
    tables = {t for t, _pk, _col in merging.household_child_tables(conn)}
    assert {"service_requests", "complaints", "calls", "referrals", "intake_records", "field_assessments", "distributions"} <= tables


@pytest.mark.parametrize("staff", [None, "", "NOPE-99", "OPR-01"])
def test_merge_without_confirmation_changes_nothing(conn: sqlite3.Connection, staff: str | None) -> None:
    kept, other = arabic_pair(conn)
    before_hh = [tuple(r) for r in conn.execute("SELECT * FROM households ORDER BY household_id")]
    before_hist = conn.execute("SELECT COUNT(*) FROM merge_history").fetchone()[0]
    before_children = merging.child_counts(conn, other)
    with pytest.raises(confirm.ConfirmationRequired):
        merging.merge_households(conn, kept, other, confirmed_by=staff, field_choices={}, reason="same family")
    assert [tuple(r) for r in conn.execute("SELECT * FROM households ORDER BY household_id")] == before_hh
    assert conn.execute("SELECT COUNT(*) FROM merge_history").fetchone()[0] == before_hist
    assert merging.child_counts(conn, other) == before_children


def test_merge_requires_reason(conn: sqlite3.Connection) -> None:
    kept, other = arabic_pair(conn)
    with pytest.raises(merging.MergeError):
        merging.merge_households(conn, kept, other, confirmed_by="CW-01", field_choices={}, reason="   ")


def test_merge_with_confirmation_preserves_everything(conn: sqlite3.Connection) -> None:
    kept, other = arabic_pair(conn)
    hh_count = conn.execute("SELECT COUNT(*) FROM households").fetchone()[0]
    children_total = total_child_rows(conn)
    kept_before, other_before = household(conn, kept), household(conn, other)
    kept_children, other_children = merging.child_counts(conn, kept), merging.child_counts(conn, other)
    assert sum(other_children.values()) > 0
    audit_before = conn.execute("SELECT COUNT(*) FROM audit_log WHERE channel = 'app:06'").fetchone()[0]

    rec = merging.merge_households(
        conn,
        kept,
        other,
        confirmed_by="CW-01",
        field_choices={"head_name": other},
        reason="Same head, Arabic and Latin spelling",
        match_score=1.0,
    )
    conn.commit()

    assert rec["merge_id"] == "MRG-0003"
    assert conn.execute("SELECT COUNT(*) FROM households").fetchone()[0] == hh_count
    assert total_child_rows(conn) == children_total
    merged = household(conn, other)
    assert merged["status"] == "merged" and merged["merged_into"] == kept
    assert household(conn, kept)["status"] == "active"
    # The chosen name (from the other record) was written in its cleaned form, script follows it.
    assert household(conn, kept)["head_name"] == matching.clean_name(other_before["head_name"])
    assert household(conn, kept)["name_script"] == other_before["name_script"]
    after = merging.child_counts(conn, kept)
    assert after == {t: kept_children[t] + other_children[t] for t in after}
    assert sum(merging.child_counts(conn, other).values()) == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    hist = dict(conn.execute("SELECT * FROM merge_history WHERE merge_id = ?", (rec["merge_id"],)).fetchone())
    assert hist["decision"] == "merged" and hist["decided_by"] == "CW-01"
    assert json.loads(hist["kept_snapshot_json"]) == kept_before
    assert json.loads(hist["other_snapshot_json"]) == other_before
    assert json.loads(hist["field_choices_json"]) == {"head_name": other}
    moved = json.loads(hist["moved_records_json"])
    assert {t: len(v) for t, v in moved.items()} == {t: n for t, n in other_children.items() if n}
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE channel = 'app:06'").fetchone()[0] >= audit_before + 1
    audit = conn.execute("SELECT * FROM audit_log WHERE channel = 'app:06' AND action = 'merge' AND entity_id = ?", (other,)).fetchone()
    assert audit is not None and audit["actor_id"] == "CW-01"

    # The merged pair is gone from the candidate list and cannot be merged again.
    assert all(other not in (c["household_a"], c["household_b"]) for c in matching.candidate_pairs(conn))
    with pytest.raises(merging.MergeError):
        merging.merge_households(conn, kept, other, confirmed_by="CW-01", field_choices={}, reason="again")


def test_merge_rejects_invalid_field_choice(conn: sqlite3.Connection) -> None:
    kept, other = arabic_pair(conn)
    with pytest.raises(merging.MergeError):
        merging.merge_households(conn, kept, other, confirmed_by="CW-01", field_choices={"status": other}, reason="x")
    with pytest.raises(merging.MergeError):
        merging.merge_households(conn, kept, other, confirmed_by="CW-01", field_choices={"area_id": "HH-00001"}, reason="x")


def test_not_duplicate_decision_recorded(conn: sqlite3.Connection) -> None:
    pair = next(c for c in matching.candidate_pairs(conn) if c["name_a"] == c["name_b"])
    a, b = pair["household_a"], pair["household_b"]
    before = [tuple(r) for r in conn.execute("SELECT * FROM households ORDER BY household_id")]
    with pytest.raises(confirm.ConfirmationRequired):
        merging.record_not_duplicate(conn, a, b, confirmed_by=None, reason="different families")
    with pytest.raises(merging.MergeError):
        merging.record_not_duplicate(conn, a, b, confirmed_by="CRD-01", reason="")
    rec = merging.record_not_duplicate(conn, a, b, confirmed_by="CRD-01", reason="Different families, checked on visit", match_score=0.9)
    assert rec["decision"] == "not_duplicate"
    assert [tuple(r) for r in conn.execute("SELECT * FROM households ORDER BY household_id")] == before
    hist = merging.history(conn)
    assert hist[0]["merge_id"] == rec["merge_id"] and hist[0]["decision"] == "Not a duplicate" and hist[0]["decided_by"] == "CRD-01"
    assert all(frozenset((c["household_a"], c["household_b"])) != frozenset((a, b)) for c in matching.candidate_pairs(conn))
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE channel = 'app:06' AND action = 'not_duplicate'").fetchone()[0] == 1


def test_history_lists_seeded_decisions(conn: sqlite3.Connection) -> None:
    hist = {h["merge_id"]: h for h in merging.history(conn)}
    assert hist["MRG-0001"]["decision"] == "Merged" and hist["MRG-0001"]["decided_by"] == "CW-01"
    assert hist["MRG-0002"]["decision"] == "Not a duplicate" and hist["MRG-0002"]["reason"]


# -- app ------------------------------------------------------------------
def test_app_runs(app_test) -> None:  # noqa: ANN001
    at = app_test(6).run()
    assert not at.exception
    assert any("Potential duplicates" in s.value for s in at.subheader)


def test_app_merge_through_confirmation(app_test, db_path) -> None:  # noqa: ANN001
    at = app_test(6).run()
    pair_key = at.selectbox(key="p06_pair").value
    at.text_area(key=f"p06_reason_{pair_key}").input("Same family, spelling variant").run()
    # Without the tick nothing changes.
    at.button[0].click().run()
    assert not at.exception
    with db.transaction(db_path) as c:
        assert c.execute("SELECT COUNT(*) FROM merge_history").fetchone()[0] == 2
    at.checkbox(key="confirm_tick_p06_merge").check()  # form values are sent with the submit
    at.button[0].click().run()
    assert not at.exception
    with db.transaction(db_path) as c:
        assert c.execute("SELECT COUNT(*) FROM merge_history WHERE decision = 'merged'").fetchone()[0] == 2
    assert any("merged into" in s.value for s in at.success)
