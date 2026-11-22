"""Field data pipeline: validate field team spreadsheet exports and load them once (Project 07).

Entry point for both callers:
    * Project 07 runs it on demand ("Run now", trigger='manual').
    * The Project 20 scheduler runs it on a timer (trigger='scheduled'); the API may run it
      with trigger='api'. The caller records its own job_runs row; `summary.detail` is a
      one-line description for it.

    with db.transaction() as conn:
        summary = pipeline.run_pipeline(conn, trigger="scheduled")
    summary.to_dict()

What one file goes through (one pipeline_runs row per file):
    1. Source: the file name must match a pipeline_sources.file_pattern (e.g. team_north_*.csv).
    2. Structure: the CSV must have every required column after header clean-up
       (case, spaces and known aliases are tidied). Otherwise the run is 'failed'
       and nothing is loaded.
    3. Each row is cleaned (trimmed, codes upper-cased, dates in YYYY-MM-DD or DD/MM/YYYY,
       area code or English area name) and checked against the documented schema
       (COLUMNS). A row breaking a rule goes to pipeline_quarantine with the rule and reason.
    4. Valid rows are loaded into field_assessments with INSERT OR IGNORE on a UNIQUE
       row_key = sha256("source|household_id|assessment_date|need_category"). A row already
       loaded (from this file or any earlier one) counts as a duplicate and adds nothing,
       so loading the same file twice never creates duplicate rows.
    Status: 'success' (no row quarantined), 'partial' (some rows quarantined), 'failed'.

Scheduled runs skip files whose exact content (sha256) has been processed before, so the
timer does not re-read the same export every hour; manual and API runs read every file
(and still add nothing the second time). The functions take an open connection and do not
commit: call them inside db.transaction().
"""

from __future__ import annotations

import csv
import fnmatch
import hashlib
import io
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from shared import call_notes, clock, config, db

SAMPLE_DIR: Path = config.PROJECTS_DIR / "07-field-data-pipeline" / "sample_data"
TRIGGERS: tuple[str, ...] = ("scheduled", "manual", "api")
PRIORITIES: tuple[str, ...] = ("low", "medium", "high")
MAX_HOUSEHOLD_SIZE = 20

# The documented schema of a field team export: column -> description.
COLUMNS: dict[str, str] = {
    "household_id": "Household ID such as HH-00042 (HH-42 and hh 42 are accepted and padded). Must exist in the household register.",
    "area": "Area code (AR-01 to AR-12) or the area's English name.",
    "assessment_date": "Date of the visit: YYYY-MM-DD or DD/MM/YYYY.",
    "household_size": f"Number of people, a whole number from 1 to {MAX_HOUSEHOLD_SIZE}.",
    "need_category": "One of: "
    + ", ".join(call_notes.CATEGORIES)
    + " (case and spaces are tidied; 'wash' and 'water' mean water_hygiene).",
    "priority": "One of: " + ", ".join(PRIORITIES) + ".",
}
REQUIRED_COLUMNS: tuple[str, ...] = tuple(COLUMNS)

# Validation rules: rule -> reason stored with each quarantined row.
RULES: dict[str, str] = {
    "missing_household_id": "household_id is empty",
    "unknown_household": "household_id is not in the household register",
    "unknown_area": "area code is not in the area list",
    "bad_date": "assessment_date is not a valid date",
    "household_size_out_of_range": f"household_size must be 1-{MAX_HOUSEHOLD_SIZE}",
    "unknown_need_category": "need_category is not one of the six categories",
    "unknown_priority": "priority must be low, medium or high",
}

HEADER_ALIASES: dict[str, str] = {
    "hh_id": "household_id",
    "household": "household_id",
    "householdid": "household_id",
    "area_id": "area",
    "area_code": "area",
    "date": "assessment_date",
    "visit_date": "assessment_date",
    "size": "household_size",
    "hh_size": "household_size",
    "family_size": "household_size",
    "need": "need_category",
    "category": "need_category",
}
NEED_ALIASES: dict[str, str] = {"wash": "water_hygiene", "water": "water_hygiene", "water_and_hygiene": "water_hygiene"}

_HH_RE = re.compile(r"^HH[\s_-]*(\d{1,5})$", re.IGNORECASE)


