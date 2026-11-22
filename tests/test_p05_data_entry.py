"""Project 05: field note to draft intake form (rule-based extraction, confirmed save)."""

from __future__ import annotations

import json
from pathlib import Path

import p05_drafts as drafts
import p05_extract as ex
import pytest

from shared import db
from shared.confirm import ConfirmationRequired

SAMPLES = Path(__file__).resolve().parents[1] / "projects" / "05-emergency-data-entry" / "sample_data"


@pytest.fixture
def areas(conn):  # noqa: ANN001, ANN201
    return drafts.load_areas(conn)


def run(note: str, areas, known: set[str] | None = None) -> ex.Extraction:  # noqa: ANN001
    return ex.extract(note, areas, known or set())


def sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8").strip()


# -- sample notes -----------------------------------------------------------------
def test_english_sample(areas) -> None:  # noqa: ANN001
    r = run(sample("note_en.txt"), areas)
    assert r.language == "en"
    f = r.fields
    assert (f["head_name"], f["area_id"], f["household_size"], f["primary_need"], f["urgency"]) == (
        "Samir Nassar",
        "AR-08",
        5,
        "food",
        "urgent",
    )
    assert r.missing == []
    assert r.uncertain == ["primary_need"]  # food and shelter both mentioned
    assert "shelter" in r.reasons["primary_need"]


def test_arabic_sample(areas) -> None:  # noqa: ANN001
    r = run(sample("note_ar.txt"), areas)
    assert r.language == "ar"
    f = r.fields
    assert (f["head_name"], f["area_id"], f["primary_need"], f["urgency"]) == ("ليلى منصور", "AR-05", "water_hygiene", "urgent")
    assert f["household_size"] is None
    assert r.missing == ["household_size"]
    assert r.uncertain == []


def test_sample_files_have_no_contact_shapes() -> None:
    for name in ("note_en.txt", "note_ar.txt"):
        assert "@" not in sample(name)


# -- digits and number words ---------------------------------------------------------
@pytest.mark.parametrize(
    ("note", "size"),
    [
        ("Family of 7 in Riverbend", 7),
        ("household of eight people", 8),
        ("There are twelve people living there", 12),
        ("عائلة من ٦ أشخاص", 6),
        ("عدد أفراد الأسرة ٤", 4),
        ("عائلة من خمسة أشخاص", 5),
        ("تسعة أفراد في البيت", 9),
        ("size is 3", 3),
    ],
)
def test_household_size_forms(note: str, size: int, areas) -> None:  # noqa: ANN001
    assert run(note, areas).fields["household_size"] == size


def test_children_count_is_not_household_size(areas) -> None:  # noqa: ANN001
    assert run("She has 3 children and no food", areas).fields["household_size"] is None


def test_two_different_sizes_uncertain(areas) -> None:  # noqa: ANN001
    r = run("Family of five. Later said household of six.", areas)
    assert r.fields["household_size"] == 5
    assert "household_size" in r.uncertain


# -- areas -------------------------------------------------------------------------------
def test_area_by_english_name_arabic_name_and_code(areas) -> None:  # noqa: ANN001
    assert run("They live in Harbor Flats.", areas).fields["area_id"] == "AR-04"
    assert run("يسكنون في سهول المرفأ", areas).fields["area_id"] == "AR-04"
    assert run("Area code AR-12 on the form", areas).fields["area_id"] == "AR-12"
    assert run("المنطقة ar-3", areas).fields["area_id"] == "AR-03"


def test_two_areas_uncertain(areas) -> None:  # noqa: ANN001
    r = run("Moved from Saltmarsh to Pinecrest last week.", areas)
    assert r.fields["area_id"] == "AR-07"
    assert "area_id" in r.uncertain
    assert "Pinecrest" in r.reasons["area_id"]


def test_fuzzy_area_uncertain(areas) -> None:  # noqa: ANN001
    r = run("Living in Riverbent now.", areas)
    assert r.fields["area_id"] == "AR-08"
    assert "area_id" in r.uncertain
    assert "Approximate" in r.reasons["area_id"]


def test_area_missing(areas) -> None:  # noqa: ANN001
    r = run("Head of household: Rana Saleh. Family of four needs food.", areas)
    assert "area_id" in r.missing


