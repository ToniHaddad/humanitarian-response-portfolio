# Verification

Date: 2026-11-22. Machine: Windows 11, Python 3.12, no Docker.

This file says what was run and seen to pass, what was not, and what the owner should check before the repository is made public. Nothing here is marked verified unless it was run.

## Summary

- All 20 projects are built. Each has an app, tests, a README with a "Try this demo" walkthrough, and a screenshot captured from the running app.
- Test suite: **643 passed, 0 failed** (29 test files, 1 deprecation warning from a library). Command: `python -m pytest`.
- Style: `ruff check .` and `ruff format --check .` pass.
- A fresh clone was set up word for word from the root README on Windows PowerShell. Setup, the one-command launch, three single-project launches and the command-line reset all worked.
- Server mode was written and statically checked. It was **not run**, because Docker was not available.

## The twenty projects

"Walkthrough" says how the README's "Try this demo" was checked: **re-run** means it was followed step by step on a freshly seeded database during final verification, through the functions the app calls or Streamlit's test harness. **Group review** means it was followed the same way during the group's review, and a test covers the workflow. None was clicked through by hand in a browser.

| # | Project | Built | Tests passing | Walkthrough checked | Screenshot present |
|---|---|---|---|---|---|
| 01 | call-center-request-dashboard | yes | 27 | group review | yes |
| 02 | call-center-calls-and-complaints | yes | 15 | re-run | yes |
| 03 | call-note-classifier-lab | yes | 8 | group review | yes |
| 04 | beneficiary-referral-tracker | yes | 27 | group review | yes |
| 05 | emergency-data-entry | yes | 48 | group review | yes |
| 06 | family-record-checker | yes | 27 | group review | yes |
| 07 | field-data-pipeline | yes | 26 | group review | yes |
| 08 | offline-field-sync | yes | 21 | re-run | yes |
| 09 | unmet-needs-map | yes | 13 | group review | yes |
| 10 | warehouse-stock-forecast | yes | 37 | group review | yes |
| 11 | food-distribution-tracker | yes | 24 | re-run | yes |
| 12 | distribution-reconciliation-report | yes | 18 | re-run | yes |
| 13 | volunteer-shift-planner | yes | 21 | group review | yes |
| 14 | fleet-readiness-and-tracking | yes | 19 | group review | yes |
| 15 | security-situation-log | yes | 27 | group review | yes |
| 16 | convoy-dispatch-planner | yes | 40 | re-run | yes |
| 17 | operations-room-live-board | yes | 11 | re-run | yes |
| 18 | daily-emergency-briefing | yes | 12 | by the building agent only | yes |
| 19 | sitrep-search-assistant | yes | 10 | by the building agent only | yes |
| 20 | operations-api-and-alerts | yes | 64 | by the building agent only | yes |

The remaining tests cover the foundation: schema, seed, data safety, server-mode files, documentation links, the server login and the name deny-list.

## The six named walkthroughs

Each was re-run on a freshly seeded database and gave the result the specification names.

- **02:** two calls about the same complaint. Calls went from 300 to 302 and complaints from 28 to 29; the one new complaint has 2 calls.
- **08:** a record edited offline and on the server. Sync reported 0 applied and 1 conflict; the record stayed at the server version until a staff member chose and confirmed, then it saved as a new version.
- **11:** recording a parcel for the walkthrough household moved its coverage end date, and the household left next week's forecast (28 households due became 27).
- **12:** 100 parcels dispatched, 96 distributed and 3 returned gave 1 parcel unaccounted for.
- **16:** assigning the vehicle that is overdue for service was refused with the reason shown, and nothing was assigned.
- **17:** with the position simulator stopped, the vehicle was not stale at exactly the 15-minute limit and was flagged stale at 16 minutes.

## Tests the specification requires

All fifteen exist and pass: parcel coverage end dates with household size and edited assumptions; parcels and meals never summed; stock balances by warehouse and item; duplicate merges (confirmation, history, no records lost); repeat complaint calls; repeated pipeline loads; sync conflicts never resolved silently; vehicle load limits and the not-ready block; the stale position flag at the limit; the advisory rule with an override and reason; alert thresholds at, above and below the limit; reconciliation totals including 100, 96, 3; low-confidence classifier predictions left blank; search returning "nothing found"; no phone number or email shape in the seed data.

## What was verified in local demo mode

Run in a fresh clone, following the root README:

- Virtual environment and install: worked as written.
- `python run.py --no-browser`: the homepage and all twenty project ports answered, and the API's documentation page loaded.
- All twenty apps and the homepage rendered in Streamlit's test harness with no exception.
- `python run.py 02`, `12` and `17` each started on their own.
- `python run.py --reset` restored deleted rows.
- All 31 relative links in the root README resolve. The homepage shows 20 of 20 demos ready and no "In progress" label.
- A scan of the files and the git history found no real IP address, hostname, key, token or organization name, and no `.env`, database or private brief tracked by git.

