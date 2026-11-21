# Decisions

`<date> | <project or area> | <decision> | <why>`

2026-11-21 | repo | `BUILD_SPEC.md` is listed in `.gitignore` and never committed | It is the owner's private brief and names a real organization.
2026-11-21 | repo | Portfolio brand is "Waypoint Response Lab"; no real organization is named in code or docs | The spec asks for original branding and an independent presentation.
2026-11-21 | stack | Pinned versions are the ones pip resolved on 2026-11-21 (Streamlit 1.67.0, pandas 3.1.1, scikit-learn 1.9.1, FastAPI 0.165.0, APScheduler 3.11.3) | The spec asks for pinned versions; these were tested here.
2026-11-21 | stack | No `uv`; plain `venv` + `pip install -r requirements-dev.txt` | Not installed on the build machine; plain pip works everywhere.
2026-11-21 | architecture | Each project runs as its own Streamlit process on port 8500+NN; homepage 8500; API 8600; ports defined only in `shared/ports.py` | Owner instruction; lets many agents run their apps in parallel without collisions.
2026-11-21 | architecture | `python run.py` starts the homepage plus every project whose `project.toml` says `status = "built"` | One command for the whole portfolio, and unbuilt projects are not started.
2026-11-21 | architecture | Homepage card status comes from `projects/NN-*/project.toml` (`status = "in_progress"` or `"built"`) | Owner instruction: each project agent edits only its own folder.
2026-11-21 | database | Database path is resolved only in `shared.config.db_path()` from `PORTFOLIO_DB_PATH` (default `data/portfolio.db`) | Owner instruction; each agent runs against its own copy.
2026-11-21 | database | All twenty projects' tables are defined in one file, `shared/schema.sql` (65 tables, 4 views) | Spec Phase 0: full schema in one place with shared identifiers.
2026-11-21 | database | Stock is a single ledger (`stock_movements`, received/dispatched only); on hand is the view `v_stock_on_hand`; returns re-enter as `received` with reason `distribution_return`; write-offs leave as `dispatched` | Makes "received minus dispatched equals on hand" exact and avoids storing balances twice.
2026-11-21 | database | A dispatch writes one `dispatches` row and one ledger row together (`shared.stock.record_dispatch`) | One activity, one quantity in the ledger; the dispatch row is the business record that plans, convoys and reconciliation link to.
2026-11-21 | database | Parcels and ready-to-eat meals are separate columns (`parcel_quantity`, `rte_meal_quantity`) and are converted to coverage days separately (`shared.food`) | Spec: keep the units separate and never sum them.
2026-11-21 | database | Repeat calls are rows in `calls` with `complaint_id` set and `is_repeat = 1`; complaints are counted from `complaints` only | Spec: a repeat call raises the call count, not the complaint count.
2026-11-21 | database | Advisory overrides need a non-empty reason, enforced by a CHECK constraint on `advisories` | Staff review must leave a recorded reason.
2026-11-21 | database | Merges never delete a household: the merged row gets `status = 'merged'`, `merged_into`, and a full snapshot in `merge_history` | Spec: history preserved, no records lost.
2026-11-21 | database | Pipeline rows carry a UNIQUE `row_key` (hash of source, household, date, need) | Loading the same file twice cannot create duplicates.
2026-11-21 | database | Map positions use an invented planar grid in km (`x_km`, `y_km`, 0-100), not latitude/longitude; charts draw it without map tiles | Spec data rule (invented region), and it works offline.
2026-11-21 | data | Staff are role IDs only (`OPR-01`, `CW-02`, ...); volunteers, drivers and vehicles are IDs; household heads have fictional names randomly combined from common first and family names (needed for the Arabic/Latin duplicate checks in 06) | Spec excludes staff names; 06 needs names with spelling variants.
2026-11-21 | data | Seed dates are offsets from an anchor time that defaults to the moment the seed runs; `--anchor` pins it; tests pin 2026-11-16 10:00 | Keeps "today", "overdue" and "next week" meaningful whenever a reviewer runs it, while the same anchor gives identical data.
2026-11-21 | data | Each seed section uses its own random stream seeded with `4217:<section>` | Changing one section does not shift the data of the others.
2026-11-21 | data | Walkthrough IDs and periods are written by the seed into the setting `demo.scenarios` | Apps and READMEs can point at the right records even though dates move with the anchor.
2026-11-21 | shared | Cross-project calculations live in `shared/` before any project is built: `food` (coverage), `stock` (ledger), `fleet` (readiness, staleness, position reports), `security` (advisory rule v1), `geo`, `classifier` | Projects are built in parallel; 01 and 03, 10/11/12, and 14/15/16/17 must use one definition each.
2026-11-21 | shared | Advisory rule v1: verified incidents in the last 7 days; severity weighted 1.0 within 48 h, 0.5 older; level 3 at score >= 12, 2 at >= 6, 1 at >= 3; level >= 2 blocks convoy stops | A documented, simple rule the duty officer can check by hand.
2026-11-21 | shared | Vehicle not ready = service overdue (days or km), any expired document, or an open grounding defect; due for service within 14 days or 500 km is shown but not blocking | Clear, explainable readiness reasons for 14 and 16.
2026-11-21 | shared | Stale position = older than the limit (`settings.stale_position_minutes`, default 15); exactly at the limit is not stale | Gives an unambiguous rule for the "at the limit" test.
2026-11-21 | shared | Classifier: char n-gram (2-4) TF-IDF + logistic regression; predictions below `settings.classifier_confidence_threshold` (0.6) are returned blank | Works for Arabic and English without a tokenizer; small and local.
2026-11-21 | tests | Tests use a session-built seeded database in pytest's temp folder, copied per test; a session guard points `PORTFOLIO_DB_PATH` away from the default database | Owner instruction: tests never touch the default database.
2026-11-21 | tests | One test file per project, `tests/test_pNN_<name>.py`; project logic modules must be named `pNN_*.py` | All project folders share `sys.path` in tests, so names must be unique; a test enforces it.
2026-11-21 | tooling | Style check is `ruff check .` and `ruff format --check .` (line length 140, E501 ignored) | One fast tool for lint and format, also run in CI.
2026-11-21 | tooling | Screenshots use Playwright with the installed Chrome (`channel="chrome"`, Edge fallback); full-page capture grows the viewport to Streamlit's scroll container | No browser download; Streamlit scrolls inside its own container.
2026-11-21 | tooling | Database reset copies the freshly built database into the existing file with SQLite's backup API | Windows cannot delete a file that a running app has open.
2026-11-21 | ui | `.streamlit/config.toml` sets `toolbarMode = "minimal"` | Hides the developer "Deploy" button from demo pages.
2026-11-21 | process | The `toni-skills:task-observer` skill was not started in this run | The run was restricted to the repository folder; that skill writes logs outside it.
2026-11-22 | 03 / shared | classifier_notes test split now holds out one note template per category and language (`call_notes.make_labelled_notes_split`) plus every fourth other note; test accuracy 0.76 instead of 1.00 | With the old random split every test note shared a template with training, so accuracy was 100% and there were no misclassified examples to show; held-out phrasing gives an honest generalisation check
