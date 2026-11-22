"""Tests for Project 03: classifier lab (evaluation numbers, threshold behaviour, Try a note)."""

from __future__ import annotations

import p03_classifier_lab as p03
import pytest

from shared import classifier, db
from shared.call_notes import CATEGORIES

FOOD = "Family of 6 has no food left, asking for a food parcel and rice."
VAGUE = "Caller asked a general question about the office."


@pytest.fixture
def model(conn):  # noqa: ANN001, ANN201
    return classifier.train(conn)


def test_app_starts(app_test) -> None:  # noqa: ANN001
    at = app_test(3).run()
    assert not at.exception


def test_evaluation_is_consistent(conn, model) -> None:  # noqa: ANN001
    ev = p03.evaluate(conn, model)
    n_test = p03.count_notes(conn, "test")
    assert ev.n_test == n_test == 217
    assert sum(sum(r) for r in ev.matrix) == n_test
    diag = sum(ev.matrix[i][i] for i in range(len(ev.labels)))
    assert ev.accuracy == pytest.approx(diag / n_test)
    assert round(ev.accuracy, 3) == 0.765
    assert len(ev.misclassified) == n_test - diag
    for i, row in enumerate(ev.per_category):
        assert row["category"] == ev.labels[i]
        assert row["notes"] == sum(ev.matrix[i])
        assert row["correct"] == ev.matrix[i][i]
        assert row["accuracy"] == pytest.approx(ev.matrix[i][i] / sum(ev.matrix[i]))
    assert sum(r["notes"] for r in ev.per_category) == n_test
    assert sum(r["notes"] for r in ev.per_language) == n_test
    assert all(m["true"] != m["predicted"] for m in ev.misclassified)
    assert p03.weakest_category(ev)["category"] == "health"


def test_threshold_blank_and_shown(conn, model) -> None:  # noqa: ANN001
    tiny = p03.evaluate(conn, model, 0.0001)
    assert tiny.blank_count == 0 and tiny.shown_count == tiny.n_test
    assert tiny.shown_accuracy == pytest.approx(tiny.accuracy)
    strict = p03.evaluate(conn, model, 0.99)
    assert strict.blank_count + strict.shown_count == strict.n_test
    assert strict.blank_share == pytest.approx(strict.blank_count / strict.n_test)
    assert strict.blank_count > tiny.blank_count
    impossible = p03.evaluate(conn, model, 1.01)
    assert impossible.blank_share == 1.0 and impossible.shown_accuracy is None
    default = p03.evaluate(conn, model)
    assert default.threshold == 0.6 and 0 < default.blank_count < default.n_test


def test_classifier_low_confidence_left_blank(model) -> None:  # noqa: ANN001
    top = classifier.predict(model, FOOD, 0.0)
    conf = top.confidence
    assert top.category == "food" and top.top_category == "food"
    # Below the threshold: blank, with the top guess kept as a hint.
    below = classifier.predict(model, FOOD, conf + 0.001)
    assert below.category is None and below.top_category == "food"
    # At the threshold: shown.
    assert classifier.predict(model, FOOD, conf).category == "food"
    # Above the threshold (threshold lower than the confidence): shown, even for a vague note at a tiny threshold.
    assert classifier.predict(model, FOOD, conf - 0.001).category == "food"
    assert classifier.predict(model, VAGUE, 0.0001).category is not None
    # A threshold of 0.99 blanks both.
    assert classifier.predict(model, FOOD, 0.99).category is None
    assert classifier.predict(model, VAGUE, 0.99).category is None


def test_empty_text_is_blank(model) -> None:  # noqa: ANN001
    for text in ("", "   ", "\n"):
        pred = classifier.predict(model, text, 0.0)
        assert pred.category is None and pred.confidence == 0.0
        assert p03.try_note(model, text, 0.6)["blank"]


def test_try_note_probabilities(model) -> None:  # noqa: ANN001
    res = p03.try_note(model, FOOD, 0.6)
    assert not res["blank"] and res["category"] == "food"
    assert set(res["probabilities"]) == set(CATEGORIES)
    assert sum(res["probabilities"].values()) == pytest.approx(1.0, abs=0.01)


def test_threshold_comes_from_settings(conn) -> None:  # noqa: ANN001
    assert classifier.threshold_from_settings(conn) == 0.6
    db.set_setting(conn, "classifier_confidence_threshold", "0.9", None)
    assert classifier.threshold_from_settings(conn) == 0.9


def test_app_try_note_walkthrough(app_test) -> None:  # noqa: ANN001
    at = app_test(3).run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Training notes (count)"] == "383"
    assert metrics["Test notes (count)"] == "217"
    assert metrics["Accuracy, test notes (%)"] == "76.5%"

    at.text_area(key="p03_note").set_value(FOOD).run()
    assert not at.exception
    assert any("Predicted category: Food" in s.value for s in at.success)
    assert not any("Left blank for the operator" in w.value for w in at.warning)

    at.text_area(key="p03_note").set_value(VAGUE).run()
    assert not at.exception
    warnings = [w.value for w in at.warning]
    assert any("Left blank for the operator: confidence" in w and "below threshold" in w for w in warnings)
    at.selectbox(key="p03_operator").select("Health").run()
    assert any("Nothing was saved" in s.value for s in at.success)
