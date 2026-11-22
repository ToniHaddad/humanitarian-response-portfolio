"""Project 20 page helpers: tables for the Streamlit front end of the platform.

The platform logic lives in shared/ (alerts, scheduler, simulator, auth, api) so
Projects 14 and 17 and server mode use the same code. This module only shapes
what the page shows.
"""

from __future__ import annotations

import socket
import sqlite3
from contextlib import closing
from typing import Any

import pandas as pd

from shared import alerts, auth, confirm, db, ports, scheduler, simulator

STATUS_FILTERS: dict[str, tuple[str, ...]] = {
    "Active (open and acknowledged)": ("open", "acknowledged"),
    "Open": ("open",),
    "Acknowledged": ("acknowledged",),
    "Resolved": ("resolved",),
    "All": ("open", "acknowledged", "resolved"),
}


def save_rule(
    conn: sqlite3.Connection,
    rule_id: str,
    confirmed_by: str | None,
    *,
    threshold: float,
    enabled: bool,
    channel: str = "app:20",
) -> dict[str, Any]:
    """The page's rule change: needs a confirming coordinator (or admin), then the shared audited update.

    `alerts.update_rule` stays free of confirmation because the API and scripts call it directly.
    """
    staff = confirm.require_confirmation(conn, confirmed_by, auth.ROLES["rules"])
    return alerts.update_rule(conn, rule_id, staff, threshold=threshold, enabled=enabled, channel=channel)


def switch_simulator(conn: sqlite3.Connection, enabled: bool, confirmed_by: str | None, channel: str = "app:20") -> None:
    """The page's simulator switch: needs a confirming coordinator (or admin), then the shared audited switch."""
    staff = confirm.require_confirmation(conn, confirmed_by, auth.ROLES["simulator"])
    simulator.set_enabled(conn, enabled, staff, channel=channel)


def read() -> closing[sqlite3.Connection]:
    """A plain read connection (no write lock), closed when the block ends."""
    return closing(db.connect())


