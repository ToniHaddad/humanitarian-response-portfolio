"""Project 04: beneficiary referral tracker (rule-based matching and the referral lifecycle)."""

from __future__ import annotations

import sqlite3

import p04_referrals as p04
import pytest

from shared import db
from shared.confirm import ConfirmationRequired
from shared.geo import distance_km

HH = "HH-00009"  # Harbor Flats (AR-04) on every seed
FEVER = "My child has had a fever for three days and we need medicine."


def _top(conn: sqlite3.Connection, need: str = FEVER, hh: str = HH, category: str | None = None) -> p04.Match:
    return p04.find_matches(conn, hh, need, category)["matches"][0]


def _new_referral(conn: sqlite3.Connection) -> str:
    return p04.create_referral(conn, HH, FEVER, _top(conn), "CW-01")


def _status(conn: sqlite3.Connection, rid: str) -> str:
    return conn.execute("SELECT status FROM referrals WHERE referral_id = ?", (rid,)).fetchone()[0]


def test_app_starts(app_test) -> None:
    at = app_test(4).run()
    assert not at.exception


def test_weights_sum_to_one() -> None:
    assert p04.W_CATEGORY + p04.W_KEYWORDS + p04.W_DISTANCE + p04.W_CAPACITY == pytest.approx(1.0)


def test_each_score_component() -> None:
    assert p04.category_component("health", "health") == 1.0
    assert p04.category_component("food", "health") == 0.0
    assert p04.category_component("food", None) == 0.0
    assert p04.keyword_component(0) == 0.0
    assert p04.keyword_component(1) == 0.5
    assert p04.keyword_component(2) == 1.0
    assert p04.keyword_component(5) == 1.0
    assert p04.distance_component(0) == 1.0
    assert p04.distance_component(20) == pytest.approx(0.5)
    assert p04.distance_component(100) == 0.0
    assert p04.capacity_component(10, 20) == 0.5
    assert p04.capacity_component(0, 20) == 0.0
    assert p04.capacity_component(5, 0) == 0.0
    assert p04.combine(1, 1, 1, 1) == 1.0
    assert p04.combine(1, 0, 0, 0) == p04.W_CATEGORY
    assert p04.combine(0, 0.5, 0, 0) == pytest.approx(p04.W_KEYWORDS / 2, abs=0.001)


def test_match_score_is_the_weighted_sum_of_its_components(conn: sqlite3.Connection) -> None:
    m = _top(conn)
    c = m.components
    assert m.score == p04.combine(c["category"], c["keywords"], c["distance"], c["capacity"])


def test_health_need_ranks_a_health_service_first(conn: sqlite3.Connection) -> None:
    result = p04.find_matches(conn, HH, FEVER)
    assert result["category"] == "health"
    top = result["matches"][0]
    assert (top.provider_id, top.service_name) == ("PRV-02", "General clinic")
    scores = [m.score for m in result["matches"]]
    assert scores == sorted(scores, reverse=True)


def test_other_needs_rank_their_own_category(conn: sqlite3.Connection) -> None:
    assert _top(conn, "no food left, we are hungry").service_category == "food"
    assert _top(conn, "the landlord wants us to leave, eviction").service_category == "protection"


def test_category_can_be_chosen_by_the_caseworker(conn: sqlite3.Connection) -> None:
    result = p04.find_matches(conn, HH, FEVER, category="food")
    assert result["category"] == "food"
    assert result["matches"][0].service_category == "food"


def test_infer_category_none_when_nothing_matches(conn: sqlite3.Connection) -> None:
    assert p04.infer_category(conn, "zzzz qqqq") is None
    assert p04.find_matches(conn, HH, "zzzz qqqq")["category"] is None


def test_excluded_services_with_reason(conn: sqlite3.Connection) -> None:
    # School fees support (cash) accepts no new referrals and is full in the seed.
    result = p04.find_matches(conn, HH, "school fees for my son", category="cash")
    excluded = {m.service_name: m.excluded_reason for m in result["excluded"]}
    assert "not accepting new referrals" in excluded["School fees support"]
    assert "School fees support" not in [m.service_name for m in result["matches"]]


