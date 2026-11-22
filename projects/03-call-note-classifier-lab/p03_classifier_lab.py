"""Project 03 logic: evaluate the shared call-note classifier on the held-out test split.

The model itself lives in shared/classifier.py (Project 01 uses the same one). This
module only measures it: accuracy overall and per category, the confusion matrix,
the misclassified notes, and what happens at the confidence threshold.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from shared import classifier
from shared.call_notes import CATEGORIES


@dataclass
class Evaluation:
    n_test: int
    accuracy: float
    per_category: list[dict[str, Any]]  # category, notes, correct, accuracy (recall)
    per_language: list[dict[str, Any]]  # language, notes, correct, accuracy
    labels: tuple[str, ...]
    matrix: list[list[int]]  # rows true, columns predicted
    misclassified: list[dict[str, Any]] = field(default_factory=list)
    threshold: float = classifier.DEFAULT_THRESHOLD
    blank_count: int = 0
    blank_share: float = 0.0
    shown_count: int = 0
    shown_accuracy: float | None = None  # None when every prediction is left blank


def count_notes(conn: sqlite3.Connection, split: str) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM classifier_notes WHERE split = ?", (split,)).fetchone()[0])


def evaluate(conn: sqlite3.Connection, model: Any, threshold: float | None = None) -> Evaluation:
    """Score the model on the test split (notes it was not trained on)."""
    if threshold is None:
        threshold = classifier.threshold_from_settings(conn)
    texts, truth, langs = classifier.load_notes(conn, "test")
    labels = tuple(CATEGORIES)
    index = {c: i for i, c in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    per_cat = {c: [0, 0] for c in labels}  # notes, correct
    per_lang: dict[str, list[int]] = {}
    wrong: list[dict[str, Any]] = []
    shown = shown_ok = 0

    probs = model.predict_proba(texts) if texts else []
    classes = [str(c) for c in model.classes_]
    for text, true, lang, row in zip(texts, truth, langs, probs, strict=True):
        best = int(row.argmax())
        pred, conf = classes[best], round(float(row[best]), 3)
        matrix[index[true]][index[pred]] += 1
        ok = pred == true
        per_cat[true][0] += 1
        per_cat[true][1] += ok
        entry = per_lang.setdefault(lang, [0, 0])
        entry[0] += 1
        entry[1] += ok
        if not ok:
            wrong.append({"text": text, "language": lang, "true": true, "predicted": pred, "confidence": conf})
        if conf >= threshold:
            shown += 1
            shown_ok += ok

    n = len(texts)
    correct = sum(matrix[i][i] for i in range(len(labels)))
    wrong.sort(key=lambda w: -w["confidence"])
    return Evaluation(
        n_test=n,
        accuracy=correct / n if n else 0.0,
        per_category=[
            {"category": c, "notes": v[0], "correct": v[1], "accuracy": v[1] / v[0] if v[0] else 0.0} for c, v in per_cat.items()
        ],
        per_language=[{"language": lang, "notes": v[0], "correct": v[1], "accuracy": v[1] / v[0]} for lang, v in sorted(per_lang.items())],
        labels=labels,
        matrix=matrix,
        misclassified=wrong,
        threshold=threshold,
        blank_count=n - shown,
        blank_share=(n - shown) / n if n else 0.0,
        shown_count=shown,
        shown_accuracy=shown_ok / shown if shown else None,
    )


def weakest_category(ev: Evaluation) -> dict[str, Any]:
    return min(ev.per_category, key=lambda r: (r["accuracy"], r["category"]))


def probabilities(model: Any, text: str) -> dict[str, float]:
    """Probability per category for a note (empty for blank text)."""
    if not text or not text.strip():
        return {}
    probs = model.predict_proba([text])[0]
    return {str(c): round(float(p), 3) for c, p in zip(model.classes_, probs, strict=True)}


def try_note(model: Any, text: str, threshold: float) -> dict[str, Any]:
    """What the reviewer sees for a typed note: category (None = blank), confidence, hint, probabilities."""
    pred = classifier.predict(model, text, threshold)
    return {
        "category": pred.category,
        "confidence": pred.confidence,
        "top_category": pred.top_category,
        "blank": pred.category is None,
        "probabilities": probabilities(model, text),
    }
