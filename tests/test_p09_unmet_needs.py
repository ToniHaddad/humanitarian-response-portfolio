"""Project 09: unmet needs map. Gap = counted requests - completed assistance, per area and aid type."""

from __future__ import annotations

import sqlite3
from datetime import date

import p09_unmet as p
import pytest

from shared import clock, db

# A stretch of January with no seeded requests, so hand-built rows are the only ones in range.
Q_START = date(2026, 1, 5)
Q_END = date(2026, 1, 9)
AREA = "AR-06"
OTHER_AREA = "AR-07"


def add_request(
    conn: sqlite3.Connection,
    rid: str,
    created: str = "2026-01-06T09:00:00",
    area: str = AREA,
    category: str | None = "food",
    outcome: str | None = None,
) -> None:
    household = db.scalar("SELECT household_id FROM households WHERE area_id = ? LIMIT 1", [area], conn=conn)
    conn.execute(
        "INSERT INTO service_requests (request_id, created_at, household_id, area_id, notes, language, category, status, closure_outcome, closed_at)"
        " VALUES (?, ?, ?, ?, 'test note', 'en', ?, ?, ?, ?)",
        (rid, created, household, area, category, "closed" if outcome else "new", outcome, created if outcome else None),
    )


def add_referral(conn: sqlite3.Connection, ref: str, rid: str, received: int | None) -> None:
    household = db.scalar("SELECT household_id FROM service_requests WHERE request_id = ?", [rid], conn=conn)
    provider = db.scalar("SELECT provider_id FROM service_providers LIMIT 1", conn=conn)
    staff = db.scalar("SELECT staff_id FROM staff WHERE role = 'caseworker' LIMIT 1", conn=conn)
    conn.execute(
        "INSERT INTO referrals (referral_id, created_at, household_id, request_id, need_text, service_category, provider_id,"
        " service_name, status, caseworker_id, family_received) VALUES (?, '2026-01-07T09:00:00', ?, ?, 'need', 'food', ?, 'svc', 'proposed', ?, ?)",
        (ref, household, rid, provider, staff, received),
    )


