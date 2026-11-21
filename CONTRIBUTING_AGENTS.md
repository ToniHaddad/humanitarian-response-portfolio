# Rules for project agents

This repository holds 20 Streamlit demo apps over one shared SQLite database. Many agents build projects at the same time, so these rules keep everyone out of each other's way. Read this whole file before writing code.

## 1. What you may edit

- **Only your own project folder** `projects/NN-<slug>/` and **your own test file** `tests/test_pNN_<name>.py`.
- Never edit `shared/`, `tests/conftest.py`, other test files, `home.py`, `run.py`, `scripts/`, the root README, `PROGRESS.md`, `DECISIONS.md`, `requirements*.txt`, or another project's folder.
- **Schema, seed or shared-helper changes are requested from your group director, never made by you.** Describe the change and why; the director makes it, reseeds and tells everyone.
- Need a library not in `requirements.txt`? Ask the director. Do not `pip install` into the shared `.venv` yourself.
- Do not commit. The director reviews and commits one project at a time.
- Stop every Streamlit or other server you start before you finish.

## 2. Data rules (strict)

- Fictional data only: household IDs (`HH-00042`), fictional areas (`AR-01` Cedarbrook ... `AR-12` Lantern Bay), fictional sites, providers, vehicles (`VEH-01`, fleet numbers, not plates), volunteers (`VOL-001`), staff role IDs (`OPR-01`, `CW-02`, `CRD-01`, `DO-01`, ...).
- Nothing shaped like a phone number, email address, IP address or real hostname, in code, sample files or docs. `tests/test_foundation_data_safety.py` scans the database and every file under `projects/*/sample_data/`.
- No real organization, donor, partner or person names. The portfolio brand is **Waypoint Response Lab** (`shared.ui.BRAND`).
- Map positions are kilometres on an invented 100 x 100 grid (`x_km`, `y_km`). Never use latitude and longitude or map tiles.

## 3. Database

- One SQLite file. **Every path is resolved by `shared.config.db_path()`, from the env var `PORTFOLIO_DB_PATH`** (default `data/portfolio.db`).
- **Use your own copy** for running your app and walkthroughs, so you never disturb other agents:

  ```powershell
  $env:PORTFOLIO_DB_PATH = "C:\dev\humanitarian-response-ai-portfolio\data\agentNN.db"
  .\.venv\Scripts\python.exe -m shared.seed --reset          # builds your copy
  ```

  Never reset `data/portfolio.db` while other agents may be using it.
- Connect with `shared.db`: `db.connect()`, `db.transaction()` (commits or rolls back), `db.query_df(sql, params)`, `db.query_all`, `db.query_one`, `db.scalar`, `db.next_id(conn, table, column, "PREFIX-", width)`, `db.get_setting(key)`, `db.set_setting(...)`.
- **Audit every change**: call `db.audit(conn, actor_id=..., channel="app:NN", action=..., entity_table=..., entity_id=..., before=..., after=..., reason=...)` in the same transaction as the change.
- Time comes from `shared.clock` (`clock.now()`, `clock.today()`, `clock.iso()`), never `datetime.now()` directly, so tests can freeze it.
- Walkthrough IDs are in the setting `demo.scenarios` (JSON): `json.loads(db.get_setting("demo.scenarios"))["p12"]`. Use these instead of hard-coding IDs, because seeded dates move with the seed's anchor time.

### Schema overview (`shared/schema.sql`, the only place tables are defined)

Reference
- `areas`: 12 fictional areas with Arabic names and grid centroids.
- `sites`: distribution points, community centres and a clinic, each in an area.
- `warehouses`: three depots on the grid.
- `items`: relief items with unit (parcel, meal, kit, ...), weight, and person-days for food parcels.
- `staff`: role IDs only (operator, caseworker, coordinator, duty_officer, warehouse_officer, field_worker, admin).
- `settings`: editable limits (callback SLA, stale-position minutes, classifier threshold, simulator switch) and `demo.scenarios`.