# -- needs, urgency, ids, names -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("note", "need"),
    [
        ("Child has a fever and needs a doctor", "health"),
        ("لا يوجد طعام في البيت", "food"),
        ("Cannot pay rent, lost his job", "cash"),
        ("المرحاض مسدود والصرف الصحي يتسرب", "water_hygiene"),
        ("She feels unsafe and reports harassment", "protection"),
        ("Family displaced, needs shelter", "shelter"),
    ],
)
def test_need_keywords_both_languages(note: str, need: str, areas) -> None:  # noqa: ANN001
    r = run(note, areas)
    assert r.fields["primary_need"] == need
    assert "primary_need" not in r.uncertain


def test_two_needs_uncertain_and_no_need_missing(areas) -> None:  # noqa: ANN001
    r = run("Child has a fever and the family has no bread", areas)
    assert "primary_need" in r.uncertain
    assert "primary_need" in run("Visit completed, nothing else to report", areas).missing


@pytest.mark.parametrize(
    ("note", "urgency"),
    [
        ("Needs help urgently", "urgent"),
        ("حالة عاجلة", "urgent"),
        ("Medicine ran out", "high"),
        ("Not urgent, no rush", "low"),
        ("ليس عاجل", "low"),
    ],
)
def test_urgency_cues(note: str, urgency: str, areas) -> None:  # noqa: ANN001
    r = run(note, areas)
    assert r.fields["urgency"] == urgency
    assert "urgency" not in r.uncertain


def test_urgency_default_is_flagged(areas) -> None:  # noqa: ANN001
    r = run("Family of four in Riverbend needs bread.", areas)
    assert r.fields["urgency"] == "normal"
    assert "urgency" in r.uncertain


def test_household_id_checked_against_register(areas, conn) -> None:  # noqa: ANN001
    real = conn.execute("SELECT household_id FROM households LIMIT 1").fetchone()[0]
    known = drafts.known_household_ids(conn)
    assert "household_id" not in run(f"Register entry {real}", areas, known).uncertain
    unknown = run("Register entry HH-99999", areas, known)
    assert unknown.fields["household_id"] == "HH-99999"
    assert "household_id" in unknown.uncertain


def test_head_name_missing_and_variants(areas) -> None:  # noqa: ANN001
    assert "head_name" in run("Family of four needs food in Riverbend", areas).missing
    assert run("Her name is Hala Karam and she needs food", areas).fields["head_name"] == "Hala Karam"
    assert run("اسمه سامر ناصر ويسكن في بستان اللوز", areas).fields["head_name"] == "سامر ناصر"
    assert "head_name" in run("Mr. Khoury visited", areas).uncertain  # one name part only


def test_missing_flags_all_four_required(areas) -> None:  # noqa: ANN001
    r = run("hello", areas)
    assert set(r.missing) == set(ex.REQUIRED_FIELDS)


# -- drafts and saving ---------------------------------------------------------------------------------
GOOD = dict(head_name="Samir Nassar", household_id="", area_id="AR-08", household_size=5, primary_need="food", urgency="urgent", notes="n")


def make_draft(conn) -> str:  # noqa: ANN001
    return drafts.create_draft(conn, sample("note_en.txt"), "OPR-01")


def test_create_draft_row(conn) -> None:  # noqa: ANN001
    d_id = make_draft(conn)
    assert d_id.startswith("DRF-") and len(d_id) == 8
    row = drafts.get_draft(conn, d_id)
    assert row["status"] == "draft" and row["language"] == "en"
    assert row["extracted"]["area_id"] == "AR-08"
    assert set(row["flags"]) >= {"missing", "uncertain"}
    assert row["flags"]["uncertain"] == ["primary_need"]


def test_create_draft_rejects_empty_note(conn) -> None:  # noqa: ANN001
    with pytest.raises(drafts.DraftError):
        drafts.create_draft(conn, "   ", "OPR-01")


def test_save_without_confirmation_writes_nothing(conn) -> None:  # noqa: ANN001
    d_id = make_draft(conn)
    records = db.scalar("SELECT COUNT(*) FROM intake_records", conn=conn)
    audits = db.scalar("SELECT COUNT(*) FROM audit_log", conn=conn)
    for who in (None, "", "NOBODY-99", "FW-01"):
        with pytest.raises(ConfirmationRequired):
            drafts.save_draft(conn, d_id, GOOD, confirmed_by=who)
    assert db.scalar("SELECT COUNT(*) FROM intake_records", conn=conn) == records
    assert db.scalar("SELECT COUNT(*) FROM audit_log", conn=conn) == audits
    assert drafts.get_draft(conn, d_id)["status"] == "draft"


