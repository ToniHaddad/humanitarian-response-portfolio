# 20 · Operations API and alerts

## Problem
An operations team runs many small tools over one database. Someone has to see who changed what, be told when stock runs low, callbacks pile up, a vehicle stops reporting or a data load fails, and check that the background jobs are still running. Other systems (vehicle trackers, field devices) need a documented way to send data in, with roles, so that not everyone can change everything.

## Intended user
Systems administrators and integrators; coordinators and duty officers who acknowledge alerts.

## Technique
Rule-based scoring (alert rules with thresholds) and straightforward calculation, plus a documented REST API (FastAPI), a job scheduler (APScheduler) and a position simulator. No machine learning.

The platform logic is in shared modules so Projects 14, 17 and server mode use the same code:

| Module | What it does |
|---|---|
| `shared/alerts.py` | Evaluates the four rule types, writes and resolves alerts, acknowledgement, rule changes, the stale limit. |
| `shared/scheduler.py` | The four jobs, `run_job` ("run now"), backups, heartbeats, `health_report`, the APScheduler setup (`python -m shared.scheduler`). |
| `shared/simulator.py` | The position simulator switch and one simulator step; last position and stale flag per vehicle. |
| `shared/auth.py` | The role table, and the server-mode staff login and API token checks. |
| `shared/api.py` | The REST API (`python -m shared.api`). |

The alert rules (table `alert_rules`, threshold editable):

| Rule | Fires when | At the threshold |
|---|---|---|
| `RULE-STOCK` stock_below | on hand < threshold, one alert per warehouse and item | does not fire |
| `RULE-CALLBACK` overdue_callbacks | overdue callbacks >= threshold (overdue: due time passed, callback not done, request not closed) | fires |
| `RULE-STALE` stale_position | minutes since a vehicle's last report > threshold, one alert per vehicle (`shared.fleet.is_stale`) | does not fire |
| `RULE-PIPELINE` pipeline_failed | failed `pipeline_ingest` job runs in the last `window_hours` (24) >= threshold | fires |

An evaluation never opens a second alert while one is open or acknowledged for the same rule and entity (for example `VEH-07`). When the condition clears, the open or acknowledged alert is marked resolved. Disabled rules are skipped. Every alert written is audited (actor `system`).

## Inputs
The shared SQLite database: `alert_rules`, `alerts`, `scheduled_jobs`, `job_runs`, `service_heartbeats`, `audit_log`, `settings`, `v_stock_on_hand`, `service_requests`, `vehicles`, `position_reports` (`v_last_position`), `convoys`, `convoy_stops`, `v_current_advisory`; the Project 07 sample exports through `shared.pipeline`; intake records and sync tables through `shared.sync`. Libraries already in `requirements.txt`: FastAPI, uvicorn, APScheduler, httpx (tests only).

## Assumptions
- Roles come from the `staff` table. Write actions and allowed roles (`shared.auth.ROLES`):

  | Action | Allowed roles |
  |---|---|
  | Report a position (`POST /positions`) | field_worker, coordinator, admin |
  | Acknowledge an alert | coordinator, duty_officer, warehouse_officer, admin |
  | Evaluate alert rules now | coordinator, duty_officer, admin |
  | Change an alert rule, settings (stale limit), simulator switch | coordinator, admin |
  | Run a job now | coordinator, admin |
  | Sync: send a device change | field_worker, coordinator, admin |
  | Sync: server edit, resolve a conflict | caseworker, coordinator, admin |

- The stale-position limit is one number: changing it here sets the `stale_position_minutes` setting (read by 14 and 17) and the `RULE-STALE` threshold together.
- A job is overdue when it has no successful run within 2 x its interval. A service is "silent" when its heartbeat is older than 5 minutes.
- The simulator moves each simulated vehicle at 40 km/h toward its next convoy stop when its convoy is departed, otherwise drifts within 0.5 km of its home warehouse. `VEH-07` is not simulated, so it goes stale. Simulator reports are not audited one by one (they are data, like seed reports); the job run records each step.
- Backups use SQLite's online backup API into `PORTFOLIO_BACKUP_DIR` (default `data/backups`), keeping the last 5 copies of each database file.

