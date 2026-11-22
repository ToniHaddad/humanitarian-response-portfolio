"""Operations API (Project 20): a documented REST API over the shared database.

Run it:
    python -m shared.api                      # uvicorn on shared.ports.API_PORT (8600)
    uvicorn shared.api:app --port 8600

Docs: /docs (Swagger UI), /redoc, /openapi.json, and /reference, a self-contained
page with no external assets that works offline.

Roles. Read endpoints are open. Every write needs an `X-Staff-Id` header with an
active seeded staff ID whose role is allowed for that action (ROLES below):
401 without a valid staff ID, 403 for a role that is not allowed. In server mode
(PORTFOLIO_MODE=server) writes also need `Authorization: Bearer <PORTFOLIO_API_TOKEN>`.

Audit. Every change made through the API writes audit_log with channel 'api',
the actor and role, and before/after JSON, in the same transaction as the change.
"""

from __future__ import annotations

import html
import os
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from shared import alerts, auth, confirm, db, fleet, ports, scheduler, simulator

CHANNEL = "api"

# Which roles may perform each write action (shared.auth.ROLES). Admin is listed explicitly everywhere.
ROLES = auth.ROLES

DESCRIPTION = """
Operations API for the Waypoint Response Lab demo portfolio. Every record is fictional.

**Roles.** Read endpoints are open. Each write needs the header `X-Staff-Id` with a seeded staff ID
(for example `CRD-01` coordinator, `FW-01` field worker, `DO-01` duty officer, `ADM-01` admin).
The role comes from the `staff` table. 401: no or unknown staff ID. 403: role not allowed.

**Server mode** (`PORTFOLIO_MODE=server`): writes also need `Authorization: Bearer <PORTFOLIO_API_TOKEN>`.

**Audit.** Every change writes one or more `audit_log` rows with channel `api`.

An offline reference with every endpoint and its required roles is at `/reference`.
"""

TAGS = [
    {"name": "health", "description": "Services, scheduled jobs and their last successful run."},
    {"name": "jobs", "description": "Scheduled jobs and the 'run now' control."},
    {"name": "alerts", "description": "Alert rules, evaluation, history and acknowledgement."},
    {"name": "fleet", "description": "Vehicle positions, the position simulator and the stale limit."},
    {"name": "audit", "description": "The audit log of every change."},
    {"name": "data", "description": "Read-only views over the shared database."},
    {"name": "sync", "description": "Server side of the offline field sync (Project 08)."},
    {"name": "docs", "description": "Offline API reference."},
]

app = FastAPI(
    title="Waypoint Response Lab operations API",
    version="1.0.0",
    description=DESCRIPTION,
    openapi_tags=TAGS,
)


# -- errors ---------------------------------------------------------------
@app.exception_handler(LookupError)
async def _not_found(request: Request, exc: LookupError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc).strip("'\"")})


@app.exception_handler(confirm.ConfirmationRequired)
async def _forbidden(request: Request, exc: confirm.ConfirmationRequired) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def _bad_request(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# -- roles ----------------------------------------------------------------
@dataclass(frozen=True)
class Actor:
    staff_id: str
    role: str


def require(action: str) -> Callable[..., Actor]:
    """Dependency for a write action: checks the server-mode token, the staff ID and its role."""
    allowed = ROLES[action]

    def dependency(
        x_staff_id: str | None = Header(None, alias="X-Staff-Id", description="Seeded staff ID making the change, e.g. CRD-01"),
        authorization: str | None = Header(None, description="Server mode only: Bearer <PORTFOLIO_API_TOKEN>"),
    ) -> Actor:
        if auth.server_mode() and not auth.check_api_token(auth.bearer_token(authorization)):
            raise HTTPException(
                401, "Server mode: a valid 'Authorization: Bearer <token>' header is required for changes.", {"WWW-Authenticate": "Bearer"}
            )
        if not x_staff_id or not x_staff_id.strip():
            raise HTTPException(401, "Send the X-Staff-Id header with your staff ID for any change.")
        with closing(db.connect()) as conn:
            row = conn.execute("SELECT role, active FROM staff WHERE staff_id = ?", (x_staff_id.strip(),)).fetchone()
        if row is None or not row["active"]:
            raise HTTPException(401, f"Unknown or inactive staff ID: {x_staff_id}")
        if row["role"] not in allowed:
            raise HTTPException(403, f"{x_staff_id} ({row['role']}) may not do this; allowed roles: {', '.join(allowed)}.")
        return Actor(x_staff_id.strip(), row["role"])

    return dependency


def roles_doc(action: str) -> dict[str, Any]:
    return {"x-required-roles": list(ROLES[action])}


WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "No or unknown staff ID (or, in server mode, no valid bearer token)"},
    403: {"description": "The staff member's role may not perform this action"},
}


