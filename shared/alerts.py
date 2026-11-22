"""Alert rules, their evaluation and the alert history (Project 20; read by 14, 17).

Four rule types live in `alert_rules`. Each uses the rule's `threshold` as its
seeded description says:

  stock_below        one alert per warehouse and item with on_hand < threshold.
                     Exactly at the threshold it does not fire.
  overdue_callbacks  one alert ('callbacks') when the number of overdue callbacks
                     is >= threshold. Overdue: callback_due_at < now, callback_done_at
                     is NULL and the request is not closed.
  stale_position     one alert per vehicle whose last report is more than threshold
                     minutes old (shared.fleet.is_stale: exactly at it is not stale).
                     A vehicle with no report at all is stale.
  pipeline_failed    one alert ('pipeline_ingest') when the failed pipeline_ingest
                     job runs within params_json.window_hours (default 24) are >= threshold.

evaluate_rules() never opens a second alert while one is open or acknowledged for
the same rule and entity_ref, and resolves open/acknowledged alerts whose condition
has cleared. Disabled rules are skipped entirely. Every alert write is audited.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from shared import clock, confirm, db, fleet

RULE_TYPES: tuple[str, ...] = ("stock_below", "overdue_callbacks", "stale_position", "pipeline_failed")
ACTIVE_STATUSES: tuple[str, ...] = ("open", "acknowledged")
ACK_ROLES: tuple[str, ...] = ("coordinator", "duty_officer", "warehouse_officer", "admin")
DEFAULT_PIPELINE_WINDOW_HOURS = 24.0


class AlertNotFound(LookupError):
    pass


class RuleNotFound(LookupError):
    pass


@dataclass(frozen=True)
class Finding:
    """One entity for which a rule's condition is currently true."""

    entity_ref: str
    observed_value: float
    message: str


@dataclass
class EvaluationResult:
    rules_evaluated: int = 0
    rules_skipped: int = 0
    fired: list[int] = field(default_factory=list)  # new alert IDs
    resolved: list[int] = field(default_factory=list)  # alert IDs resolved this run
    still_active: int = 0  # findings that already had an open or acknowledged alert

    def summary(self) -> str:
        return (
            f"{self.rules_evaluated} rules evaluated, {len(self.fired)} new alerts, "
            f"{len(self.resolved)} resolved, {self.still_active} already open or acknowledged"
        )

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["summary"] = self.summary()
        return out


# -- pure evaluators: condition only, easy to test at / above / below the limit --
def stock_below_findings(stock_rows: Iterable[Mapping[str, Any]], threshold: float) -> list[Finding]:
    """stock_rows: dicts with warehouse_id, item_id, on_hand (optional warehouse_name, item_name)."""
    out = []
    for r in stock_rows:
        on_hand = float(r["on_hand"])
        if on_hand < threshold:
            where = r.get("warehouse_name") or r["warehouse_id"]
            what = r.get("item_name") or r["item_id"]
            out.append(
                Finding(
                    f"{r['warehouse_id']}/{r['item_id']}",
                    on_hand,
                    f"{where}: {on_hand:g} {what} on hand, below {threshold:g}.",
                )
            )
    return out


def overdue_callback_findings(overdue_count: int, threshold: float) -> list[Finding]:
    if overdue_count >= threshold:
        return [Finding("callbacks", float(overdue_count), f"{overdue_count} callbacks are overdue (threshold {threshold:g}).")]
    return []


def stale_position_findings(last_reports: Mapping[str, str | datetime | None], threshold: float, now: datetime) -> list[Finding]:
    """last_reports: vehicle_id -> time of its last report (None when it never reported)."""
    out = []
    for vehicle_id, last in sorted(last_reports.items()):
        if not fleet.is_stale(last, now=now, limit_minutes=threshold):
            continue
        if last is None:
            out.append(Finding(vehicle_id, -1.0, f"{vehicle_id} has never reported a position."))
            continue
        when = last if isinstance(last, datetime) else datetime.fromisoformat(str(last))
        minutes = round((now - when).total_seconds() / 60.0, 1)
        out.append(Finding(vehicle_id, minutes, f"{vehicle_id} has not reported a position for {minutes:g} minutes (limit {threshold:g})."))
    return out


def pipeline_failed_findings(failed_count: int, threshold: float, window_hours: float) -> list[Finding]:
    if failed_count >= threshold:
        return [
            Finding(
                "pipeline_ingest",
                float(failed_count),
                f"{failed_count} failed pipeline run(s) in the last {window_hours:g} hours (threshold {threshold:g}).",
            )
        ]
    return []