def test_no_capacity_is_excluded(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE provider_services SET current_load = weekly_capacity WHERE service_name = 'General clinic'")
    result = p04.find_matches(conn, HH, FEVER)
    assert "General clinic" not in [m.service_name for m in result["matches"]]
    excluded = {m.service_name: m.excluded_reason for m in result["excluded"]}
    assert "no places left" in excluded["General clinic"]
    assert result["matches"][0].service_name == "Mobile clinic visits"


def test_not_accepting_new_is_excluded(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE provider_services SET accepts_new = 0 WHERE service_name = 'General clinic'")
    result = p04.find_matches(conn, HH, FEVER)
    assert "General clinic" not in [m.service_name for m in result["matches"]]
    assert any(m.service_name == "General clinic" and "not accepting" in m.excluded_reason for m in result["excluded"])


def test_excluded_match_cannot_become_a_referral(conn: sqlite3.Connection) -> None:
    result = p04.find_matches(conn, HH, "school fees", category="cash")
    with pytest.raises(ValueError):
        p04.create_referral(conn, HH, "school fees", result["excluded"][0], "CW-01")


def test_explanation_lists_category_keywords_distance_and_capacity(conn: sqlite3.Connection) -> None:
    text = _top(conn).explanation
    assert "Category match: health" in text
    assert "Keywords found: fever" in text
    assert "km from Harbor Flats" in text
    assert "10 of 80 places left this week" in text
    assert "Eligibility:" in text


def test_arabic_need_matches_arabic_keywords(conn: sqlite3.Connection) -> None:
    m = _top(conn, "ابني عنده حرارة ونحتاج طبيب")
    assert (m.provider_id, m.service_name) == ("PRV-02", "General clinic")
    assert set(m.keywords_found) == {"حرارة", "طبيب"}
    assert "حرارة" in m.explanation


def test_arabic_article_and_diacritics_are_normalised() -> None:
    assert p04.keyword_hits("الطبيبُ", "طبيب,fever") == ["طبيب"]
    assert p04.keyword_hits("FEVER, Fevers!", "fever") == ["fever"]
    assert p04.keyword_hits("the landlord said leave", "صاحب البيت,landlord") == ["landlord"]


def test_distance_uses_area_centroid(conn: sqlite3.Connection) -> None:
    area = p04.household_area(conn, HH)
    assert (area["x_km"], area["y_km"]) == (8.0, 46.0)
    prov = conn.execute("SELECT x_km, y_km FROM service_providers WHERE provider_id = 'PRV-02'").fetchone()
    expected = distance_km((8.0, 46.0), (prov["x_km"], prov["y_km"]))
    assert _top(conn).distance_km == expected == 1.45
    # a household in another area gets different distances to the same provider
    other = conn.execute("SELECT household_id FROM households WHERE area_id = 'AR-12' AND status = 'active' LIMIT 1").fetchone()[0]
    far = next(m for m in p04.find_matches(conn, other, FEVER)["matches"] if m.provider_id == "PRV-02")
    assert far.distance_km == distance_km((30.0, 12.0), (prov["x_km"], prov["y_km"]))


def test_open_request_fills_household_and_need(conn: sqlite3.Connection) -> None:
    reqs = p04.open_requests(conn)
    assert reqs and all(r["status"] != "closed" and r["household_id"] for r in reqs)
    r = reqs[0]
    assert p04.find_matches(conn, r["household_id"], r["notes"])["matches"]


def test_lifecycle_full_path_with_audit(conn: sqlite3.Connection) -> None:
    rid = _new_referral(conn)
    assert rid == "REF-0031"
    assert _status(conn, rid) == "proposed"
    p04.approve(conn, rid, "CW-01")
    assert _status(conn, rid) == "approved"
    p04.record_provider_response(conn, rid, "accepted", "Appointment offered.", "CW-01")
    assert _status(conn, rid) == "accepted"
    p04.confirm_outcome(conn, rid, True, "Family confirmed.", "CW-01")
    row = conn.execute("SELECT * FROM referrals WHERE referral_id = ?", (rid,)).fetchone()
    assert row["status"] == "support_received"
    assert row["family_received"] == 1
    assert row["approved_by"] == "CW-01" and row["approved_at"] == "2026-11-16T10:00:00"
    assert row["provider_response"] == "accepted" and row["outcome_confirmed_by"] == "CW-01"
    actions = [
        r[0] for r in conn.execute("SELECT action FROM audit_log WHERE entity_id = ? AND channel = 'app:04' ORDER BY audit_id", (rid,))
    ]
    assert actions == ["create", "approve", "provider_response", "confirm_outcome"]


def test_outcome_not_received(conn: sqlite3.Connection) -> None:
    rid = _new_referral(conn)
    p04.approve(conn, rid, "CRD-01")
    p04.record_provider_response(conn, rid, "accepted", "", "CW-01")
    p04.confirm_outcome(conn, rid, False, "Could not travel.", "CRD-01")
    row = conn.execute("SELECT status, family_received FROM referrals WHERE referral_id = ?", (rid,)).fetchone()
    assert (row["status"], row["family_received"]) == ("support_not_received", 0)


def test_declined_ends_the_referral(conn: sqlite3.Connection) -> None:
    rid = _new_referral(conn)
    p04.approve(conn, rid, "CW-01")
    p04.record_provider_response(conn, rid, "declined", "Full this week.", "CW-01")
    assert _status(conn, rid) == "declined"
    with pytest.raises(p04.InvalidTransition):
        p04.confirm_outcome(conn, rid, True, "", "CW-01")
    with pytest.raises(p04.InvalidTransition):
        p04.record_provider_response(conn, rid, "accepted", "", "CW-01")
    with pytest.raises(p04.InvalidTransition):
        p04.approve(conn, rid, "CW-01")


def test_status_order_is_enforced(conn: sqlite3.Connection) -> None:
    rid = _new_referral(conn)
    with pytest.raises(p04.InvalidTransition):  # no response before approval
        p04.record_provider_response(conn, rid, "accepted", "", "CW-01")
    with pytest.raises(p04.InvalidTransition):  # no outcome before acceptance
        p04.confirm_outcome(conn, rid, True, "", "CW-01")
    p04.approve(conn, rid, "CW-01")
    with pytest.raises(p04.InvalidTransition):  # cannot approve twice
        p04.approve(conn, rid, "CW-01")
    with pytest.raises(p04.InvalidTransition):  # no outcome while only approved
        p04.confirm_outcome(conn, rid, True, "", "CW-01")
    with pytest.raises(ValueError):
        p04.record_provider_response(conn, rid, "maybe", "", "CW-01")
    assert _status(conn, rid) == "approved"


def test_provider_response_without_confirmation_raises_and_changes_nothing(conn: sqlite3.Connection) -> None:
    rid = _new_referral(conn)
    p04.approve(conn, rid, "CW-01")
    audits = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    for who in (None, "", "NOBODY", "OPR-01"):  # missing, blank, unknown, wrong role
        with pytest.raises(ConfirmationRequired):
            p04.record_provider_response(conn, rid, "accepted", "", who)
    row = conn.execute("SELECT status, provider_response FROM referrals WHERE referral_id = ?", (rid,)).fetchone()
    assert (row["status"], row["provider_response"]) == ("approved", None)
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == audits


def test_approve_without_confirmation_raises_and_changes_nothing(conn: sqlite3.Connection) -> None:
    rid = _new_referral(conn)
    audits = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    for who in (None, "", "NOBODY", "OPR-01"):  # missing, blank, unknown, wrong role
        with pytest.raises(ConfirmationRequired):
            p04.approve(conn, rid, who)
    row = conn.execute("SELECT status, approved_by FROM referrals WHERE referral_id = ?", (rid,)).fetchone()
    assert (row["status"], row["approved_by"]) == ("proposed", None)
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == audits


def test_outcome_without_confirmation_raises_and_changes_nothing(conn: sqlite3.Connection) -> None:
    rid = _new_referral(conn)
    p04.approve(conn, rid, "CW-01")
    p04.record_provider_response(conn, rid, "accepted", "", "CW-01")
    audits = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    with pytest.raises(ConfirmationRequired):
        p04.confirm_outcome(conn, rid, True, "x", None)
    with pytest.raises(ConfirmationRequired):
        p04.confirm_outcome(conn, rid, True, "x", "OPR-01")
    row = conn.execute("SELECT status, family_received FROM referrals WHERE referral_id = ?", (rid,)).fetchone()
    assert (row["status"], row["family_received"]) == ("accepted", None)
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == audits


def test_list_filter_and_next_step(conn: sqlite3.Connection) -> None:
    everything = p04.list_referrals(conn)
    assert len(everything) == 30
    for status in p04.STATUSES:
        rows = p04.list_referrals(conn, status)
        assert rows and all(r["status"] == status and r["next_step"] for r in rows)
    assert len(p04.list_referrals(conn, "support_received")) == 9
    rid = _new_referral(conn)
    assert p04.list_referrals(conn)[0]["referral_id"] == rid
    assert "Approve" in p04.list_referrals(conn)[0]["next_step"]


def test_walkthrough(conn: sqlite3.Connection) -> None:
    # 1. HH-00009 (Harbor Flats), need: fever and medicine.
    result = p04.find_matches(conn, HH, FEVER)
    top = result["matches"][0]
    assert (top.provider_name, top.service_name) == ("Harbor Flats Health Point", "General clinic")
    assert "Keywords found: fever" in top.explanation
    # 2. Create, approve (confirmed), provider accepts, family received support.
    rid = p04.create_referral(conn, HH, FEVER, top, "CW-01")
    assert rid == "REF-0031"
    p04.approve(conn, rid, confirmed_by="CW-01")
    p04.record_provider_response(conn, rid, "accepted", "Appointment offered.", "CW-01")
    p04.confirm_outcome(conn, rid, True, "Family confirmed by the caseworker.", "CW-01")
    # 3. It shows support_received in the list.
    shown = {r["referral_id"]: r["status"] for r in p04.list_referrals(conn)}
    assert shown[rid] == "support_received"


def test_app_actions_through_ui(app_test, db_path) -> None:
    """The Referrals tab shows the seeded referrals and the filter works."""
    at = app_test(4).run()
    assert not at.exception
    flt = next(s for s in at.selectbox if s.key == "p04_status")
    flt.select("declined").run()
    assert not at.exception
    with db.connect(db_path) as c:
        assert len(p04.list_referrals(c, "declined")) == 3


def test_app_full_flow_through_ui(app_test, db_path) -> None:
    """Create, approve, respond and confirm through the real widgets."""
    at = app_test(4).run()
    next(r for r in at.radio if r.key == "p04_mode").set_value("Household").run()
    next(s for s in at.selectbox if s.key == "p04_household").select(HH)
    next(t for t in at.text_area if t.key == "p04_need_free").set_value(FEVER)
    at.run()
    assert not at.exception
    next(b for b in at.button if b.key == "p04_create_PRV-02_General clinic").click().run()
    assert not at.exception
    with db.connect(db_path) as c:
        assert _status(c, "REF-0031") == "proposed"
    at.run()
    next(s for s in at.selectbox if s.key == "p04_open").select("REF-0031")
    at.run()
    # approve: tick the box and submit the confirmation form
    next(c for c in at.checkbox if c.key == "confirm_tick_approve_REF-0031").check()
    next(b for b in at.button if b.label == "Approve referral").click().run()
    assert not at.exception
    with db.connect(db_path) as c:
        assert _status(c, "REF-0031") == "approved"
    # provider response: refused without the tick, done with it
    next(b for b in at.button if b.label == "Record provider response").click().run()
    with db.connect(db_path) as c:
        assert _status(c, "REF-0031") == "approved"
    next(c for c in at.checkbox if c.key == "confirm_tick_response_REF-0031").check()
    next(b for b in at.button if b.label == "Record provider response").click().run()
    with db.connect(db_path) as c:
        assert _status(c, "REF-0031") == "accepted"
    # outcome
    next(c for c in at.checkbox if c.key == "confirm_tick_outcome_REF-0031").check()
    next(b for b in at.button if b.label == "Confirm outcome").click().run()
    assert not at.exception
    with db.connect(db_path) as c:
        assert _status(c, "REF-0031") == "support_received"