Households
- `households`: fictional heads of household (Arabic or Latin script), size, area, status (`active`/`merged`), `merged_into`.
- `merge_history`: every merge or not-duplicate decision with full snapshots, field choices, moved records, reason, staff.

Call centre and casework (01, 02, 03, 04)
- `service_requests`: requests with notes, language, confirmed `category`, model `suggested_category`/`suggested_confidence`, status, assignment, callback due/done, closure.
- `request_events`: timeline per request (created, assigned, callback_scheduled, callback_done, closed, ...).
- `calls`: **every incoming call**; may link to one `complaint_id` and/or one `request_id`; `is_repeat` marks calls about something already logged.
- `complaints`: one row per complaint (topic, owner, status, resolution). Repeat calls never add complaints.
- `complaint_followups`: owner assignment, contacts, escalations, closure notes.
- `classifier_notes`: 600 labelled synthetic Arabic/English notes with a fixed train/test split.
- `service_providers`, `provider_services`: fictional service directory with keywords (English and Arabic), capacity, load, eligibility.
- `referrals`: proposed, approved, provider response, family received or not, with explanation and staff.

Intake, pipeline and sync (05, 06, 07, 08)
- `intake_records`: intake records from data entry, field devices or server, with `version`.
- `record_versions`: every saved version of an intake record.
- `intake_drafts`: drafts extracted from pasted notes, with missing/uncertain flags.
- `sync_devices`, `sync_queue`, `sync_conflicts`: offline devices, their outgoing queue (`base_version`), and conflicts needing a staff choice.
- `pipeline_sources`, `pipeline_runs`, `pipeline_quarantine`, `field_assessments`: field team exports, run history, rejected rows with rule and reason, and loaded rows (`row_key` UNIQUE so reloading adds nothing).

Supply chain and distributions (09, 10, 11, 12)
- `item_lead_times`: lead time, safety days and reorder threshold per warehouse and item.
- `distribution_plans`: planned quantities per site, item and date (past, today, future).
- `dispatches`: stock leaving a warehouse for a site (optionally on a convoy).
- `stock_movements`: **the only stock ledger**: on hand = received − dispatched (view `v_stock_on_hand`). Returns come back as `received`/`distribution_return`.
- `distributions`: per household: size, parcel item, `parcel_quantity` (parcels), `rte_meal_quantity` (meals), expected coverage days, dispatch link.
- `distribution_returns`: parcels returned after a distribution day.
- `forecast_assumptions`: editable assumptions (person-days per parcel, meals per person per day, ...).
- `reconciliation_notes`, `reconciliation_reports`: resolution notes per discrepancy and approved reports.

Field operations (13, 14, 15, 16)
- `volunteers`, `volunteer_trainings`, `volunteer_availability`: volunteer pool (no interpreters), trainings, dated availability.
- `shifts`, `shift_roles`, `shift_assignments`: shifts with required roles (driver, packing, registration, distribution) and assignments.
- `vehicles`: type, capacity, odometer, service schedule, document expiry dates, `simulated` flag.
- `vehicle_defects`: defects with severity (minor, major, grounding).
- `drivers`: licence class and expiry.
- `position_reports`: one row per position report (API, simulator or seed); view `v_last_position`.
- `convoys`, `convoy_vehicles`, `convoy_stops`, `convoy_loads`, `convoy_checklist`, `convoy_events`: convoy plan, vehicles and drivers, stop order, load per vehicle, pre-departure checklist, status log.
- `incidents`: fictional incident reports with verification status.
- `advisories`: suggested and final movement level per area, override flag and reason (enforced by a CHECK); view `v_current_advisory`.

Reporting and platform (17, 18, 19, 20)
- `field_updates`: sample field team updates, some with figures that conflict with records.
- `briefings`, `briefing_sources`: briefings and the source records behind each figure.
- `sitreps`, `sitrep_passages`: 16 fictional situation reports split into passages.
- `audit_log`: every change (actor, role, channel, before/after JSON, reason).
- `alert_rules`, `alerts`: configurable rules (stock below, overdue callbacks, stale position, failed pipeline run) and alert history with acknowledgement.
- `scheduled_jobs`, `job_runs`, `service_heartbeats`: jobs and their runs, and service liveness for the health page.

