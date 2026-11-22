"""Project 02: calls and complaints. Repeat calls raise the call count, never the complaint count."""

from __future__ import annotations

import json
import sqlite3

import p02_calls as p
import pytest

from shared import db
from shared.confirm import ConfirmationRequired

OPERATOR = "OPR-01"
AREA = "AR-01"


def counts(conn: sqlite3.Connection) -> tuple[int, int]:
    return (
        db.scalar("SELECT COUNT(*) FROM calls", conn=conn),
        db.scalar("SELECT COUNT(*) FROM complaints", conn=conn),
    )


def staff_of(conn: sqlite3.Connection, role: str) -> str:
    return db.scalar("SELECT staff_id FROM staff WHERE role = ? AND active = 1 ORDER BY staff_id LIMIT 1", [role], conn=conn)


def new_complaint_call(conn: sqlite3.Connection, topic: str = "waiting_time") -> dict:
    return p.log_call(
        conn,
        operator_id=OPERATOR,
        call_type="complaint",
        language="en",
        area_id=AREA,
        notes="Waited all morning at the site.",
        new_complaint_topic=topic,
        new_complaint_description="Long wait at the distribution point.",
    )


def test_app_starts(app_test) -> None:
    at = app_test(2).run()
    assert not at.exception
    assert any("Calls received" in m.label for m in at.metric)


def test_repeat_complaint_calls_raise_call_count_not_complaint_count(conn: sqlite3.Connection) -> None:
    """Part 4: repeat complaint calls: call count rises, complaint count does not."""
    calls0, complaints0 = counts(conn)
    first = new_complaint_call(conn)
    cid = first["complaint_id"]
    second = p.log_call(
        conn,
        operator_id=OPERATOR,
        call_type="complaint",
        language="en",
        area_id=AREA,
        notes="Calling again about the same complaint.",
        complaint_id=cid,
    )
    calls1, complaints1 = counts(conn)
    assert calls1 == calls0 + 2
    assert complaints1 == complaints0 + 1
    assert p.complaint_call_count(conn, cid) == 2
    assert second["complaint_id"] == cid
    flags = [r[0] for r in conn.execute("SELECT is_repeat FROM calls WHERE complaint_id = ? ORDER BY call_id", (cid,))]
    assert flags == [0, 1]


def test_period_figures_match_direct_sql(conn: sqlite3.Connection) -> None:
    for days, (start, end) in {1: ("2026-11-16", "2026-11-16"), 7: ("2026-11-10", "2026-11-16"), 30: ("2026-10-18", "2026-11-16")}.items():
        fig = p.period_figures(conn, days)
        assert (fig["start"], fig["end"]) == (start, end)
        assert fig["calls_received"] == db.scalar(
            "SELECT COUNT(*) FROM calls WHERE substr(received_at,1,10) BETWEEN ? AND ?", [start, end], conn=conn
        )
        assert fig["calls_received"] == sum(fig["calls_by_type"].values())
        assert fig["new_complaints"] == db.scalar(
            "SELECT COUNT(*) FROM complaints WHERE substr(opened_at,1,10) BETWEEN ? AND ?", [start, end], conn=conn
        )
        assert fig["open_complaints"] == db.scalar("SELECT COUNT(*) FROM complaints WHERE status != 'closed'", conn=conn)
    assert p.period_figures(conn, 30)["calls_received"] > 0


def test_figures_move_by_exactly_the_logged_rows(conn: sqlite3.Connection) -> None:
    before = p.period_figures(conn, 1)
    cid = new_complaint_call(conn)["complaint_id"]
    p.log_call(conn, operator_id=OPERATOR, call_type="complaint", language="en", area_id=AREA, notes="again", complaint_id=cid)
    after = p.period_figures(conn, 1)
    assert after["calls_received"] == before["calls_received"] + 2
    assert after["new_complaints"] == before["new_complaints"] + 1
    assert after["open_complaints"] == before["open_complaints"] + 1


