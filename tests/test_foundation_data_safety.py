"""Data safety: seed data must contain nothing shaped like a phone number or email address."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from shared import db

ROOT = Path(__file__).resolve().parents[1]

PHONE_PATTERNS = [
    re.compile(r"\+\d[\d\s().-]{6,}\d"),  # international prefix
    re.compile(r"(?<![\w.:-])\d{7,}(?![\w.:-])"),  # seven or more digits in a row
    re.compile(r"(?<![\w.:-])\d{2,4}[\s.-]\d{3}[\s.-]\d{3,4}(?![\w.:-])"),  # 03-123-456, 01 234 5678
    re.compile(r"\(\d{2,4}\)\s?\d{3}[\s.-]?\d{3,4}"),  # (01) 234-5678
    re.compile(r"(?<![\w-])0\d[\s/-]\d{6}(?![\w-])"),  # 03/123456 style local numbers
]
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IPV4_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")


def findings(text: str) -> list[str]:
    found = [m.group(0) for p in PHONE_PATTERNS for m in p.finditer(text)]
    found += [m.group(0) for m in EMAIL_PATTERN.finditer(text)]
    found += [m.group(0) for m in IPV4_PATTERN.finditer(text)]
    return found


@pytest.mark.parametrize(
    "sample",
    [
        "+00 3 123 456",
        "03-123-456",
        "71234567",
        "01 234 5678",
        "(01) 234-5678",
        "03/123456",
        "someone@example.org",
        "a.b@mail.co",
        "10.0.0.12",
    ],
)
def test_detector_catches_known_shapes(sample: str) -> None:
    assert findings(f"Call me on {sample} tomorrow"), sample


@pytest.mark.parametrize(
    "sample",
    ["HH-00042", "CALL-000123", "2026-11-16T10:00:00", "REQ-00007", "DSP-0012", "SITE-03", "8 kg", "6 x 1.5 L", "port 8502", "1.9.1"],
)
def test_detector_ignores_ids_dates_and_units(sample: str) -> None:
    assert not findings(sample), sample


def test_seeded_database_has_no_phone_email_or_ip(conn: sqlite3.Connection) -> None:
    problems: list[str] = []
    for table in db.table_names(conn):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        for row in conn.execute(f"SELECT * FROM {table}"):
            for col, value in zip(cols, row, strict=True):
                if isinstance(value, str):
                    for hit in findings(value):
                        problems.append(f"{table}.{col}: {hit!r}")
    assert not problems, problems[:20]


def test_sample_data_files_and_seed_content_are_clean() -> None:
    files = [ROOT / "shared" / "seed_content.py", ROOT / "shared" / "call_notes.py"]
    files += [p for p in (ROOT / "projects").glob("*/sample_data/**/*") if p.is_file()]
    problems = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        problems += [f"{path.relative_to(ROOT)}: {hit!r}" for hit in findings(text)]
    assert not problems, problems[:20]
