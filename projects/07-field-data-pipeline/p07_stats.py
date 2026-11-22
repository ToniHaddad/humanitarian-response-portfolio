"""Project 07 logic: run history, error rates and quarantine queries over the pipeline tables.

The ingestion itself lives in shared/pipeline.py (shared with the Project 20 scheduler);
this module only reads what it wrote and does the straightforward calculations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from shared import db, pipeline

BROKEN_SAMPLE: Path = pipeline.SAMPLE_DIR / "broken" / "team_north_2026-11-22.csv"


def error_rate(quarantined: int, rows_read: int) -> float:
    """Quarantined rows as a percentage of rows read, rounded to 0.1. 0.0 when nothing was read."""
    if rows_read <= 0:
        return 0.0
    return round(100.0 * quarantined / rows_read, 1)


def run_history(conn: sqlite3.Connection, limit: int | None = None) -> pd.DataFrame:
    """Pipeline runs, newest first."""
    sql = (
        "SELECT run_id, started_at, trigger, source_id, file_name, status, rows_read, rows_loaded, "
        "rows_duplicate, rows_quarantined, error_message FROM pipeline_runs ORDER BY started_at DESC, run_id DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return db.query_df(sql, conn=conn)


def error_rates_by_source(conn: sqlite3.Connection) -> pd.DataFrame:
    """Per source: rows read, rows quarantined and error rate (%) across all runs."""
    df = db.query_df(
        "SELECT s.source_id, COALESCE(SUM(r.rows_read), 0) AS rows_read, COALESCE(SUM(r.rows_quarantined), 0) AS rows_quarantined "
        "FROM pipeline_sources s LEFT JOIN pipeline_runs r ON r.source_id = s.source_id GROUP BY s.source_id ORDER BY s.source_id",
        conn=conn,
    )
    df["error_rate_pct"] = [error_rate(q, n) for q, n in zip(df["rows_quarantined"], df["rows_read"], strict=True)]
    return df


def error_rates_by_rule(conn: sqlite3.Connection) -> pd.DataFrame:
    """Per documented rule: rows quarantined and that count as a percentage of all rows read."""
    total_read = int(db.scalar("SELECT COALESCE(SUM(rows_read), 0) FROM pipeline_runs", conn=conn))
    counts = {r["rule"]: r["n"] for r in db.query_all("SELECT rule, COUNT(*) AS n FROM pipeline_quarantine GROUP BY rule", conn=conn)}
    rules = list(pipeline.RULES) + [r for r in counts if r not in pipeline.RULES]
    return pd.DataFrame(
        {
            "rule": rules,
            "rows_quarantined": [int(counts.get(r, 0)) for r in rules],
            "error_rate_pct": [error_rate(int(counts.get(r, 0)), total_read) for r in rules],
        }
    )


def quarantine_rows(
    conn: sqlite3.Connection, run_id: str | None = None, rule: str | None = None, source_id: str | None = None
) -> pd.DataFrame:
    """Quarantined rows with the raw row, rule and reason, optionally filtered, newest run first."""
    sql = (
        "SELECT q.run_id, r.file_name, q.source_id, q.row_number, q.rule, q.reason, q.raw_row_json AS raw_row "
        "FROM pipeline_quarantine q JOIN pipeline_runs r ON r.run_id = q.run_id WHERE 1 = 1"
    )
    params: list[Any] = []
    for column, value in (("q.run_id", run_id), ("q.rule", rule), ("q.source_id", source_id)):
        if value:
            sql += f" AND {column} = ?"
            params.append(value)
    return db.query_df(sql + " ORDER BY q.run_id DESC, q.row_number", params, conn=conn)


def last_scheduled_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The latest scheduled pipeline_ingest job run, falling back to the latest scheduled pipeline run."""
    job = db.query_one(
        "SELECT started_at, status, detail FROM job_runs WHERE job_name = 'pipeline_ingest' AND trigger = 'scheduled' "
        "ORDER BY started_at DESC LIMIT 1",
        conn=conn,
    )
    if job:
        return {"started_at": job["started_at"], "status": job["status"], "detail": job["detail"], "from": "job_runs"}
    run = db.query_one(
        "SELECT started_at, status, file_name AS detail FROM pipeline_runs WHERE trigger = 'scheduled' ORDER BY started_at DESC LIMIT 1",
        conn=conn,
    )
    return {**run, "from": "pipeline_runs"} if run else None


def assessment_count(conn: sqlite3.Connection) -> int:
    return int(db.scalar("SELECT COUNT(*) FROM field_assessments", conn=conn))


def run_now(conn: sqlite3.Connection, actor_id: str = "system", include_broken: bool = False) -> pipeline.PipelineSummary:
    """Manual run on the sample folder (plus the broken sample on request). Caller wraps it in a transaction."""
    files: list[Path | str] = list(pipeline.sample_files())
    if include_broken:
        files.append(BROKEN_SAMPLE)
    return pipeline.run_pipeline(conn, trigger="manual", files=files, actor_id=actor_id)
