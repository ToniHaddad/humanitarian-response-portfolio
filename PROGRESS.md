# Progress
Current phase: 8 (final verification in progress)
Last updated: 2026-11-22 (local)

## Phases
- [x] Phase 0 Foundation
- [x] Phase 1 Group A (03, 02, 01, 04)
- [x] Phase 2 Group C (10, 11, 12, 09)
- [x] Phase 3 Group B first part (05, 06, 07)
- [x] Phase 4 Platform (20)
- [x] Phase 5 Group D and sync (15, 14, 13, 16, 08)
- [x] Phase 6 Group E (18, 19, 17)
- [x] Phase 7 Server mode
- [ ] Phase 8 Finish (final verification in progress)

## Projects
| # | Folder | Built | Tests pass | Walkthrough checked | Screenshot | Notes |
|---|--------|-------|------------|---------------------|------------|-------|
| 01 | call-center-request-dashboard | yes | yes (27) | yes (group review): intake to closure through the domain functions on a fresh seed | yes | Suggestion comes from `shared.classifier`, blank below 0.6; close needs staff confirmation. |
| 02 | call-center-calls-and-complaints | yes | yes (15) | yes (group review): calls 300 -> 302, complaints 28 -> 29, one complaint with 2 calls | yes | Every call type is logged; a call links to a request through `calls.request_id`; closing a complaint needs confirmation. |
| 03 | call-note-classifier-lab | yes | yes | yes (group review): 383 train / 217 test, accuracy 76.5%, 43.8% left blank | yes | Per category and language, confusion matrix, misclassified list; saves nothing. |
| 04 | beneficiary-referral-tracker | yes | yes | yes (group review): need matched to a provider, referral refused without confirmation, then approved and received | yes | Explanation per match; approval and outcome need staff confirmation. |
| 05 | emergency-data-entry | yes | yes (48) | yes (group review): two drafts saved as intake records | yes | |
| 06 | family-record-checker | yes | yes (27) | yes (group review): merge recorded with 5 records moved | yes | README steps use IDs and `demo.scenarios`, not names. |
| 07 | field-data-pipeline | yes | yes | yes (group review): 126 rows loaded on run 1, 0 loaded and 37 duplicates on run 2, a broken file fails | yes | `shared.pipeline.run_pipeline` is also called by the Project 20 scheduler. |
| 08 | offline-field-sync | yes | yes (21) | yes (group review): offline edit plus server edit gives a conflict, resolved with confirmation | yes | Seed gives the conflict record two versions; a test guards it. |
| 09 | unmet-needs-map | yes | yes (13) | yes (group review): 125 requested, 49 delivered, 76 gap, 5 not counted | yes | Area totals only, on the invented grid. |
| 10 | warehouse-stock-forecast | yes | yes (30) | yes (group review): 623 - 470 = 153; a receipt of 100 moves the stockout date by 62 days | yes | |
| 11 | food-distribution-tracker | yes | yes (24) | yes (group review): recorded parcel moves coverage end and next-week due list | yes | Parcels and meals are separate figures; recording needs staff confirmation. |
| 12 | distribution-reconciliation-report | yes | yes (18) | yes (group review): 100 dispatched, 96 distributed, 3 returned = 1 unaccounted | yes | Sign-off needs staff confirmation. |
| 13 | volunteer-shift-planner | yes | yes | yes (group review): overlaps and unfilled roles flagged, accept and cancel change the counts | yes | Four roles, no interpreter role; changes need a coordinator. |
| 14 | fleet-readiness-and-tracking | yes | yes | yes (group review): 4 ready, 1 due, 3 not ready; stale vehicle flagged after the limit | yes | Last position read from view `v_last_position`. |
| 15 | security-situation-log | yes | yes | yes (group review): override without a reason refused, with a reason recorded | yes | Unverified reports never feed the advisory rule; decisions need a duty officer. |
| 16 | convoy-dispatch-planner | yes | yes (40) | yes (group review): vehicle overdue for service blocked, reason shown, nothing assigned | yes | Uses `shared/fleet.py` and `shared/security.py` only. |
| 17 | operations-room-live-board | yes | yes (11) | by the building agent | yes (plus one with the simulator stopped) | Reads from all projects, writes nothing. |
| 18 | daily-emergency-briefing | yes | yes (12) | by the building agent | yes | Conflicts between field figures and records need a decision before approval. |
| 19 | sitrep-search-assistant | yes | yes (10) | by the building agent | yes | Lexical search over 16 reports, 64 passages; read-only. |
| 20 | operations-api-and-alerts | yes | yes (64) | by the building agent | yes | Shared alerts, scheduler, simulator and API. |

Tests-pass cells for 17 to 20 were confirmed by running each project's test file; the screenshot files exist in each project folder.

## Not verified
- Server mode has never been run under Docker: the image was not built, `docker compose up` was not executed, no container health check ran. Only static checks (`tests/test_server_mode.py`) and the backup, restore and health-check scripts on a temporary database were run.
- Docker-specific behavior is unconfirmed: that the pinned requirements install on `python:3.12-slim`, volume permissions for the non-root user, the port range and the arithmetic in the backup health check, and the start-up time of the apps container.
- The GitHub Actions workflow (`.github/workflows/ci.yml`) has never run on GitHub. The same commands pass locally on Windows; they were not run on Linux or macOS.
- The "Try this demo" walkthroughs were followed through the domain functions and Streamlit's test harness, not by clicking in a browser. Screenshots are real captures, mostly of the first screen.
- The server login is one shared staff username and password and one shared API token, not one account per person. The login gate is covered by tests but was not tried on a running server.
- Arabic right-to-left rendering was inspected only in the Project 04 screenshot; the Arabic rows in Project 03 were not inspected visually.
- Seeded figures quoted in project READMEs depend on the seed date and were read on seeds built on 2026-11-16 and 2026-11-22.
- The launcher opening a visible browser window was not exercised (checks used `--no-browser`).
- The Project 20 scheduled runs were exercised only through the tests and the "run now" paths, not over a long unattended period; the API docs page (`/docs`) needs network access for its scripts, `/reference` does not.
- Backups stay on the same machine as the database until they are copied away.

## Next step
Part 4 final checks, in order, and then `VERIFICATION.md`.