def api_reachable(host: str = "localhost", port: int = ports.API_PORT, timeout: float = 0.3) -> bool:
    """Quick local check whether the API port answers (the page works either way)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def alert_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) FROM alerts GROUP BY status").fetchall()
    out = {"open": 0, "acknowledged": 0, "resolved": 0}
    out.update({r[0]: int(r[1]) for r in rows})
    return out


def alerts_frame(conn: sqlite3.Connection, statuses: tuple[str, ...]) -> pd.DataFrame:
    rows = [a for a in alerts.list_alerts(conn, None, 1000) if a["status"] in statuses]
    cols = ["alert_id", "status", "severity", "rule_name", "entity_ref", "observed_value", "threshold", "message", "fired_at"]
    cols += ["acknowledged_by", "acknowledged_at", "resolved_at"]
    return pd.DataFrame(rows, columns=cols)


def open_alert_options(conn: sqlite3.Connection) -> dict[int, str]:
    return {a["alert_id"]: f"#{a['alert_id']} · {a['entity_ref']} · {a['message']}" for a in alerts.list_alerts(conn, "open", 1000)}


def rules_frame(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.DataFrame(alerts.list_rules(conn))
    df["enabled"] = df["enabled"].astype(bool)
    return df[["rule_id", "name", "rule_type", "threshold", "enabled", "severity", "description"]]


def health(conn: sqlite3.Connection) -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    report = scheduler.health_report(conn)
    services = pd.DataFrame(report["services"])[["service_name", "status", "last_seen_at", "minutes_since_seen", "silent", "detail"]]
    jobs = pd.DataFrame(report["jobs"])[
        [
            "job_name",
            "enabled",
            "interval_seconds",
            "last_success_at",
            "minutes_since_success",
            "overdue",
            "last_run_status",
            "last_run_trigger",
            "last_run_at",
            "last_run_detail",
        ]
    ]
    return services, jobs, bool(report["healthy"])


def jobs_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return scheduler.health_report(conn)["jobs"]


def positions_frame(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.DataFrame(simulator.vehicle_positions(conn))
    return df[
        ["vehicle_id", "simulated", "stale", "minutes_since_report", "reported_at", "x_km", "y_km", "speed_kmh", "source", "convoy_id"]
    ]


def recent_runs(conn: sqlite3.Connection, limit: int = 15) -> pd.DataFrame:
    return db.query_df(
        "SELECT job_run_id, job_name, trigger, status, started_at, finished_at, detail FROM job_runs ORDER BY job_run_id DESC LIMIT ?",
        (limit,),
        conn,
    )


def audit_frame(conn: sqlite3.Connection, channel: str | None = None, limit: int = 100) -> pd.DataFrame:
    sql = "SELECT audit_id, at, actor_id, actor_role, channel, action, entity_table, entity_id, before_json, after_json, reason FROM audit_log"
    params: list[Any] = []
    if channel:
        sql += " WHERE channel = ?"
        params.append(channel)
    sql += " ORDER BY audit_id DESC LIMIT ?"
    params.append(limit)
    return db.query_df(sql, params, conn)


def audit_channels(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT channel FROM audit_log ORDER BY channel")]


# -- displayed frames: short headers with units, the columns a person needs first ------------------
def short(value: Any, limit: int = 60) -> Any:
    """Cut long text for a table cell (the full text is shown in a detail view)."""
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 3].rstrip() + "..."
    return value


def _stamp(value: Any) -> Any:
    return value.replace("T", " ")[:19] if isinstance(value, str) else value


def rules_display(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["rule_id", "name", "threshold", "enabled", "severity", "rule_type"]].copy()
    out.columns = pd.Index(["Rule", "Name", "Threshold", "On", "Severity", "Type"])
    return out


def alerts_display(df: pd.DataFrame) -> pd.DataFrame:
    out = df[
        ["alert_id", "status", "severity", "rule_name", "entity_ref", "observed_value", "threshold", "fired_at", "acknowledged_by"]
    ].copy()
    out["fired_at"] = out["fired_at"].map(_stamp)
    out.columns = pd.Index(["Alert", "Status", "Severity", "Rule", "Entity", "Observed", "Threshold", "Fired at", "Ack by"])
    return out


def positions_display(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["vehicle_id", "stale", "minutes_since_report", "x_km", "y_km", "speed_kmh", "source", "convoy_id"]].copy()
    out.columns = pd.Index(["Vehicle", "Stale", "Age (min)", "East (km)", "North (km)", "Speed (km/h)", "Source", "Convoy"])
    return out


def runs_display(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["job_run_id", "job_name", "trigger", "status", "started_at", "detail"]].copy()
    out["started_at"] = out["started_at"].map(_stamp)
    out["detail"] = out["detail"].map(short)
    out.columns = pd.Index(["Run", "Job", "Trigger", "Status", "Started", "Result"])
    return out


def services_display(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["service_name", "status", "silent", "minutes_since_seen", "last_seen_at", "detail"]].copy()
    out["last_seen_at"] = out["last_seen_at"].map(_stamp)
    out["detail"] = out["detail"].map(short)
    out.columns = pd.Index(["Service", "Status", "Silent", "Age (min)", "Last seen", "Detail"])
    return out


def jobs_display(df: pd.DataFrame) -> pd.DataFrame:
    out = df[
        ["job_name", "overdue", "last_run_status", "last_run_at", "last_success_at", "minutes_since_success", "interval_seconds"]
    ].copy()
    out["last_run_at"] = out["last_run_at"].map(_stamp)
    out["last_success_at"] = out["last_success_at"].map(_stamp)
    out.columns = pd.Index(["Job", "Overdue", "Last run", "Last run at", "Last success", "Since success (min)", "Every (s)"])
    return out


def audit_display(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["audit_id", "at", "actor_id", "actor_role", "channel", "action", "entity_table", "entity_id", "reason"]].copy()
    out["at"] = out["at"].map(_stamp)
    out["reason"] = out["reason"].map(short)
    out.columns = pd.Index(["Audit", "At", "Who", "Role", "Channel", "Action", "Table", "Entity", "Reason"])
    return out