### Cross-project links (do not count anything twice)

- A call (02) links to a service request (01) through `calls.request_id`, and to a complaint through `calls.complaint_id`.
- The classifier in `shared/classifier.py` (trained and evaluated in 03) supplies the suggestion in 01.
- Referrals (04) link to households and requests.
- Planned distributions (`distribution_plans`, 11) feed the warehouse forecast (10); 12 reconciles plans, dispatches, distributions and returns.
- Vehicle readiness (`shared/fleet.py`, 14) and advisories (`shared/security.py`, 15) limit what 16 lets a coordinator dispatch.
- Position reports arrive through the API in 20 (`shared.fleet.record_position`); 14 and 17 read them.
- 17 reads from every table and writes nothing.

## 4. Shared helpers

| Module | Use it for |
|---|---|
| `shared.ui` | `page_header(project_number=NN)` first in every app: page config, demo banner, brand, title and the "who / task" line from your `project.toml`. Also `section`, `metric_row`, `table`, `badge`, `freshness_badge` (turns STALE past a limit), `staff_selector` (sidebar role selector), `confirm_action` (staff confirmation form), `show_text` / `bidi_html` / `has_arabic` (Arabic shown right-to-left). |
| `shared.charts` | `bar`, `line`, `heatmap`, `grid_map` (invented grid, no tiles), `apply_defaults`. Axis titles are required and must carry units, e.g. `"Parcels (count)"`. Never put parcels and meals on one axis. |
| `shared.confirm` | `require_confirmation(conn, confirmed_by, allowed_roles)` inside every consequential domain function (raises `ConfirmationRequired`). |
| `shared.db`, `shared.clock`, `shared.config` | Database access, audit, settings, IDs; frozen-able clock; paths and env vars. |
| `shared.food` | `coverage_days`, `parcel_coverage_days`, `meal_coverage_days`, `coverage_end_date`, `standard_parcels_for`, `load_assumptions`. |
| `shared.stock` | `on_hand`, `on_hand_qty`, `record_receipt`, `record_dispatch` (writes the dispatch and its ledger row together). |
| `shared.fleet` | `assess_vehicle`, `fleet_readiness` (ready / due_for_service / not_ready with reasons), `is_stale`, `stale_limit_minutes`, `record_position`. |
| `shared.security` | Documented advisory rule: `advisory_score`, `suggest_level`, `current_advisories`, `blocked_areas`, `LEVEL_NAMES`. |
| `shared.geo` | `distance_km`, `route_km` on the grid. |
| `shared.classifier` | `load_or_train`, `predict` (returns `category=None` below the threshold), `threshold_from_settings`. |
| `shared.call_notes` | Categories, labels, synthetic note generator. |
| `shared.projects`, `shared.ports` | Project registry from `project.toml` files; ports. |

### Staff confirmation pattern

```python
staff = ui.confirm_action("approve_ref", f"Approve referral of {hh} to {provider}?", roles=("caseworker", "coordinator"))
if staff:
    with db.transaction() as conn:
        p04_referrals.approve(conn, referral_id, confirmed_by=staff)  # calls confirm.require_confirmation first
    st.success("Referral approved")
```

Consequential actions that need this: referrals, record merges, sync conflict choices, convoy dispatch, advisory overrides, reconciliation sign-off, briefing approval, and anything else that cannot be quietly undone.

## 5. Ports and launching

