"""Project 13: volunteer shift planner."""

from __future__ import annotations

import json
import sqlite3

import p13_shifts as sh
import pytest

from shared import db
from shared.confirm import ConfirmationRequired

COORD = "CRD-01"
DAY = "2027-03-01"  # a Monday, far from the seeded shifts
DAY2 = "2027-03-03"


def add_vol(
    conn: sqlite3.Connection, vid: str, trainings=("driving",), windows=(("08:00", "18:00"),), area="AR-01", max_week=3, date=DAY, active=1
):
    conn.execute("INSERT INTO volunteers VALUES (?,?,?,?,?)", (vid, area, "ar", max_week, active))
    for t in trainings:
        conn.execute("INSERT INTO volunteer_trainings VALUES (?,?,?)", (vid, t, "2026-01-01"))
    for s, e in windows:
        conn.execute("INSERT INTO volunteer_availability VALUES (?,?,?,?)", (vid, date, s, e))


def add_shift(
    conn: sqlite3.Connection, sid: str, date=DAY, start="09:00", end="13:00", site="SITE-01", role="driver", count=1, training="driving"
):
    conn.execute("INSERT INTO shifts VALUES (?,?,?,?,?,?)", (sid, site, date, start, end, "Test shift"))
    conn.execute("INSERT INTO shift_roles VALUES (?,?,?,?)", (sid, role, count, training))


def ids(conn: sqlite3.Connection, sid: str, role: str) -> list[str]:
    return [c["volunteer_id"] for c in sh.candidates(conn, sid, role)]


# -- eligibility (hand-built) ----------------------------------------------------
def test_eligible_when_trained_and_available(conn):
    add_vol(conn, "VOL-901")
    add_shift(conn, "SHF-9001")
    assert ids(conn, "SHF-9001", "driver") == ["VOL-901"]


def test_training_required(conn):
    add_vol(conn, "VOL-901", trainings=("packing",))
    add_shift(conn, "SHF-9001")
    assert ids(conn, "SHF-9001", "driver") == []


def test_availability_must_cover_whole_shift(conn):
    add_vol(conn, "VOL-901", windows=(("09:00", "12:00"),))  # ends before the shift
    add_vol(conn, "VOL-902", windows=(("10:00", "14:00"),))  # starts after
    add_vol(conn, "VOL-903", windows=(("09:00", "13:00"),))  # exact cover
    add_vol(conn, "VOL-904", date=DAY2)  # other day
    add_shift(conn, "SHF-9001")
    assert ids(conn, "SHF-9001", "driver") == ["VOL-903"]


def test_inactive_volunteer_excluded(conn):
    add_vol(conn, "VOL-901", active=0)
    add_shift(conn, "SHF-9001")
    assert ids(conn, "SHF-9001", "driver") == []


def test_overlap_excludes_and_adjacent_shift_allowed(conn):
    add_vol(conn, "VOL-901")
    add_shift(conn, "SHF-9001", start="09:00", end="13:00")
    add_shift(conn, "SHF-9002", start="12:00", end="16:00")
    add_shift(conn, "SHF-9003", start="13:00", end="17:00")
    conn.execute(
        "INSERT INTO shift_assignments(shift_id,role,volunteer_id,status,assigned_by,assigned_at) VALUES ('SHF-9001','driver','VOL-901','suggested','CRD-01','x')"
    )
    assert ids(conn, "SHF-9002", "driver") == []
    assert ids(conn, "SHF-9003", "driver") == ["VOL-901"]


def test_cancelled_assignment_does_not_block(conn):
    add_vol(conn, "VOL-901")
    add_shift(conn, "SHF-9001", start="09:00", end="13:00")
    add_shift(conn, "SHF-9002", start="12:00", end="16:00")
    conn.execute(
        "INSERT INTO shift_assignments(shift_id,role,volunteer_id,status,assigned_by,assigned_at) VALUES ('SHF-9001','driver','VOL-901','cancelled','CRD-01','x')"
    )
    assert ids(conn, "SHF-9002", "driver") == ["VOL-901"]


def test_weekly_maximum(conn):
    add_vol(conn, "VOL-901", max_week=1)
    add_shift(conn, "SHF-9001", date=DAY)
    add_shift(conn, "SHF-9002", date=DAY2)
    conn.execute("INSERT INTO volunteer_availability VALUES ('VOL-901', ?, '08:00','18:00')", (DAY2,))
    assert ids(conn, "SHF-9002", "driver") == ["VOL-901"]
    conn.execute(
        "INSERT INTO shift_assignments(shift_id,role,volunteer_id,status,assigned_by,assigned_at) VALUES ('SHF-9001','driver','VOL-901','confirmed','CRD-01','x')"
    )
    assert ids(conn, "SHF-9002", "driver") == []


