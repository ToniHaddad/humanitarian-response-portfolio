"""Project 19: local TF-IDF search over fictional situation report passages.

Only reads. Returns passages, never a composed answer.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

# A passage must reach this cosine similarity (0 to 1) with the question to be shown.
DEFAULT_THRESHOLD = 0.12
DEFAULT_TOP_K = 5

NOTHING_FOUND = "Nothing relevant found. No passage in the library matches this question well enough to show."
EMPTY_QUESTION = "Type a question to search the library."
ONLY_COMMON_WORDS = (
    "The question has only common words (such as 'the' or 'what'). Add a specific topic, for example a need, a place or an event."
)


@dataclass(frozen=True)
class Hit:
    passage_id: int
    sitrep_id: str
    title: str
    report_date: str
    heading: str
    text: str
    score: float


@dataclass(frozen=True)
class SearchResult:
    status: str  # "found", "nothing_found", "empty", "common_words"
    message: str
    hits: list[Hit]


class Index:
    """A TF-IDF index over every passage (heading, report title and text)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            """SELECT p.passage_id, p.sitrep_id, s.title, s.report_date, p.heading, p.text
               FROM sitrep_passages p JOIN sitreps s ON s.sitrep_id = p.sitrep_id
               ORDER BY p.passage_id"""
        ).fetchall()
        self.rows = [tuple(r) for r in rows]
        docs = [f"{r[2]}. {r[4] or ''}. {r[5]}" for r in self.rows]
        self.vectorizer = TfidfVectorizer(lowercase=True, stop_words="english", sublinear_tf=True)
        self.matrix = self.vectorizer.fit_transform(docs) if docs else None

    def search(self, question: str | None, threshold: float = DEFAULT_THRESHOLD, top_k: int = DEFAULT_TOP_K) -> SearchResult:
        q = (question or "").strip()
        if not q:
            return SearchResult("empty", EMPTY_QUESTION, [])
        if self.matrix is None:
            return SearchResult("nothing_found", NOTHING_FOUND, [])
        words = [w for w in self.vectorizer.build_analyzer()(q)]
        if not words and all(t.lower() in ENGLISH_STOP_WORDS for t in q.replace("?", " ").split()):
            return SearchResult("common_words", ONLY_COMMON_WORDS, [])
        vec = self.vectorizer.transform([q])
        if vec.nnz == 0:
            return SearchResult("nothing_found", NOTHING_FOUND, [])
        scores = linear_kernel(vec, self.matrix).ravel()
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], self.rows[i][0]))
        hits = [
            Hit(r[0], r[1], r[2], r[3], r[4] or "", r[5], round(float(scores[i]), 3))
            for i in order[:top_k]
            if scores[i] >= threshold
            for r in [self.rows[i]]
        ]
        if not hits:
            return SearchResult("nothing_found", NOTHING_FOUND, [])
        return SearchResult("found", f"{len(hits)} passage(s) found, best match first.", hits)


def get_report(conn: sqlite3.Connection, sitrep_id: str) -> dict | None:
    row = conn.execute("SELECT sitrep_id, title, report_date, area_id, body_md FROM sitreps WHERE sitrep_id = ?", (sitrep_id,)).fetchone()
    return dict(row) if row else None


def list_reports(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT sitrep_id, title, report_date FROM sitreps ORDER BY report_date DESC").fetchall()
    return [dict(r) for r in rows]