# -- database readers ------------------------------------------------------
def stock_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return db.query_all(
        """SELECT s.warehouse_id, s.item_id, s.on_hand, w.name AS warehouse_name, i.name AS item_name
           FROM v_stock_on_hand s JOIN warehouses w USING (warehouse_id) JOIN items i USING (item_id)
           ORDER BY s.warehouse_id, s.item_id""",
        conn=conn,
    )


def overdue_callbacks(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict[str, Any]]:
    """Requests whose callback is past due, not done, and not closed."""
    return db.query_all(
        """SELECT request_id, area_id, status, priority, assigned_to, callback_due_at
           FROM service_requests
           WHERE callback_due_at IS NOT NULL AND callback_due_at < ? AND callback_done_at IS NULL AND status != 'closed'
           ORDER BY callback_due_at""",
        (clock.iso(now),),
        conn=conn,
    )


def last_reports(conn: sqlite3.Connection) -> dict[str, str | None]:
    rows = conn.execute(
        "SELECT v.vehicle_id, MAX(p.reported_at) FROM vehicles v LEFT JOIN position_reports p ON p.vehicle_id = v.vehicle_id GROUP BY v.vehicle_id"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def failed_pipeline_runs(conn: sqlite3.Connection, now: datetime, window_hours: float) -> int:
    since = clock.iso(now - timedelta(hours=window_hours))
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM job_runs WHERE job_name = 'pipeline_ingest' AND status = 'failed' AND started_at >= ? AND started_at <= ?",
            (since, clock.iso(now)),
        ).fetchone()[0]
    )


def _params(rule: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(rule.get("params_json") or "{}")
    except json.JSONDecodeError:
        return {}


def findings_for_rule(conn: sqlite3.Connection, rule: Mapping[str, Any], now: datetime) -> list[Finding]:
    threshold = float(rule["threshold"])
    kind = rule["rule_type"]
    if kind == "stock_below":
        return stock_below_findings(stock_rows(conn), threshold)
    if kind == "overdue_callbacks":
        return overdue_callback_findings(len(overdue_callbacks(conn, now)), threshold)
    if kind == "stale_position":
        return stale_position_findings(last_reports(conn), threshold, now)
    if kind == "pipeline_failed":
        window = float(_params(rule).get("window_hours", DEFAULT_PIPELINE_WINDOW_HOURS))
        return pipeline_failed_findings(failed_pipeline_runs(conn, now, window), threshold, window)
    raise ValueError(f"Unknown rule type {kind}")


# -- evaluation ------------------------------------------------------------
def evaluate_rules(conn: sqlite3.Connection, now: datetime | None = None, channel: str = "scheduler") -> EvaluationResult:
    """Evaluate every enabled rule, open new alerts and resolve cleared ones. Caller commits."""
    now = now or clock.now()
    at = clock.iso(now)
    result = EvaluationResult()
    for rule in db.query_all("SELECT * FROM alert_rules ORDER BY rule_id", conn=conn):
        if not rule["enabled"]:
            result.rules_skipped += 1
            continue
        result.rules_evaluated += 1
        findings = {f.entity_ref: f for f in findings_for_rule(conn, rule, now)}
        active = {
            a["entity_ref"]: a
            for a in db.query_all(
                "SELECT * FROM alerts WHERE rule_id = ? AND status IN ('open','acknowledged')",
                (rule["rule_id"],),
                conn=conn,
            )
        }
        for ref, f in findings.items():
            if ref in active:
                result.still_active += 1
                continue
            cur = conn.execute(
                """INSERT INTO alerts(rule_id, fired_at, entity_ref, observed_value, threshold, message, status)
                   VALUES (?,?,?,?,?,?, 'open')""",
                (rule["rule_id"], at, ref, f.observed_value, float(rule["threshold"]), f.message),
            )
            alert_id = int(cur.lastrowid)
            result.fired.append(alert_id)
            db.audit(
                conn,
                actor_id="system",
                channel=channel,
                action="fire",
                entity_table="alerts",
                entity_id=str(alert_id),
                after={"rule_id": rule["rule_id"], "entity_ref": ref, "observed_value": f.observed_value, "status": "open"},
                reason=f.message,
                at=at,
            )
        for ref, alert in active.items():
            if ref in findings:
                continue
            conn.execute("UPDATE alerts SET status = 'resolved', resolved_at = ? WHERE alert_id = ?", (at, alert["alert_id"]))
            result.resolved.append(int(alert["alert_id"]))
            db.audit(
                conn,
                actor_id="system",
                channel=channel,
                action="resolve",
                entity_table="alerts",
                entity_id=str(alert["alert_id"]),
                before={"status": alert["status"]},
                after={"status": "resolved", "resolved_at": at},
                reason=f"Condition cleared for {rule['rule_id']} {ref}",
                at=at,
            )
    return result


# -- acknowledgement and rule changes -------------------------------------
def get_alert(conn: sqlite3.Connection, alert_id: int) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,), conn=conn)
    if row is None:
        raise AlertNotFound(f"No alert {alert_id}")
    return row