# -- models ---------------------------------------------------------------
class ServiceStatus(BaseModel):
    service_name: str
    last_seen_at: str
    status: Literal["ok", "degraded", "down"]
    detail: str | None = None
    minutes_since_seen: float | None = Field(None, description="Minutes since the last heartbeat")
    silent: bool = Field(description=f"True when not seen for more than {scheduler.SERVICE_SILENT_MINUTES:g} minutes")


class JobStatus(BaseModel):
    job_name: str
    description: str
    interval_seconds: int
    enabled: bool
    last_success_at: str | None = Field(None, description="Finish time of the last successful run")
    minutes_since_success: float | None = None
    last_run_at: str | None = None
    last_run_status: str | None = None
    last_run_trigger: str | None = None
    last_run_detail: str | None = None
    overdue: bool = Field(description=f"No success within {scheduler.OVERDUE_FACTOR:g} x the interval")


class HealthReport(BaseModel):
    generated_at: str
    healthy: bool = Field(description="False when any job is overdue or failed, or any service is silent or not ok")
    services: list[ServiceStatus]
    jobs: list[JobStatus]


class JobRunResult(BaseModel):
    job_run_id: int | None
    job_name: str
    trigger: str | None = None
    status: Literal["success", "failed"]
    detail: str
    finished_at: str | None = None


class AlertRule(BaseModel):
    rule_id: str
    name: str
    rule_type: Literal["stock_below", "overdue_callbacks", "stale_position", "pipeline_failed"]
    threshold: float
    params_json: str
    description: str
    severity: Literal["info", "warning", "critical"]
    enabled: bool


class AlertRulePatch(BaseModel):
    threshold: float | None = Field(None, ge=0, description="New threshold (meaning depends on the rule type)")
    enabled: bool | None = Field(None, description="Switch the rule on or off")
    reason: str | None = Field(None, max_length=500, description="Why the rule was changed (kept in the audit log)")


class Alert(BaseModel):
    alert_id: int
    rule_id: str
    rule_name: str | None = None
    severity: str | None = None
    fired_at: str
    entity_ref: str = Field(description="What the alert is about, e.g. WH-03/ITM-HYG or VEH-07")
    observed_value: float | None
    threshold: float
    message: str
    status: Literal["open", "acknowledged", "resolved"]
    acknowledged_by: str | None = None
    acknowledged_at: str | None = None
    resolved_at: str | None = None


class EvaluationOut(BaseModel):
    rules_evaluated: int
    rules_skipped: int
    fired: list[int] = Field(description="IDs of new alerts")
    resolved: list[int] = Field(description="IDs of alerts resolved because their condition cleared")
    still_active: int = Field(description="Conditions that already had an open or acknowledged alert (not duplicated)")
    summary: str


class VehiclePosition(BaseModel):
    vehicle_id: str
    simulated: bool
    convoy_id: str | None = None
    reported_at: str | None = None
    x_km: float | None = None
    y_km: float | None = None
    speed_kmh: float | None = None
    source: str | None = None
    minutes_since_report: float | None = None
    stale: bool = Field(description="Older than the stale_position_minutes setting (exactly at the limit is not stale)")