## Limitations
- Staff confirmation (a form with a tick box, checked again in the function the page calls) covers acknowledging an alert, saving a rule and stopping or starting the simulator. **Run now** for a job and **Evaluate alert rules now** stay plain buttons, limited by role and audited; they are repeatable and change nothing a person cannot rerun. The API and the scheduler call the shared functions directly, as before, with role checks only.
- The scheduler runs only while it is started (`python run.py` starts it; `python run.py 20 --no-services` or a plain `streamlit run` does not). Without it, jobs run only when someone presses **Run now**: right after a reset the simulated vehicles go stale after 15 minutes until the simulator job runs, and `pipeline_ingest` shows as overdue until it runs.
- The scheduler checks the homepage and the API by fetching their local health URLs every 30 seconds (host from `PORTFOLIO_PROBE_HOST`, default `localhost`). A Streamlit project app has no heartbeat of its own, so the health page lists the homepage, the API, the scheduler and the simulator, not the 20 apps one by one.
- `python run.py` stops its processes with a hard terminate on Windows, so the scheduler cannot write its final "down" heartbeat; the health page then shows it as silent after 5 minutes instead.
- `/docs` (Swagger UI) and `/redoc` load their JavaScript and styles from a public CDN in the browser, so they do not work offline. `/reference` is a self-contained HTML page generated from the OpenAPI document (every path, method, parameter and required role) that works with no network; `/openapi.json` always works.
- Local demo mode trusts the `X-Staff-Id` header (anyone can claim a staff ID). That is acceptable for a demo; server mode adds a login for every write (the staff login as HTTP Basic, or the API token as Bearer). There is one shared staff login and one shared API token, not one per user, so the role still comes from `X-Staff-Id`.
- Two apps writing at the very same moment wait for each other (SQLite write lock); fine at demo scale.
- An alert's observed value is the value when it fired; it is not updated while the alert stays open.
- Right after a reset the health report says NEEDS ATTENTION (and the API's `/health` says `"healthy": false`): the last seeded successful `pipeline_ingest` run is from the previous morning, more than 2 x its 3600 s interval ago, and the scheduler runs the pipeline only at its first hourly tick (or on **Run now**). That is how `shared.scheduler.health_report` defines overdue, so the report clears after one pipeline run.
- The settings audit row is written by `db.set_setting` with channel `settings` and relabelled to the real channel (`app:20` or `api`) inside the same transaction; sync changes through the API are relabelled from `sync` to `api` the same way.

## Launch
```
python run.py                     # the full local demo: homepage, every built app, the API and the scheduler
python run.py 20                  # this page (port 8520) with the API (8600) and the scheduler
python run.py 20 --no-services    # this page only
streamlit run projects/20-operations-api-and-alerts/app.py --server.port 8520
```
Port 8520. The page works whether or not the API is running; the API tab shows whether it is reachable.

`python run.py` (and `python run.py 20`) starts the two background services beside the apps, and stops them with Ctrl+C:

- the API, `python -m shared.api` (or `uvicorn shared.api:app --port 8600`), on port 8600. Docs: `http://localhost:8600/docs` (Swagger UI), `http://localhost:8600/reference` (offline), `http://localhost:8600/openapi.json`.
- the scheduler, `python -m shared.scheduler`: runs the position simulator and the alert rules once at start, then every job on its interval (simulator 60 s, alert rules 300 s, data pipeline `shared.pipeline.run_pipeline` 3600 s, backup daily), records every run in `job_runs`, and writes the heartbeats every 30 s. Each run is its own short `db.transaction()`; nothing is held open between ticks.

Either can also be started on its own with the commands above.

Example API calls:
```
curl http://localhost:8600/health
curl -X POST http://localhost:8600/alerts/evaluate -H "X-Staff-Id: CRD-01"
curl -X POST http://localhost:8600/positions -H "X-Staff-Id: FW-01" -H "Content-Type: application/json" -d "{\"vehicle_id\": \"VEH-07\", \"x_km\": 41.5, \"y_km\": 52}"
```

Endpoints: `GET /health`, `GET /jobs`, `POST /jobs/{job_name}/run`, `GET /alert-rules`, `PATCH /alert-rules/{rule_id}`, `GET /alerts`, `POST /alerts/{alert_id}/acknowledge`, `POST /alerts/evaluate`, `GET /vehicles/positions`, `POST /positions`, `GET /simulator`, `PUT /simulator`, `PUT /settings/stale_position_minutes`, `GET /audit`, `GET /stock`, `GET /requests/overdue-callbacks`, `GET /advisories/current`, the sync endpoints `POST /sync/changes`, `GET|PUT /sync/records/{record_id}`, `GET /sync/conflicts`, `GET /sync/conflicts/{conflict_id}`, `POST /sync/conflicts/{conflict_id}/resolve`, `GET /sync/devices/{device_id}/status`, and `GET /reference`. 401 without a valid staff ID, 403 for a role that is not allowed. Every change writes `audit_log` with channel `api`.

### Server mode
Set in `.env` (never committed; `.env.example` has placeholders):

| Variable | Use |
|---|---|
| `PORTFOLIO_MODE=server` | switches on the login checks (local demo mode takes the role from the sidebar or the `X-Staff-Id` header alone) |
| `PORTFOLIO_STAFF_USERNAME`, `PORTFOLIO_STAFF_PASSWORD` | staff login: the form shown by `shared.auth.require_staff_login()` on this page, and `Authorization: Basic` for API writes |
| `PORTFOLIO_API_TOKEN` | alternative login for API writes from other systems: `Authorization: Bearer <token>` |
| `PORTFOLIO_PROBE_HOST` | host the scheduler probes for the homepage and API heartbeats (default `localhost`) |
| `PORTFOLIO_BIND_ADDRESS` | address the API listens on (default `localhost`) |
| `PORTFOLIO_BACKUP_DIR` | where `db_backup` writes |
| `PORTFOLIO_DB_PATH` | the database file |

A missing value, or one still set to `change-me`, refuses every login and every token.

## Screenshot
![Operations API and alerts](screenshot.png)

The Alerts tab as an administrator sees it on a freshly seeded database: the alert rules, the alert history filtered to the active alerts, and the acknowledgement form with its staff confirmation. The figures in the metric row at the top (overdue jobs, silent services) depend on how long after the seed and with which services running the page is opened; in this capture a couple of hours had passed and no scheduler was running.

## Try this demo
Steps 1 to 8 use the page alone. Rebuild the database (`python run.py --reset`), then start `python run.py 20 --no-services` (with the services running, the scheduler evaluates the rules at start, so the counts in steps 2 and 3 are already taken). Counts that depend on the clock (overdue callbacks, which differs from seed to seed, and the age of every report) are not quoted; the stock levels, alert numbers and rule thresholds below are fixed by the seed's content. Do steps 1 to 8 within about 10 minutes of the reset: after that the simulated vehicles have also gone stale (and get stale alerts in step 2, which step 6 clears).

1. Open the page and choose **CRD-01** in the sidebar. In the Alerts tab, "Alert history" with the filter "Active (open and acknowledged)" shows 3 alerts: open #4 **VEH-07** stale position (observed 20 minutes, threshold 15), open #3 **WH-03/ITM-HYG** stock alert (30 on hand, threshold 50), and the acknowledged overdue-callbacks alert #2 (observed 6, threshold 5). Note the metric row: 2 open, 1 acknowledged, and 1 job overdue (`pipeline_ingest`, see Limitations).
2. Press **Evaluate alert rules now**. Expected: "4 rules evaluated, 4 new alerts, 0 resolved, 3 already open or acknowledged": the 3 from step 1 are not duplicated, including the acknowledged callbacks alert (the number of overdue callbacks is still at or above its threshold of 5, whatever day it is). The 4 new alerts are stock alerts for WH-01/ITM-FP-SML (48), WH-02/ITM-MAT (40), WH-03/ITM-MAT (30) and WH-03/ITM-MED (20). WH-02/ITM-MED has exactly 50 on hand and does not fire.
3. Press **Evaluate alert rules now** again. Expected: "4 rules evaluated, 0 new alerts, 0 resolved, 7 already open or acknowledged": no duplicates.
4. Under "Acknowledge an alert" pick **#3 · WH-03/ITM-HYG**, choose confirming staff **CRD-01** and press **Acknowledge alert** without ticking the box: "Tick the confirmation box first. Nothing was changed." Tick the box and press again. Expected: "Alert #3 acknowledged by CRD-01." The Audit log tab shows the newest row: CRD-01, coordinator, channel `app:20`, action `acknowledge`, entity table alerts, entity 3.
5. Open "Change a rule's threshold or switch it off", pick **RULE-STOCK**, set the threshold to **51**, press **Save rule**, choose confirming staff **CRD-01**, tick the confirmation box and press **Save rule** in the confirmation form (without the tick box nothing is saved), then **Evaluate alert rules now**. Expected: "RULE-STOCK saved by CRD-01: threshold 51, enabled", then "1 new alerts ... 7 already open or acknowledged": alert #9 for **WH-02/ITM-MED**, observed 50, threshold 51 (the same 50 units that did not fire at threshold 50).
6. Open **Jobs and simulator** and press **Run now** for `position_simulator`. Expected: "position_simulator run #N: success, 7 vehicles reported" (N is the next job run number). The positions table shows source `simulator` for the 7 simulated vehicles, none of them stale, and **VEH-07** with source `seed`, stale (it is not simulated).
7. Press **Stop the simulator**, tick the confirmation box (confirming staff CRD-01) and press **Stop the simulator** in the form: "Position simulator stopped by CRD-01." Then **Run now** for `position_simulator`: "success, simulator switched off: no positions written"; the Health tab lists `position_simulator` as degraded, "stopped". From now on no positions arrive, so every vehicle turns stale once its last report is older than the 15-minute limit (Project 17 shows this on the board). Start it again the same way (**Start the simulator**, tick box, confirm) to resume.
8. Press **Run now** for `pipeline_ingest`, then open **Health**. Expected: "pipeline_ingest run #N: success, partial: 3 file(s): ..." with 11 quarantined, 0 failed and loaded plus duplicate adding up to 37 (the split between loaded and duplicate differs by seed). Health lists 4 services (api, homepage, position_simulator, scheduler) and all 4 jobs (alert_evaluation, db_backup, pipeline_ingest, position_simulator), each with its last successful run; for alert_evaluation, pipeline_ingest and position_simulator that is the time of the steps above, and "Jobs overdue" in the metric row is now 0.
9. The full demo: stop the page and run `python run.py 20` (or `python run.py` for the whole portfolio). Expected in the console: the API on port 8600, then "position_simulator: success, 7 vehicles reported" and "alert_evaluation: success, ..." from the scheduler. `http://localhost:8600/docs` opens the API documentation. Through the API, `curl -X PATCH http://localhost:8600/alert-rules/RULE-STOCK -H "X-Staff-Id: CRD-01" -H "Content-Type: application/json" -d "{\"threshold\": 45, \"reason\": \"gate check\"}"` returns the rule with threshold 45, and `curl "http://localhost:8600/audit?channel=api&limit=1"` returns the audit row: CRD-01, coordinator, channel `api`, update, alert_rules, RULE-STOCK, reason "gate check", after threshold 45 (before is the threshold last saved, 51 if you did step 5, otherwise 50). With `FW-01` (field worker) the same PATCH returns 403, and with no `X-Staff-Id` header 401. The Health tab shows the api and scheduler services ok and refreshed every 30 s.

## Tests
`tests/test_p20_operations_api.py`: every rule type at, below and above its threshold (stock on hand 49/50/51, overdue counts threshold-1/threshold/threshold+1, positions 14/15/16 minutes old, failed pipeline runs 1/2/3 against threshold 2 with one outside the window); `shared.fleet.is_stale` at the limit; the overdue-callback definition; no duplicate alerts on re-evaluation (open or acknowledged), auto-resolve with `resolved_at`, a new alert after re-crossing, disabled rules skipped; every alert write audited; acknowledgement needs a valid staff ID with an allowed role, is audited, and only works on open alerts; rule updates and the stale limit audited; the simulator writes `simulator` positions only when switched on, stays on the grid, and never moves VEH-07; `run_job` success, "pipeline not installed", a forced failure (rolled back, never raised) and the live pipeline; with the simulator stopped no positions arrive and a vehicle is not stale exactly at the limit but stale one minute later (Project 17's walkthrough); the scheduler heartbeat records the homepage and API probes; backups keep the last 5; `health_report` lists every job and service and flags overdue jobs; `build_scheduler` registers one job per enabled row (not started); auth refuses missing and `change-me` values; the API: `/docs`, `/openapi.json` and `/reference` load, a change through the API appears in the audit log with channel `api`, positions through `record_position`, 401/403 role checks, the server-mode login (bearer token or HTTP Basic staff login from the environment; local mode needs only the role header), an alert firing when the threshold is crossed, health/jobs/simulator/settings endpoints, the sync endpoints (conflict detected, never resolved silently, resolved by a caseworker with a reason); every API write endpoint (rule change, acknowledge, job run, simulator switch, stale limit, position) leaving an audit row on channel `api`; the Health tab listing every service and job; `app_test(20)` with no exception, Evaluate and Run now, and acknowledging through the confirmation form.
