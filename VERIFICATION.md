# Verification

Date: 2026-11-22. Machine: Windows 11, Python 3.12, no Docker.

This file says what was run and seen to pass, what was not, and what remains to check. Nothing here is marked verified unless it was run.

## Summary

- All 20 projects are built. Each has an app, tests, a README with a "Try this demo" walkthrough, and a screenshot captured from the running app.
- Test suite: **655 passed, 0 failed** (29 test files, 1 deprecation warning from a library). Command: `python -m pytest`.
- Style: `ruff check .` and `ruff format --check .` pass.
- The GitHub Actions workflow (style check and tests) passes on Linux.
- A fresh clone was set up word for word from the root README on Windows PowerShell. Setup, the one-command launch, three single-project launches and the command-line reset all worked.
- Every project's walkthrough was followed on two differently dated copies of the demo data and gave the result its README states on both.
- Server mode was written and statically checked. It was **not run**, because Docker was not available.

## The twenty projects

"Walkthrough checked" means the README's "Try this demo" was followed from start to finish, on demo data generated on the day of the check and again on data generated five days later with the clock frozen to match, through Streamlit's test harness and the functions the app calls. None was clicked through by hand in a browser.

| # | Project | Built | Tests passing | Walkthrough checked | Screenshot present |
|---|---|---|---|---|---|
| 01 | call-center-request-dashboard | yes | yes | yes, two seeds | yes |
| 02 | call-center-calls-and-complaints | yes | yes | yes, two seeds | yes |
| 03 | call-note-classifier-lab | yes | yes | yes, two seeds | yes |
| 04 | beneficiary-referral-tracker | yes | yes | yes, two seeds | yes |
| 05 | emergency-data-entry | yes | yes | yes, two seeds | yes |
| 06 | family-record-checker | yes | yes | yes, two seeds | yes |
| 07 | field-data-pipeline | yes | yes | yes, two seeds | yes |
| 08 | offline-field-sync | yes | yes | yes, two seeds | yes |
| 09 | unmet-needs-map | yes | yes | yes, two seeds | yes |
| 10 | warehouse-stock-forecast | yes | yes | yes, two seeds | yes |
| 11 | food-distribution-tracker | yes | yes | yes, two seeds | yes |
| 12 | distribution-reconciliation-report | yes | yes | yes, two seeds | yes |
| 13 | volunteer-shift-planner | yes | yes | yes, two seeds | yes |
| 14 | fleet-readiness-and-tracking | yes | yes | yes, two seeds | yes |
| 15 | security-situation-log | yes | yes | yes, two seeds | yes |
| 16 | convoy-dispatch-planner | yes | yes | yes, two seeds | yes |
| 17 | operations-room-live-board | yes | yes | yes, two seeds | yes |
| 18 | daily-emergency-briefing | yes | yes | yes, two seeds | yes |
| 19 | sitrep-search-assistant | yes | yes | yes, two seeds | yes |
| 20 | operations-api-and-alerts | yes | yes | yes, two seeds; the command-line part of its last step through the API test client only | yes |

Each project has its own test file; the rest of the 655 tests cover the foundation: schema, seed, data safety, server-mode files, documentation links, the server login and the name deny-list.

## The six named walkthroughs

Each gave the result the specification names, on both copies of the data.

- **02:** two calls about the same complaint add two incoming calls and one complaint; the new complaint shows 2 calls.
- **08:** a record edited offline and on the server. Sync reported 0 applied and 1 conflict; the record stayed at the server version until a staff member chose and confirmed, then it saved as a new version.
- **11:** recording a parcel for the walkthrough household moved its coverage end date to 29 days from the day of recording, and the household left next week's forecast (households due fell by 1).
- **12:** 100 parcels dispatched, 96 distributed and 3 returned gave 1 parcel unaccounted for.
- **16:** assigning the vehicle that is overdue for service was refused with the reason shown, and nothing was assigned.
- **17:** with the position simulator stopped, vehicles were not stale at 10 minutes and were flagged stale at 16 minutes (the limit is 15). Switching the simulator back on cleared the flags once new positions arrived.

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

The clone checks ran at an earlier commit. Later commits changed presentation (labels, chart spacing, table column widths, retaken screenshots), rewrote the README walkthroughs, added tests, added a staff confirmation step in Project 15, and added one seeded job run. The full suite and style check were re-run after them and pass, locally and on GitHub Actions. The launch of all twenty ports was not repeated after those commits.

## What was verified in server mode

Only static checks: the Compose file parses; every service has a restart policy, a health check, the shared database volume and the same time zone; no port is bound to all interfaces; `.env.example` holds placeholders only. The backup and restore scripts were run for real against a temporary database, including a restore after deliberate damage. The login gate on data-changing pages is covered by tests.

## Fallbacks used in place of the specified technique

One fallback was used:

- **Project 16:** the suggested stop order uses a nearest-neighbour heuristic instead of a full route optimizer. Its README says so under "Limitations".

Two design choices are worth knowing, though the specification allows them: Project 19 searches with a lexical TF-IDF index (no synonyms), and in Project 20 the position simulator runs as a scheduler job, so the Compose file has no separate simulator container.

No project replaced a required workflow with a simpler one.

## Not verified

- **Server mode under Docker:** the image was never built and `docker compose up` was never run. Unconfirmed: that the pinned requirements install on the base image, volume permissions for the non-root user, container health checks, and the start-up time of the container that runs all the apps.
- **Browser click-through:** no walkthrough was performed by a person clicking in a browser. Charts, styling and right-to-left layout were judged from the screenshots.
- **macOS:** the README's macOS and Linux commands were not typed by hand; Linux is covered only by the GitHub Actions run.
- **Project 20's last walkthrough step** (`python run.py 20` and three `curl` calls) was made through the API test client, not from a terminal.
- **The scheduler ticking on its own:** job registration and each job's "run now" path are tested; a real scheduled tick was observed once at start-up, not over hours.
- **The homepage reset button** was checked by a test, not by pressing it. The launcher opening a browser window, and stopping it with Ctrl+C, were not exercised.
- **Screenshots** were not retaken after the README rewrite; their captions were checked against the images.
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
- 16: nearest-neighbour stop order; the screenshot shows an example convoy, not the walkthrough's quantities.
- 17: panel staleness limits for people-fed panels are constants in the code; area names can overlap vehicle labels on the map.
- 18: only "households reached" is compared between field updates and records; the briefing fits one page on the seeded day, but a day with many field updates would run longer.
- 19: lexical search with a hand-tuned relevance threshold; the library is English only.
- 20: one shared staff login and one API token in server mode; saving a rule, stopping the simulator and "run now" are role-gated and audited but have no staff confirmation form.

Across projects: several wide tables scroll sideways, so their last column is cut off in static screenshots.

## What remains to check by hand

1. **Fictional names.** Heads of household are random combinations of common first and family names, which Project 06 needs for its spelling-variant checks. Three combinations that may match public figures are blocked by a deny-list and a test. Any common combination can match a real person: read the two name lists in `shared/seed_content.py` and extend the deny-list if needed.
2. **Click through the apps in a browser**, at least the six named walkthroughs and the Arabic notes in Projects 03, 04 and 05, to confirm right-to-left display.
3. **Run server mode once** on a machine with Docker (`DEPLOYMENT.md`), and expect small fixes on the first start.
4. **Read the root README** as a first-time visitor.
