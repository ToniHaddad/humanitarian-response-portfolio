"""Unmet needs: requests versus completed assistance by area, aid type and date.

Straightforward calculation, read-only. Rules:
  * Requests are service_requests created in the date range (both ends inclusive).
  * Not a need: closed as duplicate, withdrawn or not_eligible. Counted separately.
  * Completed: closed as 'assisted', or has a referral with family_received = 1
    (each request counted once, however many referrals it has).
  * Outstanding (gap) = counted requests - completed.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pandas as pd

from shared import db
from shared.call_notes import CATEGORIES, CATEGORY_LABELS

UNCATEGORISED = "uncategorised"
NOT_A_NEED = ("duplicate", "withdrawn", "not_eligible")
AID_TYPES: tuple[str, ...] = (*CATEGORIES, UNCATEGORISED)
AID_LABELS: dict[str, str] = {**CATEGORY_LABELS, UNCATEGORISED: "Not yet categorised"}
DEFAULT_DAYS = 30


def default_range(today: date) -> tuple[date, date]:
    return today - timedelta(days=DEFAULT_DAYS), today


def request_rows(conn: sqlite3.Connection, start: date, end: date) -> pd.DataFrame:
    """One row per request in the range, with its aid type and flags excluded / completed (0 or 1)."""
    placeholders = ",".join("?" for _ in NOT_A_NEED)
    sql = f"""
        SELECT r.request_id, r.area_id, r.created_at,
               COALESCE(r.category, '{UNCATEGORISED}') AS aid_type,
               CASE WHEN r.closure_outcome IN ({placeholders}) THEN 1 ELSE 0 END AS excluded,
               CASE WHEN r.closure_outcome = 'assisted'
                      OR EXISTS (SELECT 1 FROM referrals f
                                 WHERE f.request_id = r.request_id AND f.family_received = 1)
                    THEN 1 ELSE 0 END AS completed
        FROM service_requests r
        WHERE substr(r.created_at, 1, 10) BETWEEN ? AND ?
    """
    df = db.query_df(sql, [*NOT_A_NEED, start.isoformat(), end.isoformat()], conn=conn)
    # An excluded request is not a need, so it is never completed assistance.
    df["completed"] = df["completed"] * (1 - df["excluded"])
    return df


def filtered_rows(df: pd.DataFrame, aid_type: str | None = None, area_id: str | None = None) -> pd.DataFrame:
    if aid_type:
        df = df[df["aid_type"] == aid_type]
    if area_id:
        df = df[df["area_id"] == area_id]
    return df


def _areas(conn: sqlite3.Connection, area_id: str | None = None) -> pd.DataFrame:
    areas = db.query_df("SELECT area_id, name_en, x_km, y_km, population_estimate FROM areas ORDER BY area_id", conn=conn)
    return areas[areas["area_id"] == area_id] if area_id else areas


def _finish(df: pd.DataFrame) -> pd.DataFrame:
    df["outstanding"] = df["requests"] - df["completed"]
    df["completion_rate_pct"] = (100.0 * df["completed"] / df["requests"].where(df["requests"] > 0)).round(1)
    return df


def by_area(conn: sqlite3.Connection, start: date, end: date, aid_type: str | None = None, area_id: str | None = None) -> pd.DataFrame:
    """Area-level totals; all 12 areas (or the chosen one) appear, with zeros where nothing happened."""
    rows = filtered_rows(request_rows(conn, start, end), aid_type, area_id)
    counted = rows[rows["excluded"] == 0]
    agg = counted.groupby("area_id").agg(requests=("request_id", "count"), completed=("completed", "sum"))
    excluded = rows[rows["excluded"] == 1].groupby("area_id").size().rename("excluded")
    out = _areas(conn, area_id).merge(agg, how="left", left_on="area_id", right_index=True)
    out = out.merge(excluded, how="left", left_on="area_id", right_index=True)
    for col in ("requests", "completed", "excluded"):
        out[col] = out[col].fillna(0).astype(int)
    out = _finish(out)
    out["outstanding_per_1000"] = (1000.0 * out["outstanding"] / out["population_estimate"]).round(2)
    return out


def by_area_and_type(conn: sqlite3.Connection, start: date, end: date, area_id: str | None = None) -> pd.DataFrame:
    """Every area x aid type combination (zeros included): requests, completed, outstanding, completion rate."""
    rows = request_rows(conn, start, end)
    rows = rows[rows["excluded"] == 0]
    agg = rows.groupby(["area_id", "aid_type"]).agg(requests=("request_id", "count"), completed=("completed", "sum")).reset_index()
    grid = pd.MultiIndex.from_product([_areas(conn, area_id)["area_id"], AID_TYPES], names=["area_id", "aid_type"]).to_frame(index=False)
    out = grid.merge(agg, how="left", on=["area_id", "aid_type"])
    for col in ("requests", "completed"):
        out[col] = out[col].fillna(0).astype(int)
    out = _finish(out)
    out["aid_label"] = out["aid_type"].map(AID_LABELS)
    return out


def totals(conn: sqlite3.Connection, start: date, end: date, aid_type: str | None = None, area_id: str | None = None) -> dict[str, int]:
    rows = filtered_rows(request_rows(conn, start, end), aid_type, area_id)
    counted = rows[rows["excluded"] == 0]
    completed = int(counted["completed"].sum())
    return {
        "requests": len(counted),
        "completed": completed,
        "outstanding": len(counted) - completed,
        "excluded": int(rows["excluded"].sum()),
    }


def weekly(conn: sqlite3.Connection, start: date, end: date, aid_type: str | None = None, area_id: str | None = None) -> pd.DataFrame:
    """Counted requests and completed assistance by week (Monday start) of the request's creation date."""
    rows = filtered_rows(request_rows(conn, start, end), aid_type, area_id)
    rows = rows[rows["excluded"] == 0].copy()
    if rows.empty:
        return pd.DataFrame(columns=["week_start", "requests", "completed"])
    dates = pd.to_datetime(rows["created_at"].str[:10])
    rows["week_start"] = (dates - pd.to_timedelta(dates.dt.weekday, unit="D")).dt.date
    return rows.groupby("week_start").agg(requests=("request_id", "count"), completed=("completed", "sum")).reset_index()


def worked_example(row: pd.Series, aid_label: str | None = None) -> str:
    """Plain-words sentence for one area row (from by_area)."""
    what = f"{aid_label.lower()} " if aid_label else ""
    return (
        f"{row['name_en']}: {int(row['requests'])} {what}requests - {int(row['completed'])} completed "
        f"= {int(row['outstanding'])} outstanding. Population about {int(row['population_estimate']):,}, "
        f"so {row['outstanding_per_1000']:.2f} outstanding per 1,000 people."
    )