- Project NN runs on **port 8500 + NN** (01 → 8501 ... 20 → 8520). Homepage 8500, API 8600. Defined only in `shared/ports.py`.
- Launch your project: `python run.py NN` (from the repo root, with the venv's Python), or `streamlit run projects/NN-<slug>/app.py --server.port 85NN`.
- The homepage (`python run.py`) starts every project whose `project.toml` says `status = "built"`.

## 6. Your project folder

```
projects/NN-<slug>/
  project.toml        marker: number, slug, title, group, status, user, task   (you set status = "built" at the end)
  app.py              the one Streamlit entry point
  pNN_<name>.py       your logic module(s); names MUST start with pNN_ (all project folders share sys.path in tests)
  sample_data/        any sample input files (fictional, scanned by the data safety test)
  README.md
  screenshot.png      captured from the running app
```

`app.py` must start like this so it works from any working directory:

```python
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from shared import db, ui  # noqa: E402
import pNN_logic  # noqa: E402

ui.page_header(project_number=NN)
```

Keep logic out of `app.py`: put calculations and workflow steps in `pNN_*.py` functions that take a connection, so tests can call them directly. Scope: build the required workflow in the spec row and nothing beyond it. No extra pages, settings screens or themes. A sidebar role selector is enough for local mode.

## 7. Tests

- One file: `tests/test_pNN_<name>.py`. Use the fixtures from `tests/conftest.py`:
  - `db_path`: a fresh seeded copy in a temp folder, set as `PORTFOLIO_DB_PATH`, clock frozen at `conftest.ANCHOR` (2026-11-16 10:00).
  - `conn`: an open connection to that copy.
  - `app_test`: `app_test(NN).run()` returns a Streamlit `AppTest`; assert `not at.exception` to prove the app starts.
- Tests never touch `data/portfolio.db` (a session guard enforces it).
- Cover every calculation in your spec row and the Part 4 tests that belong to your project. Run:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest tests/test_pNN_<name>.py
  .\.venv\Scripts\python.exe -m pytest            # the whole suite must still pass
  ```

## 8. Style check

```powershell
.\.venv\Scripts\ruff.exe check projects/NN-<slug> tests/test_pNN_<name>.py
.\.venv\Scripts\ruff.exe format projects/NN-<slug> tests/test_pNN_<name>.py
```

CI runs `ruff check .`, `ruff format --check .` and `pytest`. Use type hints.

## 9. Screenshot

```powershell
.\.venv\Scripts\python.exe scripts/screenshot.py --project NN --out projects/NN-<slug>/screenshot.png --db data/agentNN.db --wait-text "<text your page shows>"
```

It starts your app on its port against your database, waits for it to render, captures with the installed Chrome or Edge, and stops the app. Add `--full-page` for long pages. Never create a mock image.

## 10. README sections (all required)

`# NN · Title`, then: **Problem**, **Intended user**, **Technique** (name it: text extraction, classification, fuzzy matching, search, forecasting, optimization, rule-based scoring, or straightforward calculation), **Inputs**, **Assumptions**, **Limitations** (and any fallback used), **Launch** (`python run.py NN` and the streamlit command, port 85NN), **Screenshot** (`![...](screenshot.png)`), **Try this demo** (numbered steps from the seeded data and the **expected result**), and **Tests** (the test file and what it covers). List any extra library the project uses.

## 11. Definition of done (all must be true before `status = "built"`)

- [ ] `python run.py NN` starts the app with no errors (and `app_test(NN)` passes).
- [ ] The required workflow in the spec row works from start to finish with the seed data.
- [ ] Its calculations have automated tests in `tests/test_pNN_<name>.py`, and the whole suite passes.
- [ ] Consequential actions need a staff confirmation step (`ui.confirm_action` + `confirm.require_confirmation`).
- [ ] README has every section in 10.
- [ ] You followed the "Try this demo" walkthrough yourself on a fresh copy of the database and got the expected result.
- [ ] `screenshot.png` was captured from the running app with `scripts/screenshot.py`.
- [ ] `project.toml` says `status = "built"` (the homepage then shows the card with a working link).
- [ ] Style check passes for your files. Report to the director, who updates `PROGRESS.md` and commits.

Never report something as verified unless you ran it and saw it pass. If you could not verify something, say so and why.
