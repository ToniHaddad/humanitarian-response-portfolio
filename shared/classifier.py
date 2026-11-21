"""Call-note category classifier (Project 03 trains and evaluates it; Project 01 uses it).

Technique: classification. Character n-gram TF-IDF (works for Arabic and English
without a tokenizer) feeding a logistic regression. Trained on the synthetic
labelled notes in classifier_notes. Predictions below the confidence threshold are
returned as None so the operator fills the category in.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from shared import config, db

DEFAULT_THRESHOLD = 0.6
MODEL_FILE = "call_note_classifier.joblib"


@dataclass
class Prediction:
    category: str | None  # None when confidence is below the threshold
    confidence: float  # probability of the top category
    top_category: str  # the model's best guess, even when withheld


def build_pipeline() -> Pipeline:
    return Pipeline(
        [
            ("tfidf", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=2, sublinear_tf=True)),
            ("clf", LogisticRegression(max_iter=2000, C=4.0, random_state=0)),
        ]
    )


def load_notes(conn: sqlite3.Connection, split: str | None = None) -> tuple[list[str], list[str], list[str]]:
    sql = "SELECT text, label, language FROM classifier_notes"
    params: tuple[str, ...] = ()
    if split:
        sql += " WHERE split = ?"
        params = (split,)
    rows = conn.execute(sql + " ORDER BY note_id", params).fetchall()
    return [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]


def train(conn: sqlite3.Connection) -> Pipeline:
    texts, labels, _ = load_notes(conn, "train")
    model = build_pipeline()
    model.fit(texts, labels)
    return model


def model_path() -> Path:
    return config.models_dir() / MODEL_FILE


def save(model: Pipeline, path: Path | None = None) -> Path:
    target = path or model_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, target)
    return target


def load_or_train(conn: sqlite3.Connection | None = None, retrain: bool = False) -> Pipeline:
    """Load the cached model, or train it from classifier_notes and cache it."""
    path = model_path()
    if path.exists() and not retrain:
        return joblib.load(path)
    own = conn is None
    conn = conn or db.connect()
    try:
        model = train(conn)
    finally:
        if own:
            conn.close()
    save(model, path)
    return model


def predict(model: Pipeline, text: str, threshold: float = DEFAULT_THRESHOLD) -> Prediction:
    """Top category and confidence; category is None (left blank) below the threshold."""
    if not text or not text.strip():
        return Prediction(None, 0.0, "")
    probs = model.predict_proba([text])[0]
    best = int(probs.argmax())
    confidence = float(probs[best])
    top = str(model.classes_[best])
    return Prediction(top if confidence >= threshold else None, round(confidence, 3), top)


def threshold_from_settings(conn: sqlite3.Connection | None = None) -> float:
    return db.get_setting_float("classifier_confidence_threshold", DEFAULT_THRESHOLD, conn)
