"""Project 12: distribution reconciliation (parcels planned, dispatched, distributed, returned)."""

from __future__ import annotations

import json
import sqlite3

import p12_reconciliation as rec
import pytest

from shared import clock, db
from shared.confirm import ConfirmationRequired

SITE = "SITE-04"  # no seeded parcel rounds inside the hand-built period
START, END = "2030-03-10", "2030-03-16"
STD, SML = "ITM-FP-STD", "ITM-FP-SML"


@pytest.fixture
def scenario(conn: sqlite3.Connection) -> dict:
    return json.loads(db.get_setting("demo.scenarios", conn=conn))["p12"]


def _plan(conn: sqlite3.Connection, pid: str, day: str, qty: int, item: str = STD, site: str = SITE, status: str = "planned") -> None:
    conn.execute(
        "INSERT INTO distribution_plans(plan_id, site_id, warehouse_id, item_id, planned_date, planned_quantity, status) VALUES (?,?,?,?,?,?,?)",
        (pid, site, "WH-01", item, day, qty, status),
    )


def _dispatch(conn: sqlite3.Connection, did: str, at: str, qty: int, item: str = STD, site: str = SITE) -> None:
    conn.execute(
        "INSERT INTO dispatches(dispatch_id, warehouse_id, site_id, item_id, quantity, dispatched_at) VALUES (?,?,?,?,?,?)",
        (did, "WH-01", site, item, qty, at),
    )