@dataclass
class RunResult:
    """One file processed: the pipeline_runs row it wrote."""

    run_id: str
    file_name: str
    source_id: str | None
    status: str
    rows_read: int = 0
    rows_loaded: int = 0
    rows_duplicate: int = 0
    rows_quarantined: int = 0
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineSummary:
    """What run_pipeline did across all files."""

    trigger: str
    started_at: str
    finished_at: str = ""
    runs: list[RunResult] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)

    def _sum(self, attr: str) -> int:
        return sum(getattr(r, attr) for r in self.runs)

    @property
    def rows_read(self) -> int:
        return self._sum("rows_read")

    @property
    def rows_loaded(self) -> int:
        return self._sum("rows_loaded")

    @property
    def rows_duplicate(self) -> int:
        return self._sum("rows_duplicate")

    @property
    def rows_quarantined(self) -> int:
        return self._sum("rows_quarantined")

    @property
    def failed_runs(self) -> int:
        return sum(1 for r in self.runs if r.status == "failed")

    @property
    def status(self) -> str:
        """'failed' if any file failed, 'partial' if any rows were quarantined, 'success', or 'nothing_to_do'."""
        if not self.runs:
            return "nothing_to_do"
        if self.failed_runs:
            return "failed"
        return "partial" if self.rows_quarantined else "success"

    @property
    def detail(self) -> str:
        """One line for job_runs.detail."""
        if not self.runs:
            return f"No new files ({len(self.skipped_files)} already processed)"
        return (
            f"{len(self.runs)} file(s): {self.rows_loaded} loaded, {self.rows_duplicate} duplicate, "
            f"{self.rows_quarantined} quarantined, {self.failed_runs} failed"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "detail": self.detail,
            "rows_read": self.rows_read,
            "rows_loaded": self.rows_loaded,
            "rows_duplicate": self.rows_duplicate,
            "rows_quarantined": self.rows_quarantined,
            "failed_runs": self.failed_runs,
            "runs": [r.to_dict() for r in self.runs],
            "skipped_files": list(self.skipped_files),
        }


# -- cleaning and validation -----------------------------------------------
def row_key(source_id: str, household_id: str, assessment_date: str, need_category: str) -> str:
    """Natural key hash of a loaded row (the same formula the seed uses)."""
    return hashlib.sha256(f"{source_id}|{household_id}|{assessment_date}|{need_category}".encode()).hexdigest()


def clean_header(name: str) -> str:
    key = re.sub(r"[\s\-]+", "_", (name or "").strip().lower().lstrip("﻿"))
    return HEADER_ALIASES.get(key, key)


def match_source(conn: sqlite3.Connection, file_name: str) -> str | None:
    """The pipeline source whose file_pattern matches the file name, or None."""
    for source_id, pattern in conn.execute("SELECT source_id, file_pattern FROM pipeline_sources ORDER BY source_id"):
        if fnmatch.fnmatch(Path(file_name).name.lower(), pattern.lower()):
            return str(source_id)
    return None