class PositionIn(BaseModel):
    vehicle_id: str = Field(examples=["VEH-03"])
    x_km: float = Field(ge=0, le=100, description="Kilometres east on the invented 100 x 100 km grid")
    y_km: float = Field(ge=0, le=100, description="Kilometres north on the invented grid")
    speed_kmh: float | None = Field(None, ge=0, le=200)
    convoy_id: str | None = None


class PositionOut(BaseModel):
    report_id: int
    vehicle_id: str
    reported_at: str
    x_km: float
    y_km: float
    source: str


class SimulatorState(BaseModel):
    enabled: bool
    interval_seconds: float
    simulated_vehicle_ids: list[str]


class SimulatorSwitch(BaseModel):
    enabled: bool = Field(description="true starts the position simulator, false stops it")


class StaleLimit(BaseModel):
    minutes: float = Field(gt=0, le=1440, description="A position older than this many minutes is stale")


class SettingOut(BaseModel):
    key: str
    value: str
    updated_at: str
    updated_by: str | None = None


class AuditEntry(BaseModel):
    audit_id: int
    at: str
    actor_id: str
    actor_role: str
    channel: str
    action: str
    entity_table: str
    entity_id: str
    before_json: str | None = None
    after_json: str | None = None
    reason: str | None = None


class StockRow(BaseModel):
    warehouse_id: str
    item_id: str
    received: int
    dispatched: int
    on_hand: int


class OverdueCallback(BaseModel):
    request_id: str
    area_id: str
    status: str
    priority: str
    assigned_to: str | None = None
    callback_due_at: str


class Advisory(BaseModel):
    advisory_id: str
    area_id: str
    suggested_level: int
    final_level: int = Field(description="0 normal, 1 caution, 2 restricted, 3 no movement")
    is_override: bool
    override_reason: str | None = None
    decided_by: str
    decided_at: str


# -- health and jobs --------------------------------------------------------
@app.get(
    "/health",
    response_model=HealthReport,
    tags=["health"],
    summary="Health of every service and scheduled job",
    description="Lists every service in service_heartbeats and every scheduled job with its last successful run, last run status and an overdue flag. Also records an 'api' heartbeat.",
)
def get_health() -> dict[str, Any]:
    with db.transaction() as conn:
        scheduler.beat(conn, "api", "ok", "FastAPI operations API")
        return scheduler.health_report(conn)


@app.get(
    "/jobs",
    response_model=list[JobStatus],
    tags=["jobs"],
    summary="List scheduled jobs",
    description="Every row of scheduled_jobs with its last run.",
)
def get_jobs() -> list[dict[str, Any]]:
    with closing(db.connect()) as conn:
        return scheduler.health_report(conn)["jobs"]


@app.post(
    "/jobs/{job_name}/run",
    response_model=JobRunResult,
    tags=["jobs"],
    summary="Run a scheduled job now",
    description="Runs pipeline_ingest, alert_evaluation, position_simulator or db_backup immediately (trigger 'manual'), records it in job_runs and audits the request. A failing job returns status 'failed' with a readable detail.",
    openapi_extra=roles_doc("jobs"),
    responses=WRITE_RESPONSES,
)
def run_job_now(job_name: str, actor: Annotated[Actor, Depends(require("jobs"))]) -> dict[str, Any]:
    if job_name not in scheduler.JOBS:
        raise HTTPException(404, f"No scheduled job {job_name}; jobs: {', '.join(scheduler.JOBS)}")
    return scheduler.run_job(job_name, "manual", requested_by=actor.staff_id, channel=CHANNEL)


# -- alerts -----------------------------------------------------------------
@app.get(
    "/alert-rules",
    response_model=list[AlertRule],
    tags=["alerts"],
    summary="List alert rules",
    description="The configurable rules with their threshold and parameters.",
)
def get_rules() -> list[dict[str, Any]]:
    with closing(db.connect()) as conn:
        return alerts.list_rules(conn)