@pytest.fixture
def known(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Food in AR-06: 7 requests with known outcomes. Plus one shelter and one uncategorised."""
    add_request(conn, "T-01", outcome="assisted")  # completed
    add_request(conn, "T-02")  # referral received (two referrals) -> completed once
    add_referral(conn, "T-R1", "T-02", 1)
    add_referral(conn, "T-R2", "T-02", 1)
    add_request(conn, "T-03", outcome="duplicate")  # excluded
    add_request(conn, "T-04", outcome="withdrawn")  # excluded
    add_request(conn, "T-05", outcome="not_eligible")  # excluded
    add_request(conn, "T-06")  # open: outstanding
    add_request(conn, "T-07", outcome="unreachable")  # outstanding
    add_request(conn, "T-08")  # referral not received: outstanding
    add_referral(conn, "T-R3", "T-08", 0)
    add_request(conn, "T-09", category="shelter", outcome="assisted")
    add_request(conn, "T-10", category=None)  # not yet categorised
    conn.commit()
    return conn


def row(df, area: str = AREA):
    return df[df["area_id"] == area].iloc[0]


def test_gap_for_food_in_the_quiet_area(known: sqlite3.Connection) -> None:
    r = row(p.by_area(known, Q_START, Q_END, "food"))
    # counted food: T-01, T-02, T-06, T-07, T-08 = 5; completed: T-01, T-02 = 2; not counted: 3
    assert (r["requests"], r["completed"], r["outstanding"], r["excluded"]) == (5, 2, 3, 3)
    assert r["completion_rate_pct"] == 40.0


def test_referral_received_counts_once_even_with_two_referrals(known: sqlite3.Connection) -> None:
    rows = p.request_rows(known, Q_START, Q_END)
    assert int(rows[rows["request_id"] == "T-02"]["completed"].iloc[0]) == 1
    assert p.totals(known, Q_START, Q_END, "food", AREA)["completed"] == 2


def test_open_unreachable_and_not_received_stay_outstanding(known: sqlite3.Connection) -> None:
    rows = p.request_rows(known, Q_START, Q_END).set_index("request_id")
    for rid in ("T-06", "T-07", "T-08"):
        assert rows.loc[rid, "completed"] == 0 and rows.loc[rid, "excluded"] == 0


def test_not_a_need_outcomes_are_excluded_and_never_completed(known: sqlite3.Connection) -> None:
    rows = p.request_rows(known, Q_START, Q_END).set_index("request_id")
    for rid in ("T-03", "T-04", "T-05"):
        assert rows.loc[rid, "excluded"] == 1 and rows.loc[rid, "completed"] == 0
    # even an excluded request with a received referral is not completed assistance
    add_referral(known, "T-R9", "T-03", 1)
    rows = p.request_rows(known, Q_START, Q_END).set_index("request_id")
    assert rows.loc["T-03", "completed"] == 0
    assert p.totals(known, Q_START, Q_END, area_id=AREA)["excluded"] == 3


def test_null_category_has_its_own_aid_type(known: sqlite3.Connection) -> None:
    r = row(p.by_area(known, Q_START, Q_END, p.UNCATEGORISED))
    assert (r["requests"], r["completed"], r["outstanding"]) == (1, 0, 1)
    grid = p.by_area_and_type(known, Q_START, Q_END)
    cell = grid[(grid["area_id"] == AREA) & (grid["aid_type"] == p.UNCATEGORISED)].iloc[0]
    assert cell["outstanding"] == 1
    assert p.AID_LABELS[p.UNCATEGORISED] == "Not yet categorised"


def test_date_range_is_inclusive_at_both_ends(conn: sqlite3.Connection) -> None:
    add_request(conn, "B-01", created="2026-01-05T00:00:00")
    add_request(conn, "B-02", created="2026-01-09T23:59:59")
    add_request(conn, "B-03", created="2026-01-04T23:59:59")
    add_request(conn, "B-04", created="2026-01-10T00:00:00")
    ids = set(p.request_rows(conn, Q_START, Q_END)["request_id"])
    assert ids == {"B-01", "B-02"}
    assert p.totals(conn, Q_START, Q_START)["requests"] == 1
    assert p.totals(conn, Q_END, Q_END)["requests"] == 1


def test_filters_by_aid_type_and_area(known: sqlite3.Connection) -> None:
    add_request(known, "T-11", area=OTHER_AREA, category="food")
    assert p.totals(known, Q_START, Q_END, "food", AREA)["requests"] == 5
    assert p.totals(known, Q_START, Q_END, "food", OTHER_AREA)["requests"] == 1
    assert p.totals(known, Q_START, Q_END, "shelter")["requests"] == 1
    assert p.totals(known, Q_START, Q_END, area_id=AREA)["requests"] == 7
    only = p.by_area(known, Q_START, Q_END, area_id=AREA)
    assert list(only["area_id"]) == [AREA]


def test_all_twelve_areas_appear_with_zeros(conn: sqlite3.Connection) -> None:
    df = p.by_area(conn, Q_START, Q_END)  # no requests at all in this range
    assert len(df) == 12 and set(df["area_id"]) == {f"AR-{i:02d}" for i in range(1, 13)}
    assert df["requests"].sum() == 0 and df["outstanding"].sum() == 0
    assert df["completion_rate_pct"].isna().all()  # no requests, so no rate rather than a made-up 0 or 100
    assert len(p.by_area_and_type(conn, Q_START, Q_END)) == 12 * len(p.AID_TYPES)


def test_area_totals_sum_to_overall_totals(conn: sqlite3.Connection) -> None:
    start, end = p.default_range(clock.today())
    tot = p.totals(conn, start, end)
    by_area = p.by_area(conn, start, end)
    assert tot["requests"] > 0
    assert by_area["requests"].sum() == tot["requests"]
    assert by_area["completed"].sum() == tot["completed"]
    assert by_area["outstanding"].sum() == tot["outstanding"] == tot["requests"] - tot["completed"]
    assert by_area["excluded"].sum() == tot["excluded"]
    grid = p.by_area_and_type(conn, start, end)
    assert grid["requests"].sum() == tot["requests"] and grid["outstanding"].sum() == tot["outstanding"]
    assert p.weekly(conn, start, end)["requests"].sum() == tot["requests"]
    # splitting by aid type also adds up
    assert sum(p.totals(conn, start, end, t)["requests"] for t in p.AID_TYPES) == tot["requests"]


def test_per_1000_people(known: sqlite3.Connection) -> None:
    r = row(p.by_area(known, Q_START, Q_END, "food"))
    expected = round(1000 * 3 / r["population_estimate"], 2)
    assert r["outstanding_per_1000"] == expected > 0


def test_default_range_is_last_30_days_to_today(db_path) -> None:  # noqa: ANN001
    assert p.default_range(clock.today()) == (date(2026, 10, 17), date(2026, 11, 16))


def test_app_runs_without_exception(app_test) -> None:  # noqa: ANN001
    at = app_test(9).run()
    assert not at.exception


def test_app_with_filters_and_empty_selection(app_test) -> None:  # noqa: ANN001
    at = app_test(9).run()
    at.selectbox(key="p09_aid").select("food").run()
    assert not at.exception
    at.selectbox(key="p09_area").select("AR-04").run()
    assert not at.exception
    at.date_input(key="p09_dates").set_value((date(2026, 1, 5), date(2026, 1, 9))).run()
    assert not at.exception
    assert any("No requests" in i.value for i in at.info)
