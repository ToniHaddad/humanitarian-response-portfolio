"""Tests for Project 19: sitrep search assistant (local TF-IDF index, threshold, nothing found)."""

from __future__ import annotations

import p19_search as p19
import pytest

from shared import db


@pytest.fixture
def index(conn) -> p19.Index:  # noqa: ANN001
    return p19.Index(conn)


def test_question_with_no_relevant_report_returns_nothing_found(index: p19.Index) -> None:
    for q in ("quantum computing satellite launch", "helicopter evacuation", "what is the weather"):
        result = index.search(q)
        assert result.status == "nothing_found"
        assert result.hits == []
        assert "Nothing relevant found" in result.message


def test_topic_in_library_returns_passages_from_right_report_with_date(index: p19.Index, conn) -> None:  # noqa: ANN001
    result = index.search("heating fuel blankets cold wave")
    assert result.status == "found"
    top = result.hits[0]
    assert top.sitrep_id in ("SITREP-002", "SITREP-010")
    expected = conn.execute("SELECT report_date, title FROM sitreps WHERE sitrep_id = ?", (top.sitrep_id,)).fetchone()
    assert top.report_date == expected["report_date"]
    assert top.title == expected["title"]
    assert {h.sitrep_id for h in result.hits[:4]} <= {"SITREP-002", "SITREP-010"}


def test_cash_question_finds_cash_reports(index: p19.Index) -> None:
    result = index.search("cash assistance round")
    assert {h.sitrep_id for h in result.hits[:3]} <= {"SITREP-008", "SITREP-016"}


def test_results_ordered_by_score_and_above_threshold(index: p19.Index) -> None:
    result = index.search("drinking water contaminated", top_k=8)
    scores = [h.score for h in result.hits]
    assert scores == sorted(scores, reverse=True)
    assert all(s >= p19.DEFAULT_THRESHOLD for s in scores)


def test_threshold_controls_what_is_shown(index: p19.Index) -> None:
    assert index.search("drinking water contaminated", threshold=0.99).status == "nothing_found"
    assert len(index.search("drinking water", threshold=0.0, top_k=3).hits) == 3


def test_empty_and_common_word_questions(index: p19.Index) -> None:
    assert index.search("").status == "empty"
    assert index.search("   ").status == "empty"
    assert index.search(None).status == "empty"
    common = index.search("what is the and of")
    assert common.status == "common_words"
    assert common.hits == []


def test_library_size_and_get_report(conn) -> None:  # noqa: ANN001
    assert len(p19.list_reports(conn)) == 16
    report = p19.get_report(conn, "SITREP-001")
    assert report is not None and "Juniper Hill" in report["title"]
    assert p19.get_report(conn, "SITREP-999") is None


def test_search_does_not_write(conn) -> None:  # noqa: ANN001
    before = db.scalar("SELECT COUNT(*) FROM audit_log")
    p19.Index(conn).search("water")
    assert db.scalar("SELECT COUNT(*) FROM audit_log") == before


def test_app_starts(app_test) -> None:  # noqa: ANN001
    at = app_test(19).run()
    assert not at.exception


def test_app_found_and_nothing_found(app_test) -> None:  # noqa: ANN001
    at = app_test(19).run()
    at.text_input[0].set_value("heating fuel blankets cold wave").run()
    assert not at.exception
    assert any("passage(s) found" in s.value for s in at.success)
    at.text_input[0].set_value("quantum computing satellite").run()
    assert any("Nothing relevant found" in w.value for w in at.warning)