def _dist(
    conn: sqlite3.Connection,
    did: str,
    day: str,
    qty: int,
    hh: str = "HH-00002",
    item: str | None = STD,
    site: str = SITE,
    dispatch: str | None = None,
    meals: int = 0,
) -> None:
    conn.execute(
        """INSERT INTO distributions(distribution_id, distribution_date, site_id, household_id, household_size, parcel_item_id,
                                     parcel_quantity, rte_meal_quantity, expected_coverage_days, dispatch_id, recorded_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (did, day, site, hh, 4, item, qty, meals, 20, dispatch, f"{day}T10:00:00"),
    )


def _return(conn: sqlite3.Connection, rid: str, at: str, qty: int, item: str = STD, site: str = SITE) -> None:
    conn.execute(
        "INSERT INTO distribution_returns(return_id, site_id, item_id, quantity, returned_at) VALUES (?,?,?,?,?)",
        (rid, site, item, qty, at),
    )


# -- totals -------------------------------------------------------------------
def test_seeded_scenario_100_96_3_gives_1_unaccounted(conn: sqlite3.Connection, scenario: dict) -> None:
    fig = rec.figures(conn, scenario["site_id"], scenario["item_id"], scenario["period_start"], scenario["period_end"])
    assert fig["planned"] == scenario["planned"] == 100
    assert fig["dispatched"] == scenario["dispatched"] == 100
    assert fig["distributed"] == scenario["distributed"] == 96
    assert fig["returned"] == scenario["returned"] == 3
    assert fig["unaccounted"] == scenario["unaccounted"] == 1
    assert fig["not_dispatched_against_plan"] == 0
    text = rec.explain(scenario["item_id"], fig)
    assert "100 - 96 - 3 = 1" in text


def test_zero_activity(conn: sqlite3.Connection) -> None:
    fig = rec.figures(conn, SITE, STD, START, END)
    assert fig == {"planned": 0, "dispatched": 0, "distributed": 0, "returned": 0, "unaccounted": 0, "not_dispatched_against_plan": 0}
    assert rec.discrepancies(conn, SITE, STD, START, END) == []


def test_negative_unaccounted(conn: sqlite3.Connection) -> None:
    _dispatch(conn, "DSP-T001", "2030-03-11T15:00:00", 10)
    _dist(conn, "DST-T001", "2030-03-12", 8, dispatch="DSP-T001")
    _dist(conn, "DST-T002", "2030-03-12", 3, hh="HH-00003", dispatch="DSP-T001")
    fig = rec.figures(conn, SITE, STD, START, END)
    assert fig["unaccounted"] == -1
    assert "negative" in rec.explain(STD, fig)
    keys = [d["key"] for d in rec.discrepancies(conn, SITE, STD, START, END)]
    assert keys == [f"unaccounted:{STD}"]


def test_period_boundaries_inclusive_and_outside_excluded(conn: sqlite3.Connection) -> None:
    _dispatch(conn, "DSP-T001", f"{START}T00:00:00", 5)
    _dispatch(conn, "DSP-T002", f"{END}T23:59:59", 7)
    _dispatch(conn, "DSP-T003", "2030-03-09T23:59:59", 100)
    _dispatch(conn, "DSP-T004", "2030-03-17T00:00:00", 100)
    _dist(conn, "DST-T001", START, 1, dispatch="DSP-T001")
    _dist(conn, "DST-T002", END, 2, hh="HH-00003", dispatch="DSP-T002")
    _dist(conn, "DST-T003", "2030-03-09", 50, hh="HH-00004", dispatch="DSP-T003")
    _return(conn, "RET-T001", f"{START}T08:00:00", 1)
    _return(conn, "RET-T002", f"{END}T23:00:00", 2)
    _return(conn, "RET-T003", "2030-03-17T00:00:01", 9)
    _plan(conn, "PLN-T001", START, 4)
    _plan(conn, "PLN-T002", END, 6)
    _plan(conn, "PLN-T003", "2030-03-17", 99)
    fig = rec.figures(conn, SITE, STD, START, END)
    assert (fig["planned"], fig["dispatched"], fig["distributed"], fig["returned"]) == (10, 12, 3, 3)
    assert fig["unaccounted"] == 6
    assert fig["not_dispatched_against_plan"] == -2


def test_cancelled_plans_excluded(conn: sqlite3.Connection) -> None:
    _plan(conn, "PLN-T001", "2030-03-12", 40, status="cancelled")
    _plan(conn, "PLN-T002", "2030-03-12", 25, status="completed")
    assert rec.figures(conn, SITE, STD, START, END)["planned"] == 25


def test_other_items_and_other_sites_not_counted(conn: sqlite3.Connection) -> None:
    _dispatch(conn, "DSP-T001", "2030-03-11T10:00:00", 10)
    _dispatch(conn, "DSP-T002", "2030-03-11T10:00:00", 20, item=SML)
    _dispatch(conn, "DSP-T003", "2030-03-11T10:00:00", 30, site="SITE-05")
    _dist(conn, "DST-T001", "2030-03-12", 4, item=SML)
    _dist(conn, "DST-T002", "2030-03-12", 5, hh="HH-00003", site="SITE-05")
    _return(conn, "RET-T001", "2030-03-12T10:00:00", 2, item=SML)
    _return(conn, "RET-T002", "2030-03-12T10:00:00", 3, site="SITE-05")
    _plan(conn, "PLN-T001", "2030-03-12", 8, item=SML)
    _plan(conn, "PLN-T002", "2030-03-12", 9, site="SITE-05")
    std = rec.figures(conn, SITE, STD, START, END)
    assert (std["planned"], std["dispatched"], std["distributed"], std["returned"]) == (0, 10, 0, 0)
    sml = rec.figures(conn, SITE, SML, START, END)
    assert (sml["planned"], sml["dispatched"], sml["distributed"], sml["returned"], sml["unaccounted"]) == (8, 20, 4, 2, 14)


def test_stock_ledger_is_not_used(conn: sqlite3.Connection) -> None:
    """A return is also a ledger receipt; the figures come from their own tables, so it is counted once."""
    _dispatch(conn, "DSP-T001", "2030-03-11T10:00:00", 10)
    _return(conn, "RET-T001", "2030-03-12T10:00:00", 4)
    conn.execute(
        "INSERT INTO stock_movements(warehouse_id, item_id, moved_at, movement_type, reason, quantity) VALUES ('WH-01', ?, '2030-03-12T10:00:00', 'received', 'distribution_return', 4)",
        (STD,),
    )
    conn.execute(
        "INSERT INTO stock_movements(warehouse_id, item_id, moved_at, movement_type, reason, quantity) VALUES ('WH-01', ?, '2030-03-11T10:00:00', 'dispatched', 'write_off', 50)",
        (STD,),
    )
    assert rec.figures(conn, SITE, STD, START, END)["unaccounted"] == 6


def test_meals_do_not_change_parcel_figures(conn: sqlite3.Connection) -> None:
    _dispatch(conn, "DSP-T001", "2030-03-11T10:00:00", 10)
    _dist(conn, "DST-T001", "2030-03-12", 6, dispatch="DSP-T001", meals=500)
    _dispatch(conn, "DSP-T002", "2030-03-11T10:00:00", 900, item="ITM-RTE")
    _dist(conn, "DST-T002", "2030-03-12", 0, hh="HH-00003", item=None, meals=300)
    before = rec.figures(conn, SITE, STD, START, END)
    assert before["distributed"] == 6 and before["dispatched"] == 10 and before["unaccounted"] == 4
    assert [i["item_id"] for i in rec.parcel_items(conn)] == [STD, SML]
    with pytest.raises(ValueError):
        rec.figures(conn, SITE, "ITM-RTE", START, END)
    # meal-only row without a parcel item is not an unmatched parcel record
    assert rec.unmatched_records(conn, SITE, STD, START, END) == []


def test_bad_period_rejected(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        rec.figures(conn, SITE, STD, END, START)


# -- flags --------------------------------------------------------------------
def test_seeded_duplicate_and_unmatched_flagged_and_nothing_else(conn: sqlite3.Connection, scenario: dict) -> None:
    args = (conn, scenario["site_id"], scenario["item_id"], scenario["period_start"], scenario["period_end"])
    dups = rec.duplicate_households(*args)
    assert [d["household_id"] for d in dups] == [scenario["duplicate_household_id"]]
    assert dups[0]["n"] == 2
    unmatched = rec.unmatched_records(*args)
    assert [u["household_id"] for u in unmatched] == [scenario["unmatched_household_id"]]
    assert unmatched[0]["dispatch_id"] is None
    keys = [d["key"] for d in rec.discrepancies(*args)]
    assert len(keys) == 3
    assert f"unaccounted:{scenario['item_id']}" in keys
    assert f"duplicate:{scenario['duplicate_household_id']}" in keys
    assert f"unmatched:{unmatched[0]['distribution_id']}" in keys


def test_unmatched_detects_wrong_site_or_item_dispatch(conn: sqlite3.Connection) -> None:
    _dispatch(conn, "DSP-T001", "2030-03-11T10:00:00", 10)
    _dispatch(conn, "DSP-T002", "2030-03-11T10:00:00", 10, site="SITE-05")
    _dispatch(conn, "DSP-T003", "2030-03-11T10:00:00", 10, item=SML)
    _dist(conn, "DST-T001", "2030-03-12", 1, hh="HH-00002", dispatch="DSP-T001")  # fine
    _dist(conn, "DST-T002", "2030-03-12", 1, hh="HH-00003", dispatch="DSP-T002")  # other site
    _dist(conn, "DST-T003", "2030-03-12", 1, hh="HH-00004", dispatch="DSP-T003")  # other item
    _dist(conn, "DST-T004", "2030-03-12", 1, hh="HH-00005", dispatch=None)  # none
    got = {u["distribution_id"] for u in rec.unmatched_records(conn, SITE, STD, START, END)}
    assert got == {"DST-T002", "DST-T003", "DST-T004"}
    assert rec.duplicate_households(conn, SITE, STD, START, END) == []


def test_duplicate_only_within_same_item(conn: sqlite3.Connection) -> None:
    _dist(conn, "DST-T001", "2030-03-12", 1, hh="HH-00002", item=STD)
    _dist(conn, "DST-T002", "2030-03-13", 1, hh="HH-00002", item=SML)
    assert rec.duplicate_households(conn, SITE, STD, START, END) == []
    _dist(conn, "DST-T003", "2030-03-14", 1, hh="HH-00002", item=STD)
    assert [d["household_id"] for d in rec.duplicate_households(conn, SITE, STD, START, END)] == ["HH-00002"]


# -- notes --------------------------------------------------------------------
def test_latest_note_decides_resolved_and_is_audited(conn: sqlite3.Connection, scenario: dict) -> None:
    args = (scenario["site_id"], scenario["item_id"], scenario["period_start"], scenario["period_end"])
    key = f"duplicate:{scenario['duplicate_household_id']}"

    def state() -> dict:
        return next(d for d in rec.discrepancies(conn, *args) if d["key"] == key)

    assert state()["resolved"] is False and state()["note"] is None
    n1 = rec.add_note(conn, *args[:1], args[2], args[3], key, "duplicate_household", "Checking the register", False, "FW-01")
    assert state()["resolved"] is False and state()["note"] == "Checking the register"
    rec.add_note(conn, args[0], args[2], args[3], key, "duplicate_household", "Second visit, confirmed double entry", True, "FW-01")
    assert state()["resolved"] is True and state()["note"].startswith("Second visit")
    rec.add_note(conn, args[0], args[2], args[3], key, "duplicate_household", "Reopened", False, "FW-01")
    assert state()["resolved"] is False
    audits = conn.execute("SELECT * FROM audit_log WHERE entity_table = 'reconciliation_notes' AND channel = 'app:12'").fetchall()
    assert len(audits) == 3
    assert audits[0]["entity_id"] == str(n1) and audits[0]["actor_id"] == "FW-01"
    row = conn.execute("SELECT * FROM reconciliation_notes WHERE note_id = ?", (n1,)).fetchone()
    assert row["at"] == clock.iso() and row["discrepancy_type"] == "duplicate_household"


def test_note_validation(conn: sqlite3.Connection, scenario: dict) -> None:
    s, a, b = scenario["site_id"], scenario["period_start"], scenario["period_end"]
    with pytest.raises(ValueError):
        rec.add_note(conn, s, a, b, "unaccounted:x", "unaccounted", "   ", True, "FW-01")
    with pytest.raises(ValueError):
        rec.add_note(conn, s, a, b, "unaccounted:x", "unaccounted", "text", True, "NOPE-99")
    with pytest.raises(ValueError):
        rec.add_note(conn, s, a, b, "k", "bogus", "text", True, "FW-01")


# -- approval and export ------------------------------------------------------
def test_approval_requires_confirmation(conn: sqlite3.Connection, scenario: dict) -> None:
    args = (conn, scenario["site_id"], scenario["item_id"], scenario["period_start"], scenario["period_end"])
    with pytest.raises(ConfirmationRequired):
        rec.approve_report(*args, confirmed_by=None)
    with pytest.raises(ConfirmationRequired):
        rec.approve_report(*args, confirmed_by="FW-01")  # a field worker cannot sign off
    assert conn.execute("SELECT COUNT(*) FROM reconciliation_reports").fetchone()[0] == 1  # only the seeded one


def test_approval_writes_report_with_unresolved_items(conn: sqlite3.Connection, scenario: dict) -> None:
    s, i, a, b = scenario["site_id"], scenario["item_id"], scenario["period_start"], scenario["period_end"]
    dup = f"duplicate:{scenario['duplicate_household_id']}"
    unaccounted = f"unaccounted:{i}"
    unmatched = rec.unmatched_records(conn, s, i, a, b)[0]
    unmatched_key = f"unmatched:{unmatched['distribution_id']}"
    rec.add_note(conn, s, a, b, dup, "duplicate_household", "Double entry, corrected", True, "FW-01")
    rec.add_note(conn, s, a, b, unmatched_key, "unmatched_dispatch", "Dispatch note found", True, "FW-01")
    rec.add_note(conn, s, a, b, unaccounted, "unaccounted", "Still searching", False, "FW-01")

    report_id = rec.approve_report(conn, s, i, a, b, confirmed_by="CRD-01")
    assert report_id == "RCN-0002"  # the seed already holds RCN-0001 for another site
    row = conn.execute("SELECT * FROM reconciliation_reports WHERE report_id = ?", (report_id,)).fetchone()
    assert row["approved_by"] == "CRD-01" and row["approved_at"] == clock.iso()
    summary = json.loads(row["summary_json"])
    assert summary["unaccounted"] == 1 and summary["unresolved_keys"] == [unaccounted]
    md = row["markdown"]
    for text in (
        s,
        a,
        b,
        i,
        "| Planned | 100 |",
        "| Dispatched from warehouse | 100 |",
        "| Distributed to households | 96 |",
        "| Returned | 3 |",
        "100 - 96 - 3 = 1",
    ):
        assert text in md
    assert "Double entry, corrected" in md and "Still searching" in md
    unresolved_section = md.split("## Unresolved items")[1]
    assert unaccounted in unresolved_section
    assert dup not in unresolved_section and unmatched_key not in unresolved_section
    audit = conn.execute("SELECT * FROM audit_log WHERE entity_table = 'reconciliation_reports' AND entity_id = ?", (report_id,)).fetchone()
    assert audit["channel"] == "app:12" and audit["actor_id"] == "CRD-01"
    assert [r["report_id"] for r in rec.earlier_reports(conn, s)] == [report_id]
    assert rec.approve_report(conn, s, i, a, b, confirmed_by="CRD-01") == "RCN-0003"


def test_report_with_no_discrepancies_says_none(conn: sqlite3.Connection) -> None:
    rid = rec.approve_report(conn, SITE, STD, START, END, confirmed_by="CRD-01")
    md = conn.execute("SELECT markdown FROM reconciliation_reports WHERE report_id = ?", (rid,)).fetchone()[0]
    assert md.split("## Unresolved items")[1].strip() == "None"


# -- app ----------------------------------------------------------------------
def test_app_starts_without_exception(app_test) -> None:  # noqa: ANN001
    at = app_test(12).run()
    assert not at.exception
    assert at.metric[4].value == "1"
