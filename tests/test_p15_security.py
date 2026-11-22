"""Tests for Project 15: security situation log (advisory rule, decisions, board, incidents)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import p15_security as p15
import pytest

from shared import clock, confirm, db, security

NOW = datetime(2026, 11, 16, 10, 0, 0)


def inc(severity: int, hours_ago: float, status: str = "verified") -> dict:
    return {"severity": severity, "occurred_at": NOW - timedelta(hours=hours_ago), "verification_status": status}


def scenario() -> dict:
    return json.loads(db.get_setting("demo.scenarios"))["p15"]


# -- the rule ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("severity", "level"),
    [(2, 0), (3, 1), (5, 1), (6, 2), (11, 2), (12, 3)],
)
def test_thresholds_just_below_and_at(severity: int, level: int) -> None:
    # Weighted severity inside 48 h counts in full, so the score equals the severity.
    # Sums above 5 use several incidents.
    parts = [min(5, severity - 5 * k) for k in range(3) if severity - 5 * k > 0]
    incs = [inc(s, 1) for s in parts]
    assert security.advisory_score(incs, NOW) == severity
    assert security.suggest_level(incs, NOW) == level


def test_unverified_and_discounted_ignored() -> None:
    incs = [inc(5, 1, "unverified"), inc(5, 1, "discounted"), inc(2, 1)]
    assert security.advisory_score(incs, NOW) == 2
    assert security.suggest_level(incs, NOW) == 0


def test_48_hour_weight_and_seven_day_window() -> None:
    assert security.advisory_score([inc(4, 47)], NOW) == 4.0
    assert security.advisory_score([inc(4, 49)], NOW) == 2.0
    assert security.advisory_score([inc(4, 7 * 24 - 1)], NOW) == 2.0
    assert security.advisory_score([inc(4, 7 * 24 + 1)], NOW) == 0.0
    # 6 half-weighted severity points are not enough for level 2; 5+5 at half weight is 5.
    assert security.suggest_level([inc(5, 60), inc(5, 60)], NOW) == 1


# -- decisions ----------------------------------------------------------------
def test_confirm_stores_final_equal_suggested(conn: sqlite3.Connection) -> None:
    area = scenario()["restricted_area_id"]
    adv_id = p15.decide_advisory(conn, area, 2, "DO-01")
    row = conn.execute("SELECT * FROM advisories WHERE advisory_id = ?", (adv_id,)).fetchone()
    assert (row["suggested_level"], row["final_level"], row["is_override"], row["override_reason"]) == (2, 2, 0, None)
    basis = json.loads(row["rule_basis_json"])
    assert basis["rule_version"] == security.RULE_VERSION and basis["score"] == 7.0 and len(basis["incidents"]) == 2
    assert row["decided_by"] == "DO-01" and row["active"] == 1


def test_override_with_reason_becomes_current(conn: sqlite3.Connection) -> None:
    area = scenario()["restricted_area_id"]
    adv_id = p15.decide_advisory(conn, area, 3, "DO-01", "Road to the clinic is impassable after the clash.")
    row = conn.execute("SELECT * FROM advisories WHERE advisory_id = ?", (adv_id,)).fetchone()
    assert (row["suggested_level"], row["final_level"], row["is_override"]) == (2, 3, 1)
    assert "clinic" in row["override_reason"]
    current = security.current_advisories(conn)[area]
    assert current["advisory_id"] == adv_id and current["final_level"] == 3
    assert conn.execute("SELECT COUNT(*) FROM advisories WHERE area_id = ? AND active = 1", (area,)).fetchone()[0] == 1
    assert area in security.blocked_areas(conn)


def test_downgrade_override_unblocks_area(conn: sqlite3.Connection) -> None:
    area = scenario()["restricted_area_id"]
    assert area in security.blocked_areas(conn)
    p15.decide_advisory(conn, area, 0, "DO-01", "Both closures lifted by local committee.")
    assert area not in security.blocked_areas(conn)


@pytest.mark.parametrize("reason", [None, "", "   \n"])
def test_override_without_reason_refused_and_nothing_written(conn: sqlite3.Connection, reason: str | None) -> None:
    area = scenario()["restricted_area_id"]
    before = conn.execute("SELECT COUNT(*) FROM advisories").fetchone()[0]
    audits = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    with pytest.raises(p15.OverrideReasonRequired, match="reason"):
        p15.decide_advisory(conn, area, 0, "DO-01", reason)
    assert conn.execute("SELECT COUNT(*) FROM advisories").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == audits
    assert security.current_advisories(conn)[area]["final_level"] == 2


def test_non_duty_officer_refused(conn: sqlite3.Connection) -> None:
    area = scenario()["restricted_area_id"]
    before = conn.execute("SELECT COUNT(*) FROM advisories").fetchone()[0]
    for who in ("FW-01", "OPR-01", "", None):
        with pytest.raises(confirm.ConfirmationRequired):
            p15.decide_advisory(conn, area, 2, who)  # type: ignore[arg-type]
    assert conn.execute("SELECT COUNT(*) FROM advisories").fetchone()[0] == before


def test_decisions_are_audited(conn: sqlite3.Connection) -> None:
    area = scenario()["restricted_area_id"]
    a1 = p15.decide_advisory(conn, area, 2, "DO-01")
    a2 = p15.decide_advisory(conn, area, 1, "DO-01", "Partial reopening agreed with field team.")
    rows = {r["entity_id"]: r for r in conn.execute("SELECT * FROM audit_log WHERE channel = 'app:15' AND entity_table = 'advisories'")}
    assert rows[a1]["action"] == "confirm_advisory" and rows[a1]["actor_id"] == "DO-01"
    assert rows[a2]["action"] == "override_advisory" and "Partial" in rows[a2]["reason"]


# -- incidents -----------------------------------------------------------------
def test_log_incident_unverified_and_audited(conn: sqlite3.Connection) -> None:
    iid = p15.log_incident(
        conn,
        area_id="AR-05",
        incident_type="crime",
        severity=4,
        source="hotline",
        occurred_at=NOW - timedelta(hours=1),
        description="Theft of supplies from a parked vehicle.",
        reported_by="FW-01",
    )
    row = conn.execute("SELECT * FROM incidents WHERE incident_id = ?", (iid,)).fetchone()
    assert row["verification_status"] == "unverified" and row["verified_by"] is None and row["verified_at"] is None
    assert conn.execute("SELECT 1 FROM audit_log WHERE entity_id = ? AND channel = 'app:15'", (iid,)).fetchone()
    # An unverified report changes no suggestion.
    assert p15.suggestion_for_area(conn, "AR-05")["score"] == 0


@pytest.mark.parametrize("nobody", [None, "", "  ", "NOPE-99"])
def test_log_incident_refused_without_a_staff_confirmation(conn: sqlite3.Connection, nobody: str | None) -> None:
    before = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    with pytest.raises(confirm.ConfirmationRequired):
        p15.log_incident(
            conn,
            area_id="AR-05",
            incident_type="crime",
            severity=3,
            source="hotline",
            occurred_at=NOW - timedelta(hours=1),
            description="x",
            reported_by=nobody,  # type: ignore[arg-type]
        )
    assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == before


@pytest.mark.parametrize(
    "bad",
    [{"severity": 0}, {"severity": 6}, {"incident_type": "meteor"}, {"source": "rumour"}, {"description": "  "}, {"area_id": "AR-99"}],
)
def test_log_incident_validation(conn: sqlite3.Connection, bad: dict) -> None:
    args = dict(
        area_id="AR-05",
        incident_type="crime",
        severity=3,
        source="hotline",
        occurred_at=NOW - timedelta(hours=1),
        description="x",
        reported_by="FW-01",
    )
    args.update(bad)
    before = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    with pytest.raises(p15.IncidentError):
        p15.log_incident(conn, **args)  # type: ignore[arg-type]
    assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == before


def test_verify_and_discount(conn: sqlite3.Connection) -> None:
    area = scenario()["unverified_only_area_id"]
    ids = [r["incident_id"] for r in p15.area_incidents(conn, area) if r["verification_status"] == "unverified"]
    assert len(ids) >= 2
    p15.set_verification(conn, ids[0], "verified", "DO-01")
    row = conn.execute("SELECT * FROM incidents WHERE incident_id = ?", (ids[0],)).fetchone()
    assert (row["verification_status"], row["verified_by"], row["verified_at"]) == ("verified", "DO-01", clock.iso(NOW))
    p15.set_verification(conn, ids[1], "discounted", "DO-01", "Duplicate of another report.")
    assert conn.execute("SELECT verification_status FROM incidents WHERE incident_id = ?", (ids[1],)).fetchone()[0] == "discounted"
    actions = {r["action"] for r in conn.execute("SELECT action FROM audit_log WHERE channel = 'app:15'")}
    assert {"incident_verified", "incident_discounted"} <= actions
    with pytest.raises(confirm.ConfirmationRequired):
        p15.set_verification(conn, ids[0], "discounted", "FW-01")


# -- board ------------------------------------------------------------------------
def test_status_board_on_seed(conn: sqlite3.Connection) -> None:
    board = {r["area_id"]: r for r in p15.status_board(conn, NOW)}
    assert len(board) == 12
    sc = scenario()
    ar07 = board[sc["restricted_area_id"]]
    assert (ar07["suggested_level"], ar07["score"], ar07["verified_in_window"], ar07["unverified_in_window"]) == (2, 7.0, 2, 1)
    assert ar07["current_level"] == 2 and not ar07["differs"]
    ar04 = board[sc["override_area_id"]]
    assert ar04["suggested_level"] == 1 and ar04["current_level"] == 0 and ar04["is_override"] and ar04["override_reason"]
    assert ar04["differs"]
    ar02 = board[sc["unverified_only_area_id"]]
    assert (ar02["suggested_level"], ar02["score"], ar02["verified_in_window"], ar02["unverified_in_window"]) == (0, 0.0, 0, 2)


def test_verifying_raises_the_suggestion(conn: sqlite3.Connection) -> None:
    area = scenario()["unverified_only_area_id"]
    for r in p15.area_incidents(conn, area):
        if r["verification_status"] == "unverified":
            p15.set_verification(conn, r["incident_id"], "verified", "DO-01")
    sug = p15.suggestion_for_area(conn, area, NOW)
    assert sug["score"] == 9.0 and sug["level"] == 2 and len(sug["incidents"]) == 2


def test_app_starts(app_test) -> None:  # noqa: ANN001
    at = app_test(15).run()
    assert not at.exception


def test_app_walkthrough_verify_then_override(app_test, db_path) -> None:  # noqa: ANN001
    """README walkthrough: verify both AR-02 reports, refused override without a reason, override with one."""
    at = app_test(15).run()
    assert not at.exception
    for turn in range(2):
        option = next(o for o in at.selectbox(key="p15_pending").options if "AR-02" in o)
        at.selectbox(key="p15_pending").set_value(option)
        at.radio(key="p15_outcome").set_value("verified").run()
        if turn == 0:  # without the tick box nothing is saved
            next(b for b in at.button if b.label == "Save outcome").click().run()
            assert not at.exception and any("AR-02" in o for o in at.selectbox(key="p15_pending").options)
            assert not any("marked verified" in s.value for s in at.success)
        at.checkbox(key="confirm_tick_p15_verify").check()
        next(b for b in at.button if b.label == "Save outcome").click().run()
        assert not at.exception and any("marked verified" in s.value for s in at.success)
    at.selectbox(key="p15_decide_area").set_value("AR-02").run()
    at.radio(key="p15_mode").set_value("Override with another level").run()
    at.selectbox(key="p15_final_level").set_value(1).run()
    at.checkbox(key="confirm_tick_p15_advisory").check()
    next(b for b in at.button if b.label == "Record advisory decision").click().run()
    assert any("override needs a written reason" in e.value.lower() for e in at.error)
    at.text_area(key="p15_reason").set_value("Reports checked, but the road is open and calm; hold at Caution.").run()
    at.checkbox(key="confirm_tick_p15_advisory").check()
    next(b for b in at.button if b.label == "Record advisory decision").click().run()
    assert not at.exception and any("AR-02 is now 1 Caution" in s.value for s in at.success)
    c = db.connect(db_path)
    try:
        row = c.execute(
            "SELECT suggested_level, final_level, is_override FROM advisories WHERE area_id = 'AR-02' AND active = 1"
        ).fetchone()
        assert tuple(row) == (2, 1, 1)
    finally:
        c.close()


def test_app_log_incident_needs_the_tick_box(app_test, db_path) -> None:  # noqa: ANN001
    def count() -> int:
        c = db.connect(db_path)
        try:
            return c.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        finally:
            c.close()

    at = app_test(15).run()
    assert not at.exception
    before = count()
    at.text_area(key="p15_f_desc").set_value("Checkpoint closed without notice.").run()
    next(b for b in at.button if b.label == "Log incident (unverified)").click().run()
    assert not at.exception and count() == before
    at.checkbox(key="confirm_tick_p15_log").check()
    next(b for b in at.button if b.label == "Log incident (unverified)").click().run()
    assert not at.exception and count() == before + 1
