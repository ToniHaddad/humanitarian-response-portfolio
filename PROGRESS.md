# Progress
Current phase: 1 (not started; Phase 0 gate passed)
Last updated: 2026-11-22 01:40 (local)

## Phases
- [x] Phase 0 Foundation (gate passed 2026-11-22, see below)
- [ ] Phase 1 Group A (03, 02, 01, 04)
- [ ] Phase 2 Group C (10, 11, 12, 09)
- [ ] Phase 3 Group B first part (05, 06, 07)
- [ ] Phase 4 Platform (20)
- [ ] Phase 5 Group D and sync (15, 14, 13, 16, 08)
- [ ] Phase 6 Group E (18, 19, 17)
- [ ] Phase 7 Server mode
- [ ] Phase 8 Finish

## Phase 0 gate evidence
- Launcher opens the homepage: `python run.py --no-browser` started the homepage; `/_stcore/health` returned 200 within 2 s; headless Chrome captured the page from that running launcher (20 cards in 5 groups, demo banner). Launcher stopped; port 8500 free afterwards.
- Reset rebuilds the database: deleted 90 information calls in `data/portfolio.db`, ran `python run.py --reset` → calls back to full count; repeated with `python -m shared.seed --reset` → same. Homepage reset button tested in `tests/test_foundation_apps.py` (needs the confirmation tick; without it nothing changes).
- `pytest`: 64 passed (see the final gate run in the commit message).
- Data safety test passes: `tests/test_foundation_data_safety.py` (phone, email and IPv4 shapes in every seeded text value and in `projects/*/sample_data/**`).
- Style: `ruff check .` and `ruff format --check .` both pass.

## Projects
| # | Folder | Built | Tests pass | Walkthrough checked | Screenshot | Notes |
|---|--------|-------|------------|---------------------|------------|-------|
| 01 | call-center-request-dashboard | no | - | - | - | uses `shared.classifier` |
| 02 | call-center-calls-and-complaints | no | - | - | - | scenario `demo.scenarios.p02` |
| 03 | call-note-classifier-lab | no | - | - | - | model in `shared/classifier.py`, notes in `classifier_notes` |
| 04 | beneficiary-referral-tracker | no | - | - | - | |
| 05 | emergency-data-entry | no | - | - | - | |
| 06 | family-record-checker | no | - | - | - | 8 planted duplicate pairs: `p06` |
| 07 | field-data-pipeline | no | - | - | - | sample files to be created in its `sample_data/` |
| 08 | offline-field-sync | no | - | - | - | scenario `p08` (INT-0021, DEV-01) |
| 09 | unmet-needs-map | no | - | - | - | |
| 10 | warehouse-stock-forecast | no | - | - | - | WH-03 hygiene kits below threshold |
| 11 | food-distribution-tracker | no | - | - | - | scenario `p11` |
| 12 | distribution-reconciliation-report | no | - | - | - | scenario `p12` (100/96/3) |
| 13 | volunteer-shift-planner | no | - | - | - | planted overlap `p13` |
| 14 | fleet-readiness-and-tracking | no | - | - | - | |
| 15 | security-situation-log | no | - | - | - | scenario `p15` |
| 16 | convoy-dispatch-planner | no | - | - | - | scenario `p16` (CNV-004, VEH-04) |
| 17 | operations-room-live-board | no | - | - | - | scenario `p17` |
| 18 | daily-emergency-briefing | no | - | - | - | planted conflict `p18` |
| 19 | sitrep-search-assistant | no | - | - | - | 16 sitreps, 64 passages |
| 20 | operations-api-and-alerts | no | - | - | - | 4 alert rules, 4 scheduled jobs seeded |

## Not verified
- Launcher opening a visible browser window: the gate run used `--no-browser` (headless check only). `webbrowser.open` is called in the default mode but was not exercised here.
- GitHub Actions workflow (`.github/workflows/ci.yml`): not run on GitHub (no remote, by instruction). The same commands (`ruff check .`, `ruff format --check .`, `pytest`) pass locally on Windows; not run on Linux.
- Server mode (Docker): not in Phase 0; Docker is not installed on the build machine.

## Next step
Phase 1: build projects 03, 02, 01, 04 (one agent per project, following `CONTRIBUTING_AGENTS.md`). The director reviews each (tests, walkthrough on a fresh database copy, screenshot), updates this table and commits one project at a time. Then check the Phase 1 gate: all four done, and the Project 02 walkthrough gives two calls and one complaint.
