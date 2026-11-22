"""Project 07: field data pipeline (Part 4: repeated pipeline loads create no duplicates)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import p07_stats as stats
import pytest

from shared import pipeline

# file -> (source, rows read, loaded, quarantined by rule)
EXPECTED: dict[str, tuple[str, int, int, dict[str, int]]] = {
    "team_north_2026-11-20.csv": (
        "TEAM-NORTH",
        16,
        13,
        {"missing_household_id": 1, "unknown_household": 1, "bad_date": 1},
    ),
    "team_coast_2026-11-21.csv": (
        "TEAM-COAST",
        17,
        13,
        {"unknown_area": 1, "household_size_out_of_range": 2, "unknown_priority": 1},
    ),
    "team_central_2026-11-21.csv": (
        "TEAM-CENTRAL",
        15,
        11,
        {"unknown_need_category": 2, "bad_date": 1, "unknown_household": 1},
    ),
}


def sample(name: str) -> Path:
    return pipeline.SAMPLE_DIR / name


def assessments(conn: sqlite3.Connection) -> int:
    return stats.assessment_count(conn)


def clear_pipeline_tables(conn: sqlite3.Connection) -> None:
    """Remove the seeded history so rates are computed on the sample files alone."""
    conn.execute("DELETE FROM field_assessments")
    conn.execute("DELETE FROM pipeline_quarantine")
    conn.execute("DELETE FROM pipeline_runs")
    conn.commit()


def quarantined_rules(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    rows = conn.execute("SELECT rule, COUNT(*) FROM pipeline_quarantine WHERE run_id = ? GROUP BY rule", (run_id,)).fetchall()
    return {r[0]: r[1] for r in rows}


# -- sample files -------------------------------------------------------------
@pytest.mark.parametrize("name", list(EXPECTED))
def test_each_sample_file_gives_expected_counts(conn: sqlite3.Connection, name: str) -> None:
    source, read, loaded, rules = EXPECTED[name]
    before = assessments(conn)
    result = pipeline.ingest_file(conn, sample(name), trigger="manual")
    assert result.source_id == source
    assert result.status == "partial"
    assert (result.rows_read, result.rows_loaded, result.rows_duplicate) == (read, loaded, 0)
    assert result.rows_quarantined == sum(rules.values())
    assert quarantined_rules(conn, result.run_id) == rules
    assert assessments(conn) == before + loaded


def test_sample_files_cover_every_rule(conn: sqlite3.Connection) -> None:
    summary = pipeline.run_pipeline(conn, trigger="manual")
    seen = {r for run in summary.runs for r in quarantined_rules(conn, run.run_id)}
    assert seen == set(pipeline.RULES)


def test_loaded_rows_are_tidied(conn: sqlite3.Connection) -> None:
    pipeline.ingest_file(conn, sample("team_north_2026-11-20.csv"), trigger="manual")
    row = conn.execute(
        "SELECT household_id, area_id, assessment_date, need_category, priority FROM field_assessments "
        "WHERE household_id = 'HH-00038' AND assessment_date = '2026-11-17'"
    ).fetchone()
    assert tuple(row) == ("HH-00038", "AR-05", "2026-11-17", "water_hygiene", "high")


# -- no duplicates --------------------------------------------------------------
def test_running_twice_adds_no_rows(conn: sqlite3.Connection) -> None:
    first = pipeline.run_pipeline(conn, trigger="manual")
    count = assessments(conn)
    assert first.rows_loaded == sum(v[2] for v in EXPECTED.values())
    second = pipeline.run_pipeline(conn, trigger="manual")
    assert second.rows_loaded == 0
    assert second.rows_duplicate == first.rows_loaded
    assert assessments(conn) == count


def test_same_file_twice_adds_no_rows(conn: sqlite3.Connection) -> None:
    path = sample("team_coast_2026-11-21.csv")
    pipeline.ingest_file(conn, path, trigger="manual")
    count = assessments(conn)
    again = pipeline.ingest_file(conn, path, trigger="manual")
    assert (again.rows_loaded, again.rows_duplicate) == (0, 13)
    assert assessments(conn) == count
    keys = conn.execute("SELECT COUNT(*), COUNT(DISTINCT row_key) FROM field_assessments").fetchone()
    assert keys[0] == keys[1]


def test_scheduled_run_skips_processed_files(conn: sqlite3.Connection) -> None:
    pipeline.run_pipeline(conn, trigger="manual")
    runs = conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0]
    scheduled = pipeline.run_pipeline(conn, trigger="scheduled")
    assert scheduled.runs == []
    assert sorted(scheduled.skipped_files) == sorted(EXPECTED)
    assert conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0] == runs


def test_run_now_helper_adds_audit_and_runs(conn: sqlite3.Connection) -> None:
    summary = stats.run_now(conn, actor_id="OPR-01")
    assert len(summary.runs) == 3
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'pipeline_run' AND actor_id = 'OPR-01'").fetchone()[0] == 3


# -- broken file ----------------------------------------------------------------
def test_broken_file_fails_and_loads_nothing(conn: sqlite3.Connection) -> None:
    before = assessments(conn)
    result = pipeline.ingest_file(conn, stats.BROKEN_SAMPLE, trigger="manual")
    assert result.status == "failed"
    assert (result.rows_read, result.rows_loaded, result.rows_quarantined) == (0, 0, 0)
    assert "household_id" in (result.error_message or "")
    assert assessments(conn) == before
    summary = stats.run_now(conn, include_broken=True)
    assert summary.status == "failed" and summary.failed_runs == 1


def test_unknown_file_name_fails(conn: sqlite3.Connection) -> None:
    result = pipeline.ingest_bytes(conn, "random_export.csv", b"a,b\n1,2\n", trigger="manual")
    assert result.status == "failed" and result.source_id is None


# -- clean-up cases ---------------------------------------------------------------
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("HH ID", "household_id"),
        ("  Visit Date ", "assessment_date"),
        ("Family Size", "household_size"),
        ("NEED", "need_category"),
        ("Area Code", "area"),
        ("Priority", "priority"),
    ],
)
def test_header_clean_up(header: str, expected: str) -> None:
    assert pipeline.clean_header(header) == expected


def test_value_clean_up(conn: sqlite3.Connection) -> None:
    raw = {
        "household_id": " hh 42 ",
        "area": " Northfield ",
        "assessment_date": "05/11/2026",
        "household_size": "4",
        "need_category": "WASH",
        "priority": "High",
    }
    clean, rule = pipeline.validate_row(conn, raw)
    assert rule is None and clean is not None
    assert clean["household_id"] == "HH-00042"
    assert clean["area_id"] == "AR-05"
    assert clean["assessment_date"] == "2026-11-05"
    assert clean["need_category"] == "water_hygiene"
    assert clean["priority"] == "high"


def test_each_rule_is_reported(conn: sqlite3.Connection) -> None:
    good = {
        "household_id": "HH-00042",
        "area": "AR-05",
        "assessment_date": "2026-11-05",
        "household_size": "4",
        "need_category": "food",
        "priority": "low",
    }
    cases = {
        "missing_household_id": {"household_id": ""},
        "unknown_household": {"household_id": "HH-00999"},
        "unknown_area": {"area": "AR-77"},
        "bad_date": {"assessment_date": "30/02/2026"},
        "household_size_out_of_range": {"household_size": "21"},
        "unknown_need_category": {"need_category": "transport"},
        "unknown_priority": {"priority": "urgent"},
    }
    assert set(cases) == set(pipeline.RULES)
    for rule, change in cases.items():
        assert pipeline.validate_row(conn, {**good, **change}) == (None, rule)


# -- error rates ----------------------------------------------------------------------
def test_error_rate_calculation() -> None:
    assert stats.error_rate(3, 16) == 18.8
    assert stats.error_rate(0, 10) == 0.0
    assert stats.error_rate(5, 0) == 0.0
    assert stats.error_rate(10, 10) == 100.0


def test_error_rates_by_source_and_rule_on_known_data(conn: sqlite3.Connection) -> None:
    clear_pipeline_tables(conn)
    pipeline.run_pipeline(conn, trigger="manual")
    by_source = stats.error_rates_by_source(conn).set_index("source_id")
    assert by_source.loc["TEAM-NORTH", "error_rate_pct"] == 18.8  # 3 of 16
    assert by_source.loc["TEAM-COAST", "error_rate_pct"] == 23.5  # 4 of 17
    assert by_source.loc["TEAM-CENTRAL", "error_rate_pct"] == 26.7  # 4 of 15
    by_rule = stats.error_rates_by_rule(conn).set_index("rule")
    assert by_rule["rows_quarantined"].sum() == 11
    assert by_rule.loc["unknown_household", "rows_quarantined"] == 2
    assert by_rule.loc["unknown_household", "error_rate_pct"] == 4.2  # 2 of 48 rows read
    assert by_rule.loc["missing_household_id", "error_rate_pct"] == 2.1  # 1 of 48


def test_quarantine_filters(conn: sqlite3.Connection) -> None:
    clear_pipeline_tables(conn)
    summary = pipeline.run_pipeline(conn, trigger="manual")
    assert len(stats.quarantine_rows(conn)) == 11
    assert len(stats.quarantine_rows(conn, rule="bad_date")) == 2
    assert len(stats.quarantine_rows(conn, source_id="TEAM-COAST")) == 4
    run_id = next(r.run_id for r in summary.runs if r.source_id == "TEAM-NORTH")
    only = stats.quarantine_rows(conn, run_id=run_id)
    assert len(only) == 3 and set(only["source_id"]) == {"TEAM-NORTH"}


def test_run_history_newest_first(conn: sqlite3.Connection) -> None:
    pipeline.run_pipeline(conn, trigger="manual")
    history = stats.run_history(conn)
    assert list(history["started_at"]) == sorted(history["started_at"], reverse=True)
    assert history.iloc[0]["trigger"] == "manual"


def test_last_scheduled_run_from_seed(conn: sqlite3.Connection) -> None:
    last = stats.last_scheduled_run(conn)
    assert last is not None and last["from"] == "job_runs"


# -- app ---------------------------------------------------------------------------------
def test_app_starts(app_test) -> None:  # noqa: ANN001
    at = app_test(7).run()
    assert not at.exception


def test_app_run_now_loads_once(app_test, db_path: Path) -> None:  # noqa: ANN001
    import sqlite3 as sq

    def count() -> int:
        c = sq.connect(db_path)
        try:
            return c.execute("SELECT COUNT(*) FROM field_assessments").fetchone()[0]
        finally:
            c.close()

    before = count()
    at = app_test(7).run()
    next(b for b in at.button if b.label == "Run now").click().run()
    assert not at.exception
    after_first = count()
    assert after_first == before + 37
    next(b for b in at.button if b.label == "Run now").click().run()
    assert not at.exception
    assert count() == after_first