def test_ranking_by_distance_then_load_then_id(conn):
    # SITE-01 is in some area; a volunteer living in the site's own area is closest.
    site_area = conn.execute("SELECT area_id FROM sites WHERE site_id='SITE-01'").fetchone()[0]
    far = conn.execute(
        "SELECT area_id FROM areas WHERE area_id != ? ORDER BY ABS(x_km-(SELECT x_km FROM areas WHERE area_id=?)) DESC LIMIT 1",
        (site_area, site_area),
    ).fetchone()[0]
    add_vol(conn, "VOL-905", area=far)
    add_vol(conn, "VOL-903", area=site_area)
    add_vol(conn, "VOL-902", area=site_area)
    add_shift(conn, "SHF-9001")
    assert ids(conn, "SHF-9001", "driver") == ["VOL-902", "VOL-903", "VOL-905"]
    top = sh.candidates(conn, "SHF-9001", "driver")[0]
    assert "driving trained" in top["why"] and "km from site" in top["why"] and "shifts that week" in top["why"]


def test_suggest_never_overlaps_or_exceeds_count(conn):
    add_vol(conn, "VOL-901")
    add_vol(conn, "VOL-902")
    add_shift(conn, "SHF-9001", start="09:00", end="13:00", count=2)
    add_shift(conn, "SHF-9002", start="12:00", end="16:00", count=1)
    props = sh.suggest(conn, ["SHF-9001", "SHF-9002"])
    assert len([p for p in props if p["shift_id"] == "SHF-9001"]) == 2
    assert len([p for p in props if p["shift_id"] == "SHF-9002"]) == 0  # both busy with the overlapping shift


# -- seed ---------------------------------------------------------------------------
def test_planted_overlap_detected(conn):
    scen = json.loads(db.get_setting("demo.scenarios"))["p13"]
    found = [o for o in sh.overlaps(conn) if o["volunteer_id"] == scen["overlap_volunteer_id"]]
    assert len(found) == 1
    assert found[0]["shift_date"] == scen["overlap_date"]
    assert {found[0]["shift_a"], found[0]["shift_b"]} == {"SHF-0003", "SHF-0004"}


def test_unfilled_counts_on_seed(conn):
    status = {(s["shift_id"], s["role"]): s for s in sh.role_status(conn)}
    assert status[("SHF-0001", "driver")]["unfilled"] == 0
    assert status[("SHF-0003", "driver")]["unfilled"] == 1
    assert status[("SHF-0006", "registration")]["unfilled"] == 2
    assert status[("SHF-0004", "packing")]["required"] == 3
    assert sh.unfilled_total(conn) == 13
    assert all(s["unfilled"] == max(0, s["required"] - s["filled"]) for s in status.values())


def test_seed_suggestions_valid_and_deterministic(conn):
    first = sh.suggest(conn)
    assert first == sh.suggest(conn)
    assert first
    status = {(s["shift_id"], s["role"]): s["unfilled"] for s in sh.role_status(conn)}
    counts: dict[tuple[str, str], int] = {}
    for p in first:
        counts[(p["shift_id"], p["role"])] = counts.get((p["shift_id"], p["role"]), 0) + 1
    assert all(n <= status[k] for k, n in counts.items())
    # Accept every suggestion for real: no new overlap may appear.
    before = len(sh.overlaps(conn))
    for p in first:
        sh.accept_suggestion(conn, p["shift_id"], p["role"], p["volunteer_id"], COORD)
    assert len(sh.overlaps(conn)) == before
    assert all(p["role"] in sh.ROLES for p in first)


def test_seed_candidates_are_eligible(conn):
    for slot in sh.role_status(conn):
        shift = conn.execute("SELECT * FROM shifts WHERE shift_id=?", (slot["shift_id"],)).fetchone()
        for c in sh.candidates(conn, slot["shift_id"], slot["role"]):
            assert sh.has_training(conn, c["volunteer_id"], slot["required_training"])
            assert sh.availability_window(conn, c["volunteer_id"], shift) is not None
            assert c["shifts_that_week"] < c["max_per_week"]


def test_invalid_existing_assignment_flagged(conn):
    add_vol(conn, "VOL-901", trainings=("packing",))
    add_shift(conn, "SHF-9001")
    conn.execute(
        "INSERT INTO shift_assignments(shift_id,role,volunteer_id,status,assigned_by,assigned_at) VALUES ('SHF-9001','driver','VOL-901','confirmed','CRD-01','x')"
    )
    bad = [r for r in sh.invalid_assignments(conn) if r["shift_id"] == "SHF-9001"]
    assert len(bad) == 1 and "no driving training" in bad[0]["problem"]


# -- confirmation and audit ------------------------------------------------------------
def test_cancel_and_confirm_need_confirmation(conn):
    with pytest.raises(ConfirmationRequired):
        sh.cancel_assignment(conn, 21, None)
    with pytest.raises(ConfirmationRequired):
        sh.cancel_assignment(conn, 21, "OPR-01")  # wrong role
    with pytest.raises(ConfirmationRequired):
        sh.confirm_assignment(conn, 8, "")
    assert conn.execute("SELECT status FROM shift_assignments WHERE assignment_id=21").fetchone()[0] == "confirmed"