@app.patch(
    "/alert-rules/{rule_id}",
    response_model=AlertRule,
    tags=["alerts"],
    summary="Change a rule's threshold or switch it on/off",
    description="Updates threshold and/or enabled. The change is audited with before and after values.",
    openapi_extra=roles_doc("rules"),
    responses=WRITE_RESPONSES,
)
def patch_rule(rule_id: str, body: AlertRulePatch, actor: Annotated[Actor, Depends(require("rules"))]) -> dict[str, Any]:
    if body.threshold is None and body.enabled is None:
        raise HTTPException(400, "Send threshold and/or enabled")
    with db.transaction() as conn:
        return alerts.update_rule(
            conn, rule_id, actor.staff_id, threshold=body.threshold, enabled=body.enabled, channel=CHANNEL, reason=body.reason
        )


@app.get("/alerts", response_model=list[Alert], tags=["alerts"], summary="Alert history", description="Newest first. Filter by status.")
def get_alerts(
    status: Literal["open", "acknowledged", "resolved"] | None = Query(None, description="Only alerts with this status"),
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    with closing(db.connect()) as conn:
        return alerts.list_alerts(conn, status, limit)


@app.post(
    "/alerts/{alert_id}/acknowledge",
    response_model=Alert,
    tags=["alerts"],
    summary="Acknowledge an open alert",
    description="Marks an open alert acknowledged by the calling staff member (confirmation by staff ID). 400 when the alert is not open.",
    openapi_extra=roles_doc("acknowledge"),
    responses=WRITE_RESPONSES,
)
def acknowledge_alert(alert_id: int, actor: Annotated[Actor, Depends(require("acknowledge"))]) -> dict[str, Any]:
    with db.transaction() as conn:
        alerts.acknowledge(conn, alert_id, actor.staff_id, channel=CHANNEL)
        row = db.query_one(
            "SELECT a.*, r.name AS rule_name, r.severity FROM alerts a JOIN alert_rules r USING (rule_id) WHERE a.alert_id = ?",
            (alert_id,),
            conn=conn,
        )
        assert row is not None
        return row


@app.post(
    "/alerts/evaluate",
    response_model=EvaluationOut,
    tags=["alerts"],
    summary="Evaluate the alert rules now",
    description="Evaluates every enabled rule: opens new alerts (never a duplicate of an open or acknowledged one) and resolves alerts whose condition cleared. Each alert written is audited (actor system, channel api).",
    openapi_extra=roles_doc("evaluate"),
    responses=WRITE_RESPONSES,
)
def evaluate(actor: Annotated[Actor, Depends(require("evaluate"))]) -> dict[str, Any]:
    with db.transaction() as conn:
        return alerts.evaluate_rules(conn, channel=CHANNEL).as_dict()


# -- fleet ------------------------------------------------------------------
@app.get(
    "/vehicles/positions",
    response_model=list[VehiclePosition],
    tags=["fleet"],
    summary="Last known position per vehicle",
    description="Last report per vehicle with minutes since it and the stale flag (shared.fleet.is_stale against the stale_position_minutes setting).",
)
def get_positions() -> list[dict[str, Any]]:
    with closing(db.connect()) as conn:
        return simulator.vehicle_positions(conn)


@app.post(
    "/positions",
    response_model=PositionOut,
    status_code=201,
    tags=["fleet"],
    summary="Report a vehicle position",
    description="Stores one position report through shared.fleet.record_position with source 'api'. Positions are kilometres on the invented 100 x 100 km grid.",
    openapi_extra=roles_doc("positions"),
    responses=WRITE_RESPONSES,
)
def post_position(body: PositionIn, actor: Annotated[Actor, Depends(require("positions"))]) -> dict[str, Any]:
    with db.transaction() as conn:
        report_id = fleet.record_position(
            conn, body.vehicle_id, body.x_km, body.y_km, source="api", convoy_id=body.convoy_id, speed_kmh=body.speed_kmh
        )
        row = db.query_one("SELECT * FROM position_reports WHERE report_id = ?", (report_id,), conn=conn)
        assert row is not None
        db.audit(
            conn,
            actor_id=actor.staff_id,
            channel=CHANNEL,
            action="create",
            entity_table="position_reports",
            entity_id=str(report_id),
            after=row,
        )
        return row


def simulator_state(conn: Any) -> dict[str, Any]:
    return {
        "enabled": simulator.is_enabled(conn),
        "interval_seconds": simulator.interval_seconds(conn),
        "simulated_vehicle_ids": [r[0] for r in conn.execute("SELECT vehicle_id FROM vehicles WHERE simulated = 1 ORDER BY vehicle_id")],
    }


@app.get(
    "/simulator",
    response_model=SimulatorState,
    tags=["fleet"],
    summary="Position simulator state",
    description="Whether the simulator switch is on, and which vehicles it moves.",
)
def get_simulator() -> dict[str, Any]:
    with closing(db.connect()) as conn:
        return simulator_state(conn)


@app.put(
    "/simulator",
    response_model=SimulatorState,
    tags=["fleet"],
    summary="Start or stop the position simulator",
    description="Sets the position_simulator_enabled setting (audited). When stopped, simulated vehicles stop reporting and go stale after the limit.",
    openapi_extra=roles_doc("simulator"),
    responses=WRITE_RESPONSES,
)
def put_simulator(body: SimulatorSwitch, actor: Annotated[Actor, Depends(require("simulator"))]) -> dict[str, Any]:
    with db.transaction() as conn:
        simulator.set_enabled(conn, body.enabled, actor.staff_id, channel=CHANNEL)
        return simulator_state(conn)


@app.put(
    "/settings/stale_position_minutes",
    response_model=SettingOut,
    tags=["fleet"],
    summary="Set the stale-position limit",
    description="Sets the stale_position_minutes setting used by Projects 14 and 17, and the threshold of every stale_position alert rule with it. Both changes are audited.",
    openapi_extra=roles_doc("settings"),
    responses=WRITE_RESPONSES,
)
def put_stale_limit(body: StaleLimit, actor: Annotated[Actor, Depends(require("settings"))]) -> dict[str, Any]:
    with db.transaction() as conn:
        alerts.set_stale_limit(conn, body.minutes, actor.staff_id, channel=CHANNEL)
        row = db.query_one("SELECT key, value, updated_at, updated_by FROM settings WHERE key = 'stale_position_minutes'", conn=conn)
        assert row is not None
        return row


# -- audit and data ----------------------------------------------------------
@app.get(
    "/audit",
    response_model=list[AuditEntry],
    tags=["audit"],
    summary="Latest audit log entries",
    description="Newest first, with optional filters.",
)
def get_audit(
    limit: int = Query(100, ge=1, le=1000),
    channel: str | None = Query(None, description="e.g. api, app:20, scheduler, seed"),
    entity_table: str | None = Query(None, description="e.g. alerts, alert_rules, settings"),
    actor_id: str | None = Query(None, description="Staff ID or 'system'"),
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM audit_log WHERE 1=1"
    params: list[Any] = []
    for col, val in (("channel", channel), ("entity_table", entity_table), ("actor_id", actor_id)):
        if val:
            sql += f" AND {col} = ?"
            params.append(val)
    sql += " ORDER BY audit_id DESC LIMIT ?"
    params.append(limit)
    with closing(db.connect()) as conn:
        return db.query_all(sql, params, conn=conn)


@app.get(
    "/stock",
    response_model=list[StockRow],
    tags=["data"],
    summary="Stock on hand",
    description="v_stock_on_hand: received minus dispatched per warehouse and item.",
)
def get_stock(
    warehouse_id: str | None = Query(None, examples=["WH-03"]),
    item_id: str | None = Query(None, examples=["ITM-HYG"]),
    below: float | None = Query(None, description="Only rows with on_hand below this"),
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM v_stock_on_hand WHERE 1=1"
    params: list[Any] = []
    if warehouse_id:
        sql += " AND warehouse_id = ?"
        params.append(warehouse_id)
    if item_id:
        sql += " AND item_id = ?"
        params.append(item_id)
    if below is not None:
        sql += " AND on_hand < ?"
        params.append(below)
    with closing(db.connect()) as conn:
        return db.query_all(sql + " ORDER BY warehouse_id, item_id", params, conn=conn)


@app.get(
    "/requests/overdue-callbacks",
    response_model=list[OverdueCallback],
    tags=["data"],
    summary="Overdue callbacks",
    description="Service requests whose callback is past due, not done and not closed (the overdue_callbacks rule counts these).",
)
def get_overdue_callbacks() -> list[dict[str, Any]]:
    with closing(db.connect()) as conn:
        return alerts.overdue_callbacks(conn)


@app.get(
    "/advisories/current",
    response_model=list[Advisory],
    tags=["data"],
    summary="Current movement advisories",
    description="v_current_advisory: the newest active decision per area.",
)
def get_advisories() -> list[dict[str, Any]]:
    with closing(db.connect()) as conn:
        rows = db.query_all("SELECT * FROM v_current_advisory ORDER BY area_id", conn=conn)
    for r in rows:
        r["is_override"] = bool(r["is_override"])
    return rows


# -- sync (Project 08), behind an import guard --------------------------------
sync_router = APIRouter(prefix="/sync", tags=["sync"])
try:
    from shared import sync as sync_module

    SYNC_INSTALLED = True
except ImportError:  # pragma: no cover - only before shared.sync exists
    SYNC_INSTALLED = False


class SyncChangeIn(BaseModel):
    device_id: str = Field(examples=["DEV-01"])
    record_id: str = Field(examples=["INT-0001"], description="The record the device changed (for a create, the device's ID for it)")
    operation: Literal["create", "update"]
    payload: dict[str, Any] = Field(description="Changed fields: head_name, area_id, household_size, primary_need, urgency, notes")
    base_version: int | None = Field(None, description="For an update: the server version the device's copy was edited from")


class SyncApplyOut(BaseModel):
    record_id: str
    operation: str
    outcome: Literal["applied", "conflict"]
    version: int
    conflict_id: str | None = None
    queue_id: int | None = None
    message: str = ""
    renamed_from: str | None = None


class ServerEditIn(BaseModel):
    changes: dict[str, Any] = Field(description="Fields to change on the server copy of the record")


class ResolveIn(BaseModel):
    choice: Literal["keep_device", "keep_server", "field_by_field"]
    reason: str = Field(min_length=1, max_length=500, description="Why this version was chosen (required)")
    field_choices: dict[str, Literal["device", "server"]] | None = Field(None, description="For field_by_field: every differing field")


async def _sync_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _relabel_sync_audit(conn: Any, since_audit_id: int) -> None:
    """apply_change audits with channel 'sync'; inside this API transaction those rows are API changes."""
    conn.execute("UPDATE audit_log SET channel = ? WHERE audit_id > ? AND channel = 'sync'", (CHANNEL, since_audit_id))


def _max_audit_id(conn: Any) -> int:
    return int(conn.execute("SELECT COALESCE(MAX(audit_id), 0) FROM audit_log").fetchone()[0])


if SYNC_INSTALLED:
    app.add_exception_handler(sync_module.SyncError, _sync_error)

    @sync_router.post(
        "/changes",
        response_model=SyncApplyOut,
        summary="Apply one change sent by a field device",
        description="shared.sync.apply_change: a create inserts the record; an update is applied only when base_version equals the server's current version. Otherwise a conflict is opened and the record is left unchanged (never merged silently).",
        openapi_extra=roles_doc("sync"),
        responses=WRITE_RESPONSES,
    )
    def post_sync_change(body: SyncChangeIn, actor: Annotated[Actor, Depends(require("sync"))]) -> dict[str, Any]:
        with db.transaction() as conn:
            since = _max_audit_id(conn)
            result = sync_module.apply_change(
                conn,
                device_id=body.device_id,
                record_id=body.record_id,
                operation=body.operation,
                payload=body.payload,
                base_version=body.base_version,
                actor_id=actor.staff_id,
            )
            _relabel_sync_audit(conn, since)
            return result.to_dict()

    @sync_router.get(
        "/records/{record_id}", summary="Server copy of an intake record", description="The versioned fields and the current version."
    )
    def get_sync_record(record_id: str) -> dict[str, Any]:
        with closing(db.connect()) as conn:
            snap = sync_module.record_snapshot(conn, record_id)
        if snap is None:
            raise HTTPException(404, f"No intake record {record_id}")
        return snap

    @sync_router.put(
        "/records/{record_id}",
        summary="Edit the server copy of a record",
        description="shared.sync.server_update: an office-side edit; it raises the version, so a device holding an older copy gets a conflict.",
        openapi_extra=roles_doc("sync_office"),
        responses=WRITE_RESPONSES,
    )
    def put_sync_record(record_id: str, body: ServerEditIn, actor: Annotated[Actor, Depends(require("sync_office"))]) -> dict[str, Any]:
        with db.transaction() as conn:
            version = sync_module.server_update(conn, record_id, body.changes, actor_id=actor.staff_id, channel=CHANNEL)
            return {"record_id": record_id, "version": version}

    @sync_router.get("/conflicts", summary="Open sync conflicts", description="Conflicts waiting for a staff choice.")
    def get_sync_conflicts() -> list[dict[str, Any]]:
        with closing(db.connect()) as conn:
            return sync_module.open_conflicts(conn)

    @sync_router.get(
        "/conflicts/{conflict_id}",
        summary="One conflict with both versions",
        description="Device and server versions side by side with the differing fields.",
    )
    def get_sync_conflict(conflict_id: str) -> dict[str, Any]:
        with closing(db.connect()) as conn:
            if db.row_dict(conn, "sync_conflicts", "conflict_id", conflict_id) is None:
                raise HTTPException(404, f"No conflict {conflict_id}")
            return sync_module.conflict_detail(conn, conflict_id)

    @sync_router.post(
        "/conflicts/{conflict_id}/resolve",
        summary="Resolve a conflict with a staff choice",
        description="shared.sync.resolve_conflict: keep the device version, keep the server version, or choose field by field, with a reason. The caller's staff ID is the confirmation; it is refused if the server record changed again.",
        openapi_extra=roles_doc("sync_office"),
        responses=WRITE_RESPONSES,
    )
    def resolve_sync_conflict(
        conflict_id: str, body: ResolveIn, actor: Annotated[Actor, Depends(require("sync_office"))]
    ) -> dict[str, Any]:
        with db.transaction() as conn:
            return sync_module.resolve_conflict(
                conn,
                conflict_id,
                choice=body.choice,
                confirmed_by=actor.staff_id,
                reason=body.reason,
                field_choices=dict(body.field_choices) if body.field_choices else None,
                channel=CHANNEL,
            )

    @sync_router.get(
        "/devices/{device_id}/status", summary="A device's queue status", description="Online flag, last sync time and queue counts."
    )
    def get_sync_device(device_id: str) -> dict[str, Any]:
        with closing(db.connect()) as conn:
            return sync_module.queue_status(conn, device_id)

else:  # pragma: no cover

    def sync_not_installed(rest: str) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "sync not installed"})

    for _method in ("GET", "POST", "PUT"):
        sync_router.add_api_route(
            "/{rest:path}",
            sync_not_installed,
            methods=[_method],
            operation_id=f"sync_not_installed_{_method.lower()}",
            summary="Offline field sync (not installed)",
            description="Placeholder until shared.sync is installed: every /sync request returns 503 'sync not installed'.",
        )

app.include_router(sync_router)


# -- offline reference page ---------------------------------------------------
def reference_html(spec: dict[str, Any]) -> str:
    """Plain HTML reference built from the OpenAPI document. No external assets."""
    e = html.escape
    rows = []
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            roles = op.get("x-required-roles")
            params = []
            for p in op.get("parameters", []):
                if p.get("name", "").lower() == "authorization":
                    continue
                req = " (required)" if p.get("required") else ""
                params.append(f"<li><code>{e(p['name'])}</code> in {e(p.get('in', ''))}{req}: {e(p.get('description', ''))}</li>")
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema", {})
            if "$ref" in body:
                name = body["$ref"].rsplit("/", 1)[-1]
                fields = spec.get("components", {}).get("schemas", {}).get(name, {}).get("properties", {})
                params.append(f"<li>JSON body <code>{e(name)}</code>: {e(', '.join(fields))}</li>")
            role_text = ", ".join(roles) if roles else "anyone (read only)"
            rows.append(
                f"<tr><td><span class='m m-{e(method)}'>{e(method.upper())}</span></td><td><code>{e(path)}</code></td>"
                f"<td><strong>{e(op.get('summary', ''))}</strong><br>{e(op.get('description', ''))}"
                f"{'<ul>' + ''.join(params) + '</ul>' if params else ''}</td><td>{e(role_text)}</td></tr>"
            )
    roles_rows = "".join(f"<tr><td><code>{e(k)}</code></td><td>{e(', '.join(v))}</td></tr>" for k, v in ROLES.items())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{e(spec["info"]["title"])}: reference</title>
<style>
body {{ font-family: "Segoe UI", Tahoma, Arial, sans-serif; margin: 2rem; color: #1d2b2b; max-width: 1200px; }}
h1 {{ color: #0B6E69; }} table {{ border-collapse: collapse; width: 100%; margin-bottom: 2rem; }}
td, th {{ border: 1px solid #c8d3d3; padding: 0.45rem 0.6rem; vertical-align: top; text-align: left; }}
th {{ background: #E3F4EC; }} code {{ background: #f2f5f5; padding: 0 0.2rem; }}
.m {{ font-weight: 700; padding: 0.1rem 0.4rem; border-radius: 4px; color: #fff; background: #5f6b6b; }}
.m-get {{ background: #0B6E69; }} .m-post {{ background: #B35C00; }} .m-put, .m-patch {{ background: #6B3E00; }}
.banner {{ background: #FFF4E0; border: 2px solid #B35C00; padding: 0.5rem 0.8rem; border-radius: 6px; }}
</style></head><body>
<p class="banner"><strong>DEMO</strong>: fictional data only. This page works offline.</p>
<h1>{e(spec["info"]["title"])} <small>v{e(spec["info"]["version"])}</small></h1>
<p>Read endpoints are open. Writes need the header <code>X-Staff-Id</code> (a seeded staff ID such as CRD-01);
401 without a valid staff ID, 403 for a role that is not allowed. In server mode writes also need
<code>Authorization: Bearer &lt;PORTFOLIO_API_TOKEN&gt;</code>. Every change is written to the audit log with channel <code>api</code>.
Machine-readable spec: <a href="/openapi.json">/openapi.json</a>. Interactive docs (need internet for their scripts): <a href="/docs">/docs</a>.</p>
<h2>Roles per write action</h2><table><tr><th>Action</th><th>Allowed roles</th></tr>{roles_rows}</table>
<h2>Endpoints</h2><table><tr><th>Method</th><th>Path</th><th>What it does</th><th>Required role</th></tr>{"".join(rows)}</table>
</body></html>"""


@app.get(
    "/reference",
    response_class=HTMLResponse,
    tags=["docs"],
    summary="Offline API reference",
    description="Every path, method, summary, parameter and required role, as plain HTML with no external assets.",
)
def get_reference() -> str:
    return reference_html(app.openapi())


def main() -> None:
    import uvicorn

    host = os.environ.get("PORTFOLIO_BIND_ADDRESS", "localhost").strip() or "localhost"
    uvicorn.run(app, host=host, port=ports.API_PORT)


if __name__ == "__main__":
    main()