@pytest.mark.parametrize("field", ["head_name", "area_id", "household_size", "primary_need"])
def test_save_refuses_empty_required_field(conn, field: str) -> None:  # noqa: ANN001
    d_id = make_draft(conn)
    records = db.scalar("SELECT COUNT(*) FROM intake_records", conn=conn)
    with pytest.raises(drafts.DraftError, match=field):
        drafts.save_draft(conn, d_id, {**GOOD, field: "" if field != "household_size" else 0}, confirmed_by="OPR-01")
    assert db.scalar("SELECT COUNT(*) FROM intake_records", conn=conn) == records
    assert drafts.get_draft(conn, d_id)["status"] == "draft"


def test_save_refuses_unknown_household_id(conn) -> None:  # noqa: ANN001
    d_id = make_draft(conn)
    with pytest.raises(drafts.DraftError, match="register"):
        drafts.save_draft(conn, d_id, {**GOOD, "household_id": "HH-99999"}, confirmed_by="OPR-01")


def test_confirmed_save_writes_record_version_draft_and_audit(conn) -> None:  # noqa: ANN001
    d_id = make_draft(conn)
    expected_id = drafts.next_record_id(conn)
    record_id = drafts.save_draft(conn, d_id, GOOD, confirmed_by="OPR-01")
    assert record_id == expected_id
    rec = dict(conn.execute("SELECT * FROM intake_records WHERE record_id = ?", (record_id,)).fetchone())
    assert (rec["source"], rec["version"], rec["language"], rec["created_by"]) == ("data_entry", 1, "en", "OPR-01")
    assert rec["raw_note"] == sample("note_en.txt")
    assert rec["household_id"] is None
    ver = dict(conn.execute("SELECT * FROM record_versions WHERE record_id = ?", (record_id,)).fetchone())
    assert ver["version"] == 1 and ver["origin"] == "data_entry"
    assert set(json.loads(ver["data_json"])) == {"head_name", "area_id", "household_size", "primary_need", "urgency", "notes"}
    draft = drafts.get_draft(conn, d_id)
    assert draft["status"] == "saved" and draft["saved_record_id"] == record_id
    audit = conn.execute("SELECT channel, action, actor_id FROM audit_log WHERE entity_id = ?", (record_id,)).fetchone()
    assert tuple(audit) == ("app:05", "draft_saved", "OPR-01")
    with pytest.raises(drafts.DraftError):  # the same draft cannot be saved twice
        drafts.save_draft(conn, d_id, GOOD, confirmed_by="OPR-01")


def test_staff_corrections_are_saved_not_extracted_values(conn) -> None:  # noqa: ANN001
    d_id = make_draft(conn)
    fixed = {**GOOD, "head_name": "Samir N. Nassar", "area_id": "AR-02", "household_size": 6, "primary_need": "shelter", "urgency": "high"}
    record_id = drafts.save_draft(conn, d_id, fixed, confirmed_by="CW-01")
    rec = dict(conn.execute("SELECT * FROM intake_records WHERE record_id = ?", (record_id,)).fetchone())
    assert (rec["head_name"], rec["area_id"], rec["household_size"], rec["primary_need"], rec["urgency"]) == (
        "Samir N. Nassar",
        "AR-02",
        6,
        "shelter",
        "high",
    )
    snap = json.loads(conn.execute("SELECT data_json FROM record_versions WHERE record_id = ?", (record_id,)).fetchone()[0])
    assert snap["area_id"] == "AR-02" and snap["household_size"] == 6


def test_missing_field_filled_by_staff_then_saved(conn) -> None:  # noqa: ANN001
    d_id = drafts.create_draft(conn, sample("note_ar.txt"), "OPR-01")
    assert drafts.get_draft(conn, d_id)["flags"]["missing"] == ["household_size"]
    fields = {**drafts.get_draft(conn, d_id)["extracted"], "household_size": 4}
    record_id = drafts.save_draft(conn, d_id, fields, confirmed_by="OPR-01")
    row = conn.execute("SELECT household_size, language, head_name FROM intake_records WHERE record_id = ?", (record_id,)).fetchone()
    assert tuple(row) == (4, "ar", "ليلى منصور")