def test_recurring_topics_calculation(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM calls WHERE complaint_id IS NOT NULL")
    conn.execute("DELETE FROM complaint_followups")
    conn.execute("DELETE FROM complaints")
    a = new_complaint_call(conn, "waiting_time")["complaint_id"]
    new_complaint_call(conn, "waiting_time")
    new_complaint_call(conn, "parcel_contents")
    p.log_call(conn, operator_id=OPERATOR, call_type="complaint", language="en", area_id=AREA, notes="again", complaint_id=a)
    topics = p.recurring_topics(conn, 7).set_index("topic")
    assert topics.loc["waiting_time", "complaints"] == 2
    assert topics.loc["waiting_time", "calls"] == 3
    assert topics.loc["waiting_time", "repeat_calls"] == 1
    assert topics.loc["waiting_time", "recurring"] == "yes"
    assert topics.loc["parcel_contents", "complaints"] == 1
    assert topics.loc["parcel_contents", "calls"] == 1
    assert topics.loc["parcel_contents", "recurring"] == "no"
    assert topics["complaints"].sum() == db.scalar("SELECT COUNT(*) FROM complaints", conn=conn)
    assert p.recurring_topics(conn, 1)["complaints"].sum() == 3


def test_information_call_adds_a_call_and_no_complaint(conn: sqlite3.Connection) -> None:
    calls0, complaints0 = counts(conn)
    reqs0 = db.scalar("SELECT COUNT(*) FROM service_requests", conn=conn)
    result = p.log_call(conn, operator_id=OPERATOR, call_type="information", language="ar", area_id=AREA, notes="ما هي ساعات العمل؟")
    assert counts(conn) == (calls0 + 1, complaints0)
    assert db.scalar("SELECT COUNT(*) FROM service_requests", conn=conn) == reqs0
    row = db.row_dict(conn, "calls", "call_id", result["call_id"])
    assert row["complaint_id"] is None and row["request_id"] is None and row["is_repeat"] == 0
    with pytest.raises(p.CallError):
        p.log_call(conn, operator_id=OPERATOR, call_type="information", language="en", area_id=AREA, notes="x", complaint_id="CMP-0001")


def test_service_request_call_creates_one_linked_request(conn: sqlite3.Connection) -> None:
    reqs0 = db.scalar("SELECT COUNT(*) FROM service_requests", conn=conn)
    calls0, complaints0 = counts(conn)
    result = p.log_call(
        conn, operator_id=OPERATOR, call_type="service_request", language="en", area_id=AREA, notes="Need a food parcel.", new_request=True
    )
    rid = result["request_id"]
    assert db.scalar("SELECT COUNT(*) FROM service_requests", conn=conn) == reqs0 + 1
    assert counts(conn) == (calls0 + 1, complaints0)
    req = db.row_dict(conn, "service_requests", "request_id", rid)
    assert req["status"] == "new" and req["category"] is None and req["created_by"] == OPERATOR
    events = conn.execute("SELECT event_type FROM request_events WHERE request_id = ?", (rid,)).fetchall()
    assert [e[0] for e in events] == ["created"]
    assert db.row_dict(conn, "calls", "call_id", result["call_id"])["request_id"] == rid
    # A second call about the existing request is a repeat and adds no request.
    again = p.log_call(conn, operator_id=OPERATOR, call_type="follow_up", language="en", area_id=AREA, notes="Any news?", request_id=rid)
    assert db.scalar("SELECT COUNT(*) FROM service_requests", conn=conn) == reqs0 + 1
    assert db.row_dict(conn, "calls", "call_id", again["call_id"])["is_repeat"] == 1


def test_follow_up_needs_exactly_one_link_and_is_repeat(conn: sqlite3.Connection) -> None:
    cid = new_complaint_call(conn)["complaint_id"]
    with pytest.raises(p.CallError):
        p.log_call(conn, operator_id=OPERATOR, call_type="follow_up", language="en", area_id=AREA, notes="x")
    result = p.log_call(conn, operator_id=OPERATOR, call_type="follow_up", language="en", area_id=AREA, notes="x", complaint_id=cid)
    assert result["is_repeat"] == 1
    assert p.complaint_call_count(conn, cid) == 2


def test_invalid_calls_write_nothing(conn: sqlite3.Connection) -> None:
    before = counts(conn)
    with pytest.raises(p.CallError):  # not an operator
        p.log_call(conn, operator_id=staff_of(conn, "caseworker"), call_type="information", language="en", area_id=AREA, notes="x")
    with pytest.raises(p.CallError):  # complaint call with no link
        p.log_call(conn, operator_id=OPERATOR, call_type="complaint", language="en", area_id=AREA, notes="x")
    with pytest.raises(p.CallError):  # unknown topic
        new_complaint_call(conn, "not_a_topic")
    assert counts(conn) == before


def test_cannot_link_a_call_to_a_closed_complaint(conn: sqlite3.Connection) -> None:
    cid = db.scalar("SELECT complaint_id FROM complaints WHERE status = 'closed' LIMIT 1", conn=conn)
    with pytest.raises(p.CallError):
        p.log_call(conn, operator_id=OPERATOR, call_type="complaint", language="en", area_id=AREA, notes="x", complaint_id=cid)


def test_owner_assignment_and_followup(conn: sqlite3.Connection) -> None:
    cid = new_complaint_call(conn)["complaint_id"]
    owner = staff_of(conn, "caseworker")
    p.assign_owner(conn, cid, owner, assigned_by=staff_of(conn, "coordinator"))
    row = db.row_dict(conn, "complaints", "complaint_id", cid)
    assert row["owner_id"] == owner and row["status"] == "in_progress"
    p.record_followup(conn, cid, owner, "contacted_family", "Spoke with the family.")
    actions = [r[0] for r in conn.execute("SELECT action FROM complaint_followups WHERE complaint_id = ? ORDER BY followup_id", (cid,))]
    assert actions == ["owner_assigned", "contacted_family"]
    events = list(p.timeline(conn, cid)["event"])
    assert any(e.startswith("call") for e in events) and "follow-up: contacted_family" in events
    with pytest.raises(p.CallError):  # an operator cannot own a complaint
        p.assign_owner(conn, cid, OPERATOR, assigned_by=owner)
    with pytest.raises(p.CallError):
        p.record_followup(conn, cid, owner, "closed", "not a follow-up action")
    audited = db.scalar("SELECT COUNT(*) FROM audit_log WHERE channel = 'app:02' AND entity_id = ?", [cid], conn=conn)
    assert audited >= 3  # opened, owner assigned, follow-up


def test_closing_without_confirmation_raises_and_leaves_complaint_open(conn: sqlite3.Connection) -> None:
    cid = new_complaint_call(conn)["complaint_id"]
    for who in (None, "", OPERATOR, "NOT-A-STAFF"):
        with pytest.raises(ConfirmationRequired):
            p.close_complaint(conn, cid, "Resolved.", confirmed_by=who)
    row = db.row_dict(conn, "complaints", "complaint_id", cid)
    assert row["status"] == "open" and row["closed_at"] is None and row["resolution"] is None


def test_closing_with_confirmation_sets_closed(conn: sqlite3.Connection) -> None:
    cid = new_complaint_call(conn)["complaint_id"]
    staff = staff_of(conn, "caseworker")
    with pytest.raises(p.CallError):
        p.close_complaint(conn, cid, "  ", confirmed_by=staff)
    p.close_complaint(conn, cid, "Family informed, site queue plan changed.", confirmed_by=staff)
    row = db.row_dict(conn, "complaints", "complaint_id", cid)
    assert row["status"] == "closed" and row["closed_at"] == "2026-11-16T10:00:00"
    assert row["resolution"].startswith("Family informed")
    last = conn.execute(
        "SELECT action, staff_id FROM complaint_followups WHERE complaint_id = ? ORDER BY followup_id DESC", (cid,)
    ).fetchone()
    assert (last[0], last[1]) == ("closed", staff)
    assert db.scalar("SELECT COUNT(*) FROM audit_log WHERE action = 'complaint_closed' AND entity_id = ?", [cid], conn=conn) == 1
    with pytest.raises(p.CallError):  # already closed
        p.close_complaint(conn, cid, "again", confirmed_by=staff)


def test_walkthrough_two_calls_one_complaint(db_path, conn: sqlite3.Connection) -> None:
    """Two calls about the same complaint produce two incoming calls and one complaint."""
    fig0 = p.period_figures(conn, 1)
    next_id = db.next_id(conn, "complaints", "complaint_id", "CMP-", 4)
    assert next_id == "CMP-0029"
    first = p.log_call(
        conn,
        operator_id="OPR-01",
        call_type="complaint",
        language="en",
        area_id="AR-01",
        notes="Waited more than four hours at the distribution point.",
        new_complaint_topic="waiting_time",
    )
    assert first["complaint_id"] == next_id
    second = p.log_call(
        conn,
        operator_id="OPR-01",
        call_type="complaint",
        language="en",
        area_id="AR-01",
        notes="Calling again: nobody has contacted us yet.",
        complaint_id=first["complaint_id"],
    )
    fig1 = p.period_figures(conn, 1)
    assert fig1["calls_received"] == fig0["calls_received"] + 2
    assert fig1["new_complaints"] == fig0["new_complaints"] + 1
    assert p.complaint_call_count(conn, next_id) == 2
    assert second["is_repeat"] == 1
    # the scenario ids from the seed exist and are usable
    scenario = json.loads(db.get_setting("demo.scenarios", conn=conn))["p02"]
    assert db.row_dict(conn, "complaints", "complaint_id", scenario["existing_open_complaint_id"])["status"] != "closed"
    assert p.complaint_call_count(conn, scenario["complaint_with_most_calls"]) >= 2


def test_walkthrough_through_the_app(app_test, conn: sqlite3.Connection) -> None:
    """The README walkthrough, clicked through the real page."""
    calls0, complaints0 = counts(conn)
    at = app_test(2).run()
    at.selectbox(key="p02_call_type").select("complaint").run()
    at.radio(key="p02_link_c").set_value("New complaint").run()
    at.selectbox(key="p02_area").select("AR-01")
    at.selectbox(key="p02_topic").select("waiting_time")
    at.text_area(key="p02_notes").input("Waited more than four hours.")
    at.button[0].click().run()
    assert not at.exception
    assert counts(conn) == (calls0 + 1, complaints0 + 1)
    at.selectbox(key="p02_call_type").select("complaint").run()
    at.radio(key="p02_link_c").set_value("Existing open complaint").run()
    at.selectbox(key="p02_pick_c").select("CMP-0029")
    at.text_area(key="p02_notes").input("Calling again about the same complaint.")
    at.button[0].click().run()
    assert not at.exception
    assert counts(conn) == (calls0 + 2, complaints0 + 1)
    assert p.complaint_call_count(conn, "CMP-0029") == 2