def acknowledge(conn: sqlite3.Connection, alert_id: int, staff_id: str | None, channel: str = "app:20") -> dict[str, Any]:
    """Acknowledge an open alert. Needs a confirming staff member with an acknowledging role."""
    confirm.require_confirmation(conn, staff_id, ACK_ROLES)
    alert = get_alert(conn, alert_id)
    if alert["status"] != "open":
        raise ValueError(f"Alert {alert_id} is {alert['status']}; only open alerts can be acknowledged")
    at = clock.iso()
    conn.execute(
        "UPDATE alerts SET status = 'acknowledged', acknowledged_by = ?, acknowledged_at = ? WHERE alert_id = ?",
        (staff_id, at, alert_id),
    )
    db.audit(
        conn,
        actor_id=str(staff_id),
        channel=channel,
        action="acknowledge",
        entity_table="alerts",
        entity_id=str(alert_id),
        before={"status": "open"},
        after={"status": "acknowledged", "acknowledged_by": staff_id, "acknowledged_at": at},
        at=at,
    )
    return get_alert(conn, alert_id)


def get_rule(conn: sqlite3.Connection, rule_id: str) -> dict[str, Any]:
    row = db.query_one("SELECT * FROM alert_rules WHERE rule_id = ?", (rule_id,), conn=conn)
    if row is None:
        raise RuleNotFound(f"No alert rule {rule_id}")
    return row


def update_rule(
    conn: sqlite3.Connection,
    rule_id: str,
    staff_id: str,
    *,
    threshold: float | None = None,
    enabled: bool | None = None,
    channel: str = "app:20",
    reason: str | None = None,
) -> dict[str, Any]:
    """Change a rule's threshold and/or enabled flag, with an audit row."""
    before = get_rule(conn, rule_id)
    if threshold is not None and threshold < 0:
        raise ValueError("threshold must not be negative")
    new_threshold = float(threshold) if threshold is not None else float(before["threshold"])
    new_enabled = int(bool(enabled)) if enabled is not None else int(before["enabled"])
    conn.execute("UPDATE alert_rules SET threshold = ?, enabled = ? WHERE rule_id = ?", (new_threshold, new_enabled, rule_id))
    db.audit(
        conn,
        actor_id=staff_id,
        channel=channel,
        action="update",
        entity_table="alert_rules",
        entity_id=rule_id,
        before={"threshold": before["threshold"], "enabled": before["enabled"]},
        after={"threshold": new_threshold, "enabled": new_enabled},
        reason=reason,
    )
    return get_rule(conn, rule_id)


def list_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return db.query_all("SELECT * FROM alert_rules ORDER BY rule_id", conn=conn)


def list_alerts(conn: sqlite3.Connection, status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    sql = "SELECT a.*, r.name AS rule_name, r.severity FROM alerts a JOIN alert_rules r USING (rule_id)"
    params: list[Any] = []
    if status:
        sql += " WHERE a.status = ?"
        params.append(status)
    sql += " ORDER BY a.fired_at DESC, a.alert_id DESC LIMIT ?"
    params.append(int(limit))
    return db.query_all(sql, params, conn=conn)


def set_setting_audited(conn: sqlite3.Connection, key: str, value: str, staff_id: str, channel: str) -> None:
    """db.set_setting (which audits with channel 'settings'), then label that audit row with the real channel."""
    db.set_setting(conn, key, value, staff_id)
    conn.execute(
        "UPDATE audit_log SET channel = ? WHERE audit_id = (SELECT MAX(audit_id) FROM audit_log WHERE entity_table = 'settings' AND entity_id = ?)",
        (channel, key),
    )


def set_stale_limit(conn: sqlite3.Connection, minutes: float, staff_id: str, channel: str = "app:20") -> None:
    """Set the stale-position limit (setting read by 14 and 17) and keep every stale_position rule's threshold in step."""
    if not 0 < minutes <= 24 * 60:
        raise ValueError("The stale-position limit must be between 0 and 1440 minutes")
    set_setting_audited(conn, "stale_position_minutes", f"{minutes:g}", staff_id, channel)
    for rule in db.query_all("SELECT rule_id FROM alert_rules WHERE rule_type = 'stale_position'", conn=conn):
        update_rule(
            conn, rule["rule_id"], staff_id, threshold=minutes, channel=channel, reason="Follows the stale_position_minutes setting"
        )