def test_discard_needs_confirmation_and_sets_status(conn) -> None:  # noqa: ANN001
    d_id = make_draft(conn)
    with pytest.raises(ConfirmationRequired):
        drafts.discard_draft(conn, d_id, confirmed_by=None)
    assert drafts.get_draft(conn, d_id)["status"] == "draft"
    drafts.discard_draft(conn, d_id, confirmed_by="OPR-01")
    assert drafts.get_draft(conn, d_id)["status"] == "discarded"
    assert d_id not in [d["draft_id"] for d in drafts.open_drafts(conn)]


def test_recent_lists(conn) -> None:  # noqa: ANN001
    d_id = make_draft(conn)
    record_id = drafts.save_draft(conn, d_id, GOOD, confirmed_by="OPR-01")
    assert drafts.recent_drafts(conn)[0]["draft_id"] == d_id
    assert drafts.recent_records(conn)[0]["record_id"] == record_id


# -- the app ------------------------------------------------------------------------------------------------
def test_app_starts(app_test) -> None:  # noqa: ANN001
    at = app_test(5).run()
    assert not at.exception


def test_app_extract_flow(app_test) -> None:  # noqa: ANN001
    at = app_test(5).run()
    next(b for b in at.button if b.label == "Load english sample note").click().run()
    next(b for b in at.button if b.label == "Extract draft form").click().run()
    assert not at.exception
    assert any("Samir Nassar" in (t.value or "") or t.value == "Samir Nassar" for t in at.text_input)


def test_app_walkthrough_two_sample_notes(app_test, conn) -> None:  # noqa: ANN001
    """The README walkthrough through the page: English note saved, Arabic note refused until the size is set."""

    def button(at, label: str):  # noqa: ANN001, ANN202
        return next(b for b in at.button if b.label == label)

    def tick(at, draft_id: str) -> None:  # noqa: ANN001
        next(c for c in at.checkbox if c.key == f"confirm_tick_save_{draft_id}").check()

    records0 = conn.execute("SELECT COUNT(*) FROM intake_records").fetchone()[0]
    at = app_test(5).run()
    button(at, "Load english sample note").click().run()
    button(at, "Extract draft form").click().run()
    assert not at.exception
    en_draft = at.session_state["p05_current"]
    assert any("1 uncertain" in m.value and "0 missing" in m.value for m in at.markdown)
    assert any("CHECK" in m.value and "Primary need" in m.value for m in at.markdown)
    tick(at, en_draft)
    button(at, "Save intake record").click().run()
    assert not at.exception
    en = conn.execute("SELECT * FROM intake_records ORDER BY record_id DESC LIMIT 1").fetchone()
    assert (en["head_name"], en["area_id"], en["household_size"], en["primary_need"], en["urgency"]) == (
        "Samir Nassar",
        "AR-08",
        5,
        "food",
        "urgent",
    )
    assert (en["version"], en["source"], en["language"]) == (1, "data_entry", "en")
    assert conn.execute("SELECT status FROM intake_drafts WHERE draft_id = ?", (en_draft,)).fetchone()[0] == "saved"

    button(at, "Load arabic sample note").click().run()
    button(at, "Extract draft form").click().run()
    ar_draft = at.session_state["p05_current"]
    assert any("Still empty" in w.value and "household size" in w.value for w in at.warning)
    assert any("MISSING" in m.value and "Household size" in m.value for m in at.markdown)
    tick(at, ar_draft)
    button(at, "Save intake record").click().run()
    assert any("household_size is empty" in e.value for e in at.error)
    assert conn.execute("SELECT COUNT(*) FROM intake_records").fetchone()[0] == records0 + 1  # nothing new saved

    next(n for n in at.number_input if n.key == f"hs_{ar_draft}").set_value(4).run()
    tick(at, ar_draft)
    button(at, "Save intake record").click().run()
    assert not at.exception
    ar = conn.execute("SELECT * FROM intake_records ORDER BY record_id DESC LIMIT 1").fetchone()
    assert (ar["area_id"], ar["household_size"], ar["primary_need"], ar["language"]) == ("AR-05", 4, "water_hygiene", "ar")
    assert conn.execute("SELECT COUNT(*) FROM intake_records").fetchone()[0] == records0 + 2