These checks ran at commit `180c849`. Later commits changed presentation only (labels, chart spacing, table column widths, retaken screenshots) and removed personal references from two text files; the full suite and style check were re-run afterwards and pass. The launch of all twenty ports was not repeated after those commits.

## What was verified in server mode

Only static checks: the Compose file parses; every service has a restart policy, a health check, the shared database volume and the same time zone; no port is bound to all interfaces; `.env.example` holds placeholders only. The backup and restore scripts were run for real against a temporary database, including a restore after deliberate damage. The login gate on data-changing pages is covered by tests.

## Fallbacks used in place of the specified technique

One fallback was used:

- **Project 16:** the suggested stop order uses a nearest-neighbour heuristic instead of a full route optimizer. Its README says so under "Limitations".

Two design choices are worth knowing, though the specification allows them: Project 19 searches with a lexical TF-IDF index (no synonyms), and in Project 20 the position simulator runs as a scheduler job, so the Compose file has no separate simulator container.

No project replaced a required workflow with a simpler one.

## Not verified

- **Server mode under Docker:** the image was never built and `docker compose up` was never run. Unconfirmed: that the pinned requirements install on the base image, volume permissions for the non-root user, container health checks, and the start-up time of the container that runs all the apps.
- **GitHub Actions:** the workflow file exists but has never run on GitHub. The same commands pass locally on Windows only; macOS and Linux commands in the README were not run.
- **Browser click-through:** no walkthrough was performed by a person clicking in a browser. Charts, styling and right-to-left layout were judged from the screenshots.
- **Fourteen walkthroughs** were not re-run in the final pass (see the table). Projects 18, 19 and 20 had no group review; their walkthroughs were checked only by the agent that built them.
- **The homepage reset button** was checked by a test, not by pressing it. The launcher opening a browser window, and stopping it with Ctrl+C, were not exercised.
- **Long unattended running:** the scheduler was exercised through tests and "run now" controls, not over hours.
- **The API documentation page** (`/docs`) loads its scripts from the internet. `/reference` works offline.

## Known limitations, one line per project

- 01: the category suggestion is only as good as the small classifier; many suggestions are left blank.
- 02: complaint topics are a fixed list chosen by the operator, not detected from the note.
- 03: about 76% accuracy on held-out synthetic notes, with about 44% of predictions left blank at the default threshold; Health is the weakest category.
- 04: matching is keyword and distance scoring over a small fictional directory.
- 05: extraction relies on patterns and keywords; unusual phrasing leaves fields flagged as missing.
- 06: matching uses spelling normalization and similarity; it suggests pairs, and staff decide.
- 07: error rates count repeat loads of the same file again; "Run now" takes no staff confirmation.
- 08: the device is simulated inside the same database; the screenshot shows the page before a conflict exists.
- 09: area-level totals only; the map is an invented grid.
- 10: the forecast is recent average demand plus planned distributions; no seasonality.
- 11: coverage is a person-days calculation from editable assumptions, not consumption data.
- 12: reconciles by site, item and period; staff notes do not change the figures.
- 13: assignments are a greedy rule-based suggestion, not an optimized roster.
- 14: positions are simulated; "stale" is a single configurable limit.
- 15: the advisory level is a documented scoring rule over verified incidents, confirmed or overridden by a duty officer.
- 16: nearest-neighbour stop order; README dates match a seed built on the day it was written.
- 17: panel staleness limits for people-fed panels are constants in the code; area names can overlap vehicle labels on the map.
- 18: only "households reached" is compared between field updates and records; stock is the current balance.
- 19: lexical search with a hand-tuned relevance threshold; the library is English only.
- 20: one shared staff login and one API token in server mode; the health endpoint reports "not healthy" on a first launch until the pipeline job has run once.

Across projects: figures quoted in the READMEs depend on the day the demo data is generated, because seeded dates follow the clock. Several wide tables scroll sideways, so their last column is cut off in static screenshots.

## What the owner should check by hand before making this public

1. **Commit author identity.** Every commit carries the git identity configured on the build machine (a real name and a personal email address). Decide whether to keep it, rewrite the history with a no-reply address, or publish a fresh single commit.
2. **Fictional names.** Heads of household are random combinations of common first and family names, which Project 06 needs for its spelling-variant checks. Three combinations that may match public figures are blocked by a deny-list and a test. Any common combination can match a real person: read the two name lists in `shared/seed_content.py` and extend the deny-list if needed.
3. **`CONTRIBUTING_AGENTS.md`.** It documents the conventions the build followed and says the projects were built by parallel agents. Keep it, rename it, or remove it, as you prefer.
4. **Click through the apps in a browser**, at least the six named walkthroughs and the Arabic notes in Projects 03, 04 and 05, to confirm right-to-left display.
5. **Run server mode once** on a machine with Docker (`DEPLOYMENT.md`), and expect small fixes on the first start.
6. **Push to a private repository first** and confirm the GitHub Actions workflow passes on Linux.
7. **Read the root README** as a first-time visitor, and the screenshots of Projects 08 and 20, whose images do not show the exact state their walkthroughs end in.