def test_cancel_audited_and_clears_overlap(conn):
    n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE channel='app:13'").fetchone()[0]
    before_unfilled = {(s["shift_id"], s["role"]): s["unfilled"] for s in sh.role_status(conn)}
    sh.cancel_assignment(conn, 21, COORD)
    assert not [o for o in sh.overlaps(conn) if o["volunteer_id"] == "VOL-007"]
    after = {(s["shift_id"], s["role"]): s["unfilled"] for s in sh.role_status(conn)}
    assert after[("SHF-0004", "packing")] == before_unfilled[("SHF-0004", "packing")] + 1
    rows = conn.execute("SELECT * FROM audit_log WHERE channel='app:13'").fetchall()
    assert len(rows) == n + 1
    assert rows[-1]["action"] == "assignment_cancelled" and rows[-1]["actor_id"] == COORD
    assert json.loads(rows[-1]["before_json"])["status"] == "confirmed"


def test_confirm_suggested_assignment_audited(conn):
    aid = conn.execute("SELECT assignment_id FROM shift_assignments WHERE status='suggested' ORDER BY assignment_id LIMIT 1").fetchone()[0]
    sh.confirm_assignment(conn, aid, COORD)
    assert conn.execute("SELECT status FROM shift_assignments WHERE assignment_id=?", (aid,)).fetchone()[0] == "confirmed"
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE channel='app:13' AND action='assignment_confirmed'").fetchone()[0] == 1


def test_accept_needs_confirmation_and_is_audited(conn):
    p = sh.suggest(conn)[0]
    with pytest.raises(ConfirmationRequired):
        sh.accept_suggestion(conn, p["shift_id"], p["role"], p["volunteer_id"], None)
    before = sh.unfilled_total(conn)
    sh.accept_suggestion(conn, p["shift_id"], p["role"], p["volunteer_id"], COORD)
    assert sh.unfilled_total(conn) == before - 1
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE channel='app:13' AND action='assignment_added'").fetchone()[0] == 1


def test_accept_rejects_ineligible_and_full(conn):
    with pytest.raises(ValueError):
        sh.accept_suggestion(conn, "SHF-0001", "driver", "VOL-001", COORD)  # already fully assigned
    with pytest.raises(ValueError):  # VOL-007 overlaps on SHF-0004's day
        sh.accept_suggestion(conn, "SHF-0003", "driver", "VOL-007", COORD)


def test_no_interpreter_role(conn):
    for role in ("interpreter", "interpreting"):
        with pytest.raises(ValueError):
            sh.candidates(conn, "SHF-0001", role)
        with pytest.raises(ValueError):
            sh.accept_suggestion(conn, "SHF-0001", role, "VOL-001", COORD)
    assert "interpreter" not in sh.ROLES
    assert all(p["role"] != "interpreter" for p in sh.suggest(conn))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO shift_roles VALUES ('SHF-0001','interpreter',1,'languages')")


# -- app ---------------------------------------------------------------------------------
def test_app_runs(app_test):
    at = app_test(13).run()
    assert not at.exception


def test_app_walkthrough_cancel_then_accept(app_test, db_path):
    """README walkthrough: cancel VOL-007's packing assignment, then accept VOL-046 for it."""
    at = app_test(13).run()
    assert not at.exception

    def metrics() -> dict[str, str]:
        return {m.label: m.value for m in at.metric}

    start = metrics()
    assert start["Overlapping assignment pairs"] == "3"
    target = next(o for o in at.selectbox(key="p13_assign").options if "SHF-0004" in o and "packing" in o and "VOL-007" in o)
    at.selectbox(key="p13_assign").set_value(target)
    at.radio(key="p13_action").set_value("cancel").run()
    at.checkbox(key="confirm_tick_change").check()
    next(b for b in at.button if b.label == "Cancel assignment").click().run()
    assert not at.exception
    assert any("#21 cancelled" in s.value for s in at.success)
    after_cancel = metrics()
    assert after_cancel["Overlapping assignment pairs"] == "2"
    assert int(after_cancel["Unfilled slots (people)"]) == int(start["Unfilled slots (people)"]) + 1

    pick = next(o for o in at.selectbox(key="p13_pick").options if o.startswith("SHF-0004 · packing · VOL-046"))
    assert not any("VOL-007" in o and "SHF-0004" in o for o in at.selectbox(key="p13_pick").options)
    at.selectbox(key="p13_pick").set_value(pick).run()
    at.checkbox(key="confirm_tick_accept").check()
    next(b for b in at.button if b.label == "Accept suggestion").click().run()
    assert not at.exception
    assert any("Recorded VOL-046 for SHF-0004 packing" in s.value for s in at.success)
    assert metrics()["Unfilled slots (people)"] == start["Unfilled slots (people)"]