def _parse_date(value: str) -> str | None:
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def validate_row(
    conn: sqlite3.Connection, raw: dict[str, Any], areas: dict[str, str] | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """Clean one row and check it. Returns (clean_row, None) or (None, rule)."""
    if areas is None:
        areas = _area_lookup(conn)
    v = {k: ("" if raw.get(k) is None else str(raw.get(k)).strip()) for k in REQUIRED_COLUMNS}
    if not v["household_id"]:
        return None, "missing_household_id"
    m = _HH_RE.match(v["household_id"].replace(" ", ""))
    hh = f"HH-{int(m.group(1)):05d}" if m else v["household_id"].upper()
    if not conn.execute("SELECT 1 FROM households WHERE household_id = ?", (hh,)).fetchone():
        return None, "unknown_household"
    area = areas.get(v["area"].lower())
    if area is None:
        return None, "unknown_area"
    adate = _parse_date(v["assessment_date"])
    if adate is None:
        return None, "bad_date"
    try:
        size = int(float(v["household_size"]))
        if float(v["household_size"]) != size:
            raise ValueError
    except ValueError:
        return None, "household_size_out_of_range"
    if not 1 <= size <= MAX_HOUSEHOLD_SIZE:
        return None, "household_size_out_of_range"
    need = re.sub(r"[\s\-/&]+", "_", v["need_category"].lower())
    need = NEED_ALIASES.get(need, need)
    if need not in call_notes.CATEGORIES:
        return None, "unknown_need_category"
    priority = v["priority"].lower()
    if priority not in PRIORITIES:
        return None, "unknown_priority"
    return {
        "household_id": hh,
        "area_id": area,
        "assessment_date": adate,
        "household_size": size,
        "need_category": need,
        "priority": priority,
    }, None


def _area_lookup(conn: sqlite3.Connection) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for area_id, name_en in conn.execute("SELECT area_id, name_en FROM areas"):
        lookup[str(area_id).lower()] = str(area_id)
        lookup[str(name_en).lower()] = str(area_id)
    return lookup


# -- one file ----------------------------------------------------------------
def ingest_bytes(conn: sqlite3.Connection, file_name: str, data: bytes, *, trigger: str, actor_id: str = "system") -> RunResult:
    """Validate and load one export. Writes one pipeline_runs row and its quarantine rows."""
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {', '.join(TRIGGERS)}")
    name = Path(file_name).name
    sha = hashlib.sha256(data).hexdigest()
    run_id = db.next_id(conn, "pipeline_runs", "run_id", "RUN-", 4)
    source_id = match_source(conn, name)
    started = clock.iso()
    conn.execute(
        "INSERT INTO pipeline_runs(run_id, started_at, trigger, source_id, file_name, file_sha256, status) VALUES (?,?,?,?,?,?, 'running')",
        (run_id, started, trigger, source_id, name, sha),
    )
    result = RunResult(run_id=run_id, file_name=name, source_id=source_id, status="running")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = None
    if source_id is None:
        result.status, result.error_message = (
            "failed",
            f"File name '{name}' matches no known field team source; file rejected before loading.",
        )
    elif text is None:
        result.status, result.error_message = "failed", "File is not UTF-8 text (CSV expected); file rejected before loading."
    else:
        reader = csv.DictReader(io.StringIO(text))
        headers = {clean_header(h): h for h in (reader.fieldnames or [])}
        missing = [c for c in REQUIRED_COLUMNS if c not in headers]
        if missing:
            result.status = "failed"
            result.error_message = f"Missing required column(s) {', '.join(repr(c) for c in missing)}; file rejected before loading."
        else:
            areas = _area_lookup(conn)
            loaded_at = clock.iso()
            for line_no, raw_row in enumerate(reader, start=2):  # row 1 is the header
                if not any((value or "").strip() for value in raw_row.values() if isinstance(value, str)):
                    continue  # blank spreadsheet line
                result.rows_read += 1
                raw = {c: raw_row.get(headers[c]) for c in REQUIRED_COLUMNS}
                clean, rule = validate_row(conn, raw, areas)
                if clean is None:
                    assert rule is not None
                    conn.execute(
                        "INSERT INTO pipeline_quarantine(run_id, source_id, row_number, raw_row_json, rule, reason) VALUES (?,?,?,?,?,?)",
                        (run_id, source_id, line_no, json.dumps(raw_row, ensure_ascii=False), rule, RULES[rule]),
                    )
                    result.rows_quarantined += 1
                    continue
                key = row_key(source_id, clean["household_id"], clean["assessment_date"], clean["need_category"])
                cur = conn.execute(
                    """INSERT OR IGNORE INTO field_assessments(row_key, source_id, household_id, area_id, assessment_date,
                                                               household_size, need_category, priority, loaded_run_id, loaded_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        key,
                        source_id,
                        clean["household_id"],
                        clean["area_id"],
                        clean["assessment_date"],
                        clean["household_size"],
                        clean["need_category"],
                        clean["priority"],
                        run_id,
                        loaded_at,
                    ),
                )
                if cur.rowcount:
                    result.rows_loaded += 1
                else:
                    result.rows_duplicate += 1
            result.status = "partial" if result.rows_quarantined else "success"
    conn.execute(
        """UPDATE pipeline_runs SET finished_at = ?, status = ?, rows_read = ?, rows_loaded = ?, rows_duplicate = ?,
                  rows_quarantined = ?, error_message = ? WHERE run_id = ?""",
        (
            clock.iso(),
            result.status,
            result.rows_read,
            result.rows_loaded,
            result.rows_duplicate,
            result.rows_quarantined,
            result.error_message,
            run_id,
        ),
    )
    db.audit(
        conn,
        actor_id=actor_id,
        channel=f"pipeline:{trigger}",
        action="pipeline_run",
        entity_table="pipeline_runs",
        entity_id=run_id,
        after=result.to_dict(),
        reason=result.error_message,
    )
    return result


def ingest_file(conn: sqlite3.Connection, path: Path | str, *, trigger: str, actor_id: str = "system") -> RunResult:
    p = Path(path)
    return ingest_bytes(conn, p.name, p.read_bytes(), trigger=trigger, actor_id=actor_id)


def already_processed(conn: sqlite3.Connection, sha256: str) -> bool:
    return conn.execute("SELECT 1 FROM pipeline_runs WHERE file_sha256 = ? LIMIT 1", (sha256,)).fetchone() is not None


def sample_files(source_dir: Path | str | None = None) -> list[Path]:
    """The CSV exports waiting in the folder (top level only), in name order."""
    folder = Path(source_dir) if source_dir else SAMPLE_DIR
    return sorted(p for p in folder.glob("*.csv") if p.is_file())


# -- entry point -------------------------------------------------------------
def run_pipeline(
    conn: sqlite3.Connection,
    *,
    trigger: str,
    files: list[Path | str] | None = None,
    source_dir: Path | str | None = None,
    skip_seen: bool | None = None,
    actor_id: str = "system",
) -> PipelineSummary:
    """Ingest field team exports and return a summary.

    files: explicit paths; default every *.csv directly in source_dir (default SAMPLE_DIR,
    the Project 07 sample_data folder). skip_seen: skip files whose content was processed
    before; default True for trigger='scheduled', False otherwise. Writes pipeline_runs,
    pipeline_quarantine, field_assessments and audit_log; does not commit.
    """
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {', '.join(TRIGGERS)}")
    if skip_seen is None:
        skip_seen = trigger == "scheduled"
    paths = [Path(f) for f in files] if files is not None else sample_files(source_dir)
    summary = PipelineSummary(trigger=trigger, started_at=clock.iso())
    for path in paths:
        data = path.read_bytes()
        if skip_seen and already_processed(conn, hashlib.sha256(data).hexdigest()):
            summary.skipped_files.append(path.name)
            continue
        summary.runs.append(ingest_bytes(conn, path.name, data, trigger=trigger, actor_id=actor_id))
    summary.finished_at = clock.iso()
    return summary
