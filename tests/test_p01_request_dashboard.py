"""Tests for Project 01: request dashboard (next-action rule, overdue rule, SLA due time,
views against direct SQL, intake with the shared classifier, lifecycle, transitions,
confirmation on close, and the README walkthrough)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any

import p01_requests as p01
import pytest

from shared import classifier, clock, confirm, db

NOW = datetime(2026, 11, 16, 10, 0, 0)
FOOD_NOTE = "Family of 6 has no food left, asking for a food parcel and rice."
VAGUE_NOTE = "need help please"


@pytest.fixture
def model(conn: sqlite3.Connection) -> Any:
    return classifier.load_or_train(conn)


def scenario_ids() -> list[str]:
    return json.loads(db.get_setting("demo.scenarios"))["p01"]["unassigned_request_ids"]


def events(conn: sqlite3.Connection, rid: str) -> list[str]:
    return [r[0] for r in conn.execute("SELECT event_type FROM request_events WHERE request_id = ? ORDER BY at, event_id", (rid,))]


def audit_count(conn: sqlite3.Connection, rid: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM audit_log WHERE entity_id = ? AND channel = 'app:01'", (rid,)).fetchone()[0]


def base_row(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "request_id": "REQ-T",
        "status": "new",
        "category": "food",
        "assigned_to": None,
        "callback_due_at": None,
        "callback_done_at": None,
    }
    row.update(kw)
    return row


# -- app starts ----------------------------------------------------------------
def test_app_starts(app_test) -> None:  # noqa: ANN001
    at = app_test(1).run()
    assert not at.exception
    labels = {m.label: m.value for m in at.metric}
    assert "Unassigned (count)" in labels and "Overdue callbacks (count)" in labels


# -- next-action rule ----------------------------------------------------------
@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (base_row(status="closed", closed_at="2026-11-15T10:00:00"), "None (closed)"),
        (base_row(category=None), "Confirm category"),
        (base_row(category=None, assigned_to="CW-01", status="assigned"), "Confirm category"),
        (base_row(), "Assign to a caseworker"),
        (base_row(status="assigned", assigned_to="CW-01"), "Schedule callback"),
        (base_row(status="callback_scheduled", assigned_to="CW-01", callback_due_at="2026-11-16T07:30:00"), "Call back now: 2.5 h overdue"),
        (
            base_row(status="callback_scheduled", assigned_to="CW-01", callback_due_at="2026-11-17T14:00:00"),
            "Call back by Tue 17 Nov 14:00",
        ),
        (
            base_row(
                status="in_progress", assigned_to="CW-01", callback_due_at="2026-11-16T07:30:00", callback_done_at="2026-11-16T07:00:00"
            ),
            "Close or record outcome",
        ),
    ],
)
def test_next_action_rule(row: dict[str, Any], expected: str) -> None:
    assert p01.next_action(row, NOW) == expected


# -- overdue rule --------------------------------------------------------------
def test_overdue_exactly_at_just_before_and_after_due() -> None:
    row = base_row(status="callback_scheduled", assigned_to="CW-01", callback_due_at=clock.iso(NOW))
    assert not p01.is_callback_overdue(row, NOW)  # exactly at the due time: not yet overdue
    assert not p01.is_callback_overdue(row, NOW - timedelta(seconds=1))
    assert p01.is_callback_overdue(row, NOW + timedelta(seconds=1))
    assert p01.hours_overdue(row, NOW) == 0.0
    assert p01.hours_overdue(row, NOW + timedelta(hours=3)) == 3.0
    assert p01.next_action(row, NOW).startswith("Call back by")
    assert p01.next_action(row, NOW + timedelta(hours=3)) == "Call back now: 3 h overdue"


def test_done_or_closed_callbacks_never_overdue() -> None:
    late = NOW + timedelta(days=5)
    done = base_row(status="in_progress", assigned_to="CW-01", callback_due_at=clock.iso(NOW), callback_done_at=clock.iso(NOW))
    closed = base_row(status="closed", assigned_to="CW-01", callback_due_at=clock.iso(NOW))
    assert not p01.is_callback_overdue(done, late)
    assert not p01.is_callback_overdue(closed, late)


# -- default due time ----------------------------------------------------------
def test_default_due_is_assigned_plus_sla_hours(conn: sqlite3.Connection) -> None:
    assert p01.sla_hours(conn) == 48.0
    assert p01.default_callback_due("2026-11-16T09:15:00", 48) == datetime(2026, 11, 18, 9, 15)
    assert p01.default_callback_due(NOW, 24) == NOW + timedelta(hours=24)


def test_schedule_uses_sla_setting(conn: sqlite3.Connection, model: Any) -> None:
    db.set_setting(conn, "callback_sla_hours", "24", "CRD-01")
    rid = p01.create_request(conn, model, area_id="AR-01", notes=FOOD_NOTE, language="en", category="food", created_by="OPR-01")
    p01.assign(conn, rid, "CW-01", "CRD-01")
    due = p01.schedule_callback(conn, rid, "CW-01")
    assert due == NOW + timedelta(hours=24)
    assert p01.get_request(conn, rid)["callback_due_at"] == "2026-11-17T10:00:00"


def test_schedule_earlier_allowed_later_refused(conn: sqlite3.Connection, model: Any) -> None:
    rid = p01.create_request(conn, model, area_id="AR-01", notes=FOOD_NOTE, language="en", category="food", created_by="OPR-01")
    p01.assign(conn, rid, "CW-01", "CRD-01")
    with pytest.raises(p01.RequestError, match="due by"):
        p01.schedule_callback(conn, rid, "CW-01", NOW + timedelta(hours=48, minutes=1))
    with pytest.raises(p01.RequestError, match="before the assignment"):
        p01.schedule_callback(conn, rid, "CW-01", NOW - timedelta(hours=1))
    assert p01.get_request(conn, rid)["status"] == "assigned"
    due = p01.schedule_callback(conn, rid, "CW-01", NOW + timedelta(hours=6))
    assert due == NOW + timedelta(hours=6)


# -- views against direct SQL --------------------------------------------------
def test_unassigned_and_overdue_match_sql(conn: sqlite3.Connection) -> None:
    now_iso = clock.iso(NOW)
    sql_unassigned = {
        r[0] for r in conn.execute("SELECT request_id FROM service_requests WHERE status != 'closed' AND assigned_to IS NULL")
    }
    sql_overdue = {
        r[0]
        for r in conn.execute(
            "SELECT request_id FROM service_requests WHERE status != 'closed' AND callback_done_at IS NULL "
            "AND callback_due_at IS NOT NULL AND callback_due_at < ?",
            (now_iso,),
        )
    }
    unassigned = p01.unassigned_requests(conn, NOW)
    overdue = p01.overdue_callbacks(conn, NOW)
    assert set(unassigned["request_id"]) == sql_unassigned and len(sql_unassigned) > 0
    assert set(overdue["request_id"]) == sql_overdue and len(sql_overdue) > 0
    assert set(scenario_ids()) <= sql_unassigned
    assert (overdue["hours_overdue"] > 0).all()
    assert overdue["next_action"].str.startswith("Call back now").all()
    # Python overdue rule agrees with the SQL on every open request.
    open_df = p01.open_requests(conn, NOW)
    assert {r["request_id"] for r in open_df.to_dict("records") if p01.is_callback_overdue(r, NOW)} == sql_overdue


def test_metrics_match_sql(conn: sqlite3.Connection) -> None:
    m = p01.metrics(conn, NOW)
    assert m["open"] == conn.execute("SELECT COUNT(*) FROM service_requests WHERE status != 'closed'").fetchone()[0]
    assert m["unassigned"] == len(p01.unassigned_requests(conn, NOW))
    assert m["overdue"] == len(p01.overdue_callbacks(conn, NOW))
    week = clock.iso(NOW - timedelta(days=7))
    assert m["closed_7d"] == conn.execute("SELECT COUNT(*) FROM service_requests WHERE closed_at >= ?", (week,)).fetchone()[0]


def test_open_requests_next_action_and_calls_count(conn: sqlite3.Connection) -> None:
    df = p01.open_requests(conn, NOW)
    assert "closed" not in set(df["status"])
    assert (df.loc[df["category"].isna(), "next_action"] == "Confirm category").all()
    for r in df.head(10).to_dict("records"):
        n = conn.execute("SELECT COUNT(*) FROM calls WHERE request_id = ?", (r["request_id"],)).fetchone()[0]
        assert r["calls"] == n == p01.calls_count(conn, r["request_id"])


def test_request_from_project_02_without_category(conn: sqlite3.Connection, model: Any) -> None:
    conn.execute(
        "INSERT INTO service_requests(request_id, created_at, area_id, notes, language, status) VALUES (?,?,?,?,?,?)",
        ("REQ-09999", clock.iso(), "AR-02", "Caller asks for help paying the rent this month.", "en", "new"),
    )
    conn.execute(
        "INSERT INTO calls(call_id, received_at, operator_id, area_id, call_type, language, notes, request_id) VALUES (?,?,?,?,?,?,?,?)",
        ("CALL-999999", clock.iso(), "OPR-01", "AR-02", "service_request", "en", "Rent help", "REQ-09999"),
    )
    df = p01.unassigned_requests(conn, NOW)
    row = df[df["request_id"] == "REQ-09999"].iloc[0]
    assert row["next_action"] == "Confirm category" and row["calls"] == 1
    with pytest.raises(p01.RequestError, match="Confirm the category"):
        p01.assign(conn, "REQ-09999", "CW-01", "CRD-01")
    p01.confirm_category(conn, model, "REQ-09999", "shelter", "OPR-01")
    assert p01.get_request(conn, "REQ-09999")["suggested_confidence"] is not None


# -- intake and the classifier -------------------------------------------------
def test_intake_stores_confident_suggestion(conn: sqlite3.Connection, model: Any) -> None:
    rid = p01.create_request(
        conn,
        model,
        area_id="AR-03",
        notes=FOOD_NOTE,
        language="en",
        category="food",
        created_by="OPR-02",
        priority="high",
        household_id="HH-00042",
    )
    row = p01.get_request(conn, rid)
    assert rid == "REQ-00131"
    assert row["status"] == "new" and row["category"] == "food" and row["suggested_category"] == "food"
    assert row["suggested_confidence"] >= classifier.threshold_from_settings(conn)
    assert row["priority"] == "high" and row["household_id"] == "HH-00042" and row["created_by"] == "OPR-02"
    assert events(conn, rid) == ["created", "category_confirmed"]
    assert audit_count(conn, rid) == 1


def test_intake_low_confidence_left_blank_and_operator_must_choose(conn: sqlite3.Connection, model: Any) -> None:
    threshold = classifier.threshold_from_settings(conn)
    pred = p01.suggest(conn, model, VAGUE_NOTE)
    assert pred.category is None and pred.confidence < threshold  # Part 4: low confidence is left blank
    before = conn.execute("SELECT COUNT(*) FROM service_requests").fetchone()[0]
    with pytest.raises(p01.RequestError, match="Choose the category"):
        p01.create_request(conn, model, area_id="AR-01", notes=VAGUE_NOTE, language="en", category=None, created_by="OPR-01")
    assert conn.execute("SELECT COUNT(*) FROM service_requests").fetchone()[0] == before
    rid = p01.create_request(conn, model, area_id="AR-01", notes=VAGUE_NOTE, language="en", category="shelter", created_by="OPR-01")
    row = p01.get_request(conn, rid)
    assert row["suggested_category"] is None and row["suggested_confidence"] < threshold
    assert row["category"] == "shelter"
    detail = conn.execute(
        "SELECT detail FROM request_events WHERE request_id = ? AND event_type = 'category_confirmed'", (rid,)
    ).fetchone()[0]
    assert "below threshold" in detail


def test_suggestion_uses_shared_classifier(conn: sqlite3.Connection, model: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, float]] = []
    real = classifier.predict

    def spy(m: Any, text: str, threshold: float = classifier.DEFAULT_THRESHOLD) -> classifier.Prediction:
        seen.append((text, threshold))
        return real(m, text, threshold)

    monkeypatch.setattr(classifier, "predict", spy)
    rid = p01.create_request(conn, model, area_id="AR-01", notes=FOOD_NOTE, language="en", category="cash", created_by="OPR-01")
    assert seen == [(FOOD_NOTE, 0.6)]
    expected = real(model, FOOD_NOTE, 0.6)
    row = p01.get_request(conn, rid)
    assert (row["suggested_category"], row["suggested_confidence"]) == (expected.category, expected.confidence)
    assert row["category"] == "cash"  # the operator changed the suggestion; the confirmed category wins


def test_threshold_setting_changes_blank_decision(conn: sqlite3.Connection, model: Any) -> None:
    conf = p01.suggest(conn, model, FOOD_NOTE).confidence
    db.set_setting(conn, "classifier_confidence_threshold", str(conf + 0.001), "CRD-01")
    assert p01.suggest(conn, model, FOOD_NOTE).category is None


def test_confirm_category_on_seeded_request(conn: sqlite3.Connection, model: Any) -> None:
    rid = scenario_ids()[0]
    row = p01.get_request(conn, rid)
    assert row["category"] is None and row["suggested_confidence"] is None
    pred = p01.confirm_category(conn, model, rid, "health", "OPR-01")
    after = p01.get_request(conn, rid)
    assert after["category"] == "health"
    assert (after["suggested_category"], after["suggested_confidence"]) == (pred.category, pred.confidence)
    assert events(conn, rid)[-1] == "category_confirmed" and audit_count(conn, rid) == 1
    with pytest.raises(p01.RequestError, match="already has"):
        p01.confirm_category(conn, model, rid, "food", "OPR-01")
    with pytest.raises(p01.RequestError, match="Unknown category"):
        p01.confirm_category(conn, model, scenario_ids()[1], "pets", "OPR-01")


# -- lifecycle and transitions -------------------------------------------------
def test_full_lifecycle_with_events_and_audit(conn: sqlite3.Connection, model: Any) -> None:
    rid = p01.create_request(conn, model, area_id="AR-04", notes=FOOD_NOTE, language="en", category="food", created_by="OPR-01")
    p01.assign(conn, rid, "CW-02", "CRD-01")
    row = p01.get_request(conn, rid)
    assert (row["status"], row["assigned_to"], row["assigned_at"]) == ("assigned", "CW-02", clock.iso(NOW))
    p01.schedule_callback(conn, rid, "CW-02")
    assert p01.get_request(conn, rid)["status"] == "callback_scheduled"
    clock.set_now(NOW + timedelta(hours=5))
    p01.record_callback_done(conn, rid, "CW-02", "Family reached")
    row = p01.get_request(conn, rid)
    assert (row["status"], row["callback_done_at"]) == ("in_progress", "2026-11-16T15:00:00")
    assert p01.next_action(row, clock.now()) == "Close or record outcome"
    p01.close(conn, rid, "assisted", confirmed_by="CW-02")
    row = p01.get_request(conn, rid)
    assert (row["status"], row["closure_outcome"], row["closed_at"]) == ("closed", "assisted", "2026-11-16T15:00:00")
    assert events(conn, rid) == ["created", "category_confirmed", "assigned", "callback_scheduled", "callback_done", "closed"]
    actions = [r[0] for r in conn.execute("SELECT action FROM audit_log WHERE entity_id = ? ORDER BY audit_id", (rid,))]
    assert actions == ["create", "assign", "schedule_callback", "callback_done", "close"]


def test_invalid_transitions_raise(conn: sqlite3.Connection, model: Any) -> None:
    rid = p01.create_request(conn, model, area_id="AR-01", notes=FOOD_NOTE, language="en", category="food", created_by="OPR-01")
    with pytest.raises(p01.RequestError, match="needs 'assigned'"):
        p01.schedule_callback(conn, rid, "CW-01")
    with pytest.raises(p01.RequestError, match="needs 'callback_scheduled'"):
        p01.record_callback_done(conn, rid, "CW-01")
    with pytest.raises(p01.RequestError, match="needs 'in_progress'"):
        p01.close(conn, rid, "assisted", confirmed_by="CW-01")
    with pytest.raises(p01.RequestError, match="needs one of: caseworker"):
        p01.assign(conn, rid, "CRD-01", "CRD-01")  # only caseworkers hold requests
    with pytest.raises(p01.RequestError, match="cannot do this"):
        p01.assign(conn, rid, "CW-01", "OPR-01")  # operators do not assign
    p01.assign(conn, rid, "CW-01", "CRD-01")
    with pytest.raises(p01.RequestError, match="needs 'new'"):
        p01.assign(conn, rid, "CW-02", "CRD-01")
    p01.schedule_callback(conn, rid, "CW-01")
    with pytest.raises(p01.RequestError, match="needs 'callback_scheduled'|needs 'assigned'"):
        p01.schedule_callback(conn, rid, "CW-01")
    with pytest.raises(p01.RequestError, match="closure outcome|needs 'in_progress'"):
        p01.close(conn, rid, "assisted", confirmed_by="CW-01")
    p01.record_callback_done(conn, rid, "CW-01")
    with pytest.raises(p01.RequestError, match="Closure outcome"):
        p01.close(conn, rid, "forgotten", confirmed_by="CW-01")
    p01.close(conn, rid, "referred", confirmed_by="CW-01")
    with pytest.raises(p01.RequestError, match="needs 'in_progress'"):
        p01.close(conn, rid, "assisted", confirmed_by="CW-01")
    assert events(conn, rid) == ["created", "category_confirmed", "assigned", "callback_scheduled", "callback_done", "closed"]


def test_close_without_confirmation_changes_nothing(conn: sqlite3.Connection, model: Any) -> None:
    rid = p01.create_request(conn, model, area_id="AR-01", notes=FOOD_NOTE, language="en", category="food", created_by="OPR-01")
    p01.assign(conn, rid, "CW-01", "CRD-01")
    p01.schedule_callback(conn, rid, "CW-01")
    p01.record_callback_done(conn, rid, "CW-01")
    before = (p01.get_request(conn, rid), events(conn, rid), audit_count(conn, rid))
    for who in (None, "", "OPR-01", "NOBODY-9"):
        with pytest.raises(confirm.ConfirmationRequired):
            p01.close(conn, rid, "assisted", confirmed_by=who)
    assert (p01.get_request(conn, rid), events(conn, rid), audit_count(conn, rid)) == before
    assert before[0]["status"] == "in_progress"


# -- walkthrough ---------------------------------------------------------------
def _button(at: Any, label: str) -> Any:
    return next(b for b in at.button if b.label == label)


def test_walkthrough_in_the_app(app_test, conn: sqlite3.Connection, model: Any) -> None:  # noqa: ANN001
    rid = scenario_ids()[0]  # the ID moves with the seed's anchor time, so it is read from demo.scenarios
    expected = p01.suggest(conn, model, p01.get_request(conn, rid)["notes"])
    unassigned_sql = conn.execute("SELECT COUNT(*) FROM service_requests WHERE status != 'closed' AND assigned_to IS NULL").fetchone()[0]

    # 1) open the app and read the counts
    at = app_test(1).run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert int(metrics["Unassigned (count)"]) == unassigned_sql
    overdue_before = int(metrics["Overdue callbacks (count)"])
    assert overdue_before == len(p01.overdue_callbacks(conn, NOW))

    # 2) the first scenario request is selected by default; confirm the category
    assert at.selectbox(key="p01_request").value == rid
    cat_box = at.selectbox(key=f"p01_cat_{rid}")
    assert cat_box.value == expected.category  # pre-filled with the suggestion, or blank below the threshold
    chosen = expected.category or "food"
    if cat_box.value is None:
        cat_box.select(chosen).run()
    _button(at, "Confirm category").click().run()
    assert not at.exception
    assert p01.get_request(conn, rid)["category"] == chosen

    # 3) assign to CW-01 and schedule the callback (due = now + 48 h)
    at.selectbox(key="p01_cw").select("CW-01").run()
    _button(at, "Assign").click().run()
    assert p01.get_request(conn, rid)["assigned_to"] == "CW-01"
    _button(at, "Schedule callback").click().run()
    assert p01.get_request(conn, rid)["callback_due_at"] == clock.iso(NOW + timedelta(hours=48))

    # 4) record the callback done
    _button(at, "Record callback done").click().run()
    assert p01.get_request(conn, rid)["status"] == "in_progress"

    # 5) close with outcome assisted: first without the tick (nothing changes), then confirmed
    at.selectbox(key="p01_outcome").select("assisted").run()
    _button(at, "Close request").click().run()
    assert p01.get_request(conn, rid)["status"] == "in_progress"
    at.checkbox(key="confirm_tick_p01_close").check()
    _button(at, "Close request").click().run()
    assert not at.exception
    row = p01.get_request(conn, rid)
    assert (row["status"], row["closure_outcome"]) == ("closed", "assisted")
    metrics = {m.label: m.value for m in at.metric}
    assert int(metrics["Unassigned (count)"]) == unassigned_sql - 1
    assert events(conn, rid) == ["created", "category_confirmed", "assigned", "callback_scheduled", "callback_done", "closed"]
    assert any("Timeline (6 events)" in s.value for s in at.subheader)

    # Intake: a clear food note gets the Food suggestion and is saved as new
    at.text_area(key="p01_new_notes").input(FOOD_NOTE).run()
    new_cat = next(s for s in at.selectbox if str(s.key).startswith("p01_new_cat_"))
    assert new_cat.value == "food"
    _button(at, "Save request").click().run()
    assert not at.exception
    new = conn.execute("SELECT * FROM service_requests ORDER BY request_id DESC LIMIT 1").fetchone()
    assert (new["request_id"], new["status"], new["category"], new["suggested_category"]) == ("REQ-00131", "new", "food", "food")
