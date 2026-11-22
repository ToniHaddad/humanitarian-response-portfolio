"""Scheduled jobs, their run history, heartbeats and the health report (Project 20).

The four jobs are rows in `scheduled_jobs`:

  pipeline_ingest     shared.pipeline.run_pipeline (Project 07), behind an import guard
  alert_evaluation    shared.alerts.evaluate_rules
  position_simulator  shared.simulator.step
  db_backup           SQLite backup API copy into PORTFOLIO_BACKUP_DIR (default data/backups)

run_job() records every run in `job_runs` (running, then success or failed with a
readable detail) and never lets an exception escape. build_scheduler() returns an
APScheduler scheduler with one interval job per enabled row; `python -m shared.scheduler`
runs it in the foreground. health_report() is the one definition the Streamlit page
and the API share.

Locally nothing runs in the background: the app's "Run now" buttons call run_job().
"""

from __future__ import annotations

import os
import sqlite3
import sys
import traceback
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from shared import alerts, clock, config, db, simulator

BACKUP_ENV_VAR = "PORTFOLIO_BACKUP_DIR"
BACKUPS_TO_KEEP = 5
OVERDUE_FACTOR = 2.0  # a job is overdue with no success within 2 x its interval
SERVICE_SILENT_MINUTES = 5.0  # a service not seen for this long is flagged
HEARTBEAT_SECONDS = 30


# -- the jobs ------------------------------------------------------------
# Each job returns (succeeded, detail). A reported failure still commits what the job
# wrote (for example the pipeline's own failed run record); an exception rolls back.
def _job_pipeline(conn: sqlite3.Connection, trigger: str, channel: str) -> tuple[bool, str]:
    """shared.pipeline.run_pipeline (Project 07). Failed when it is missing, raises, or reports a failed file."""
    try:
        from shared.pipeline import run_pipeline
    except ImportError:
        return False, "pipeline not installed"
    summary = run_pipeline(conn, trigger=trigger)
    status = str(getattr(summary, "status", "success"))
    detail = str(getattr(summary, "detail", "") or status)
    if status == "failed":
        return False, f"pipeline run failed: {detail}"
    return True, f"{status}: {detail}"


def _job_alerts(conn: sqlite3.Connection, trigger: str, channel: str) -> tuple[bool, str]:
    return True, alerts.evaluate_rules(conn, channel=channel).summary()


def _job_simulator(conn: sqlite3.Connection, trigger: str, channel: str) -> tuple[bool, str]:
    if not simulator.is_enabled(conn):
        beat(conn, "position_simulator", "degraded", "stopped")
        return True, "simulator switched off: no positions written"
    n = simulator.step(conn)
    beat(conn, "position_simulator", "ok", f"{n} vehicles reported")
    return True, f"{n} vehicles reported"


def backup_dir() -> Path:
    override = os.environ.get(BACKUP_ENV_VAR, "").strip()
    return Path(override) if override else config.DATA_DIR / "backups"


def backup_database(source: Path | None = None, target_dir: Path | None = None, keep: int = BACKUPS_TO_KEEP) -> Path:
    """Copy the database with SQLite's online backup API; keep the newest `keep` copies of this database."""
    source = Path(source) if source else config.db_path()
    target_dir = Path(target_dir) if target_dir else backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{source.stem}-{clock.now():%Y%m%d-%H%M%S}.db"
    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    copies = sorted(target_dir.glob(f"{source.stem}-*.db"))
    for old in copies[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    return target


def _job_backup(conn: sqlite3.Connection, trigger: str, channel: str) -> tuple[bool, str]:
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    target = backup_database(Path(path) if path else None)
    return True, f"backup written: {target.name} (keeping the last {BACKUPS_TO_KEEP})"


JobFunc = Callable[[sqlite3.Connection, str, str], tuple[bool, str]]
JOBS: dict[str, JobFunc] = {
    "pipeline_ingest": _job_pipeline,
    "alert_evaluation": _job_alerts,
    "position_simulator": _job_simulator,
    "db_backup": _job_backup,
}


def pipeline_installed() -> bool:
    try:
        from shared.pipeline import run_pipeline  # noqa: F401
    except ImportError:
        return False
    return True


# -- running ---------------------------------------------------------------
def run_job(
    job_name: str,
    trigger: str = "manual",
    path: Path | str | None = None,
    requested_by: str | None = None,
    channel: str = "scheduler",
) -> dict[str, Any]:
    """Run one job now and record it in job_runs. Never raises; returns the finished run.

    requested_by: staff ID that asked for a manual run (audited with `channel`).
    """
    if trigger not in ("manual", "scheduled"):
        trigger = "manual"
    try:
        with db.transaction(path) as conn:
            if conn.execute("SELECT 1 FROM scheduled_jobs WHERE job_name = ?", (job_name,)).fetchone() is None or job_name not in JOBS:
                return {"job_run_id": None, "job_name": job_name, "status": "failed", "detail": f"unknown job {job_name}"}
            cur = conn.execute(
                "INSERT INTO job_runs(job_name, started_at, status, trigger) VALUES (?,?, 'running', ?)",
                (job_name, clock.iso(), trigger),
            )
            run_id = int(cur.lastrowid)
    except Exception as exc:  # noqa: BLE001 - the database itself is unusable
        return {"job_run_id": None, "job_name": job_name, "status": "failed", "detail": f"could not record the run: {exc}"}

    status, detail = "success", ""
    try:
        with db.transaction(path) as conn:
            ok, detail = JOBS[job_name](conn, trigger, channel)
        status = "success" if ok else "failed"
    except Exception as exc:  # noqa: BLE001 - a job must never crash the scheduler or the app
        status = "failed"
        detail = f"{type(exc).__name__}: {exc}"
        traceback.print_exc(file=sys.stderr)

    finished = clock.iso()
    try:
        with db.transaction(path) as conn:
            conn.execute(
                "UPDATE job_runs SET status = ?, finished_at = ?, detail = ? WHERE job_run_id = ?",
                (status, finished, detail[:500], run_id),
            )
            if requested_by:
                db.audit(
                    conn,
                    actor_id=requested_by,
                    channel=channel,
                    action="run_job",
                    entity_table="job_runs",
                    entity_id=str(run_id),
                    after={"job_name": job_name, "status": status, "detail": detail[:500]},
                )
    except Exception as exc:  # noqa: BLE001
        detail = f"{detail} (could not record the result: {exc})"
    return {"job_run_id": run_id, "job_name": job_name, "trigger": trigger, "status": status, "detail": detail, "finished_at": finished}


def beat(conn: sqlite3.Connection, service: str, status: str = "ok", detail: str | None = None) -> None:
    """Record that a service is alive (upsert into service_heartbeats)."""
    conn.execute(
        """INSERT INTO service_heartbeats(service_name, last_seen_at, status, detail) VALUES (?,?,?,?)
           ON CONFLICT(service_name) DO UPDATE SET last_seen_at = excluded.last_seen_at, status = excluded.status,
           detail = COALESCE(excluded.detail, service_heartbeats.detail)""",
        (service, clock.iso(), status, detail),
    )


def heartbeat(path: Path | str | None = None) -> None:
    """Scheduler heartbeat: itself, and the simulator's state from its switch."""
    with db.transaction(path) as conn:
        beat(conn, "scheduler", "ok", "Pipeline, alerts, simulator, backups")
        if simulator.is_enabled(conn):
            beat(conn, "position_simulator", "ok", "running")
        else:
            beat(conn, "position_simulator", "degraded", "stopped")


# -- health ----------------------------------------------------------------
def _minutes(since: str | None, now: datetime) -> float | None:
    if not since:
        return None
    return round((now - datetime.fromisoformat(since)).total_seconds() / 60.0, 1)


def health_report(conn: sqlite3.Connection, now: datetime | None = None) -> dict[str, Any]:
    """Every service and every scheduled job, with last successful run and overdue flags."""
    now = now or clock.now()
    services = []
    for s in db.query_all("SELECT * FROM service_heartbeats ORDER BY service_name", conn=conn):
        age = _minutes(s["last_seen_at"], now)
        services.append({**s, "minutes_since_seen": age, "silent": age is None or age > SERVICE_SILENT_MINUTES})
    jobs = []
    for j in db.query_all("SELECT * FROM scheduled_jobs ORDER BY job_name", conn=conn):
        last_ok = conn.execute(
            "SELECT MAX(finished_at) FROM job_runs WHERE job_name = ? AND status = 'success'", (j["job_name"],)
        ).fetchone()[0]
        last = conn.execute(
            "SELECT status, started_at, finished_at, detail, trigger FROM job_runs WHERE job_name = ? ORDER BY started_at DESC, job_run_id DESC LIMIT 1",
            (j["job_name"],),
        ).fetchone()
        overdue = bool(j["enabled"]) and (
            last_ok is None or now - datetime.fromisoformat(last_ok) > timedelta(seconds=OVERDUE_FACTOR * j["interval_seconds"])
        )
        jobs.append(
            {
                "job_name": j["job_name"],
                "description": j["description"],
                "interval_seconds": j["interval_seconds"],
                "enabled": bool(j["enabled"]),
                "last_success_at": last_ok,
                "minutes_since_success": _minutes(last_ok, now),
                "last_run_at": last["started_at"] if last else None,
                "last_run_status": last["status"] if last else None,
                "last_run_trigger": last["trigger"] if last else None,
                "last_run_detail": last["detail"] if last else None,
                "overdue": overdue,
            }
        )
    healthy = not any(j["overdue"] or j["last_run_status"] == "failed" for j in jobs) and not any(
        s["silent"] or s["status"] != "ok" for s in services
    )
    return {"generated_at": clock.iso(now), "healthy": healthy, "services": services, "jobs": jobs}


# -- APScheduler -------------------------------------------------------------
def build_scheduler(path: Path | str | None = None, blocking: bool = False) -> Any:
    """An APScheduler scheduler (not started) with one interval job per enabled scheduled job, plus a heartbeat."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.schedulers.blocking import BlockingScheduler

    sched = BlockingScheduler() if blocking else BackgroundScheduler()
    with db.transaction(path) as conn:
        rows = db.query_all("SELECT job_name, interval_seconds FROM scheduled_jobs WHERE enabled = 1 ORDER BY job_name", conn=conn)
    for r in rows:
        if r["job_name"] not in JOBS:
            continue
        sched.add_job(
            run_job,
            "interval",
            seconds=int(r["interval_seconds"]),
            id=r["job_name"],
            name=r["job_name"],
            kwargs={"job_name": r["job_name"], "trigger": "scheduled", "path": path},
            max_instances=1,
            coalesce=True,
        )
    sched.add_job(heartbeat, "interval", seconds=HEARTBEAT_SECONDS, id="_heartbeat", name="heartbeat", kwargs={"path": path})
    return sched


def main() -> int:
    """Run the scheduler in the foreground until Ctrl+C."""
    target = config.db_path()
    if not db.database_exists(target):
        print(f"No database at {target}. Run: python -m shared.seed --reset", file=sys.stderr)
        return 1
    sched = build_scheduler(blocking=True)
    heartbeat()
    jobs = ", ".join(f"{j.id} every {int(j.trigger.interval.total_seconds())} s" for j in sched.get_jobs() if j.id != "_heartbeat")
    print(f"Scheduler running against {target}: {jobs}. Stop with Ctrl+C.", flush=True)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        with db.transaction() as conn:
            beat(conn, "scheduler", "down", "stopped")
            beat(conn, "position_simulator", "down", "scheduler stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
