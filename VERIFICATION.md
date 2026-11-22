# Verification

Date: 2026-11-22. Machines: Windows 11 with Python 3.12 for local demo mode; GitHub Actions Linux runners for the test workflow and for server mode under Docker.

This file says what was run and seen to pass, what was not, and what remains to check. Nothing here is marked verified unless it was run.

## Summary

- All 20 projects are built. Each has an app, tests, a README with a "Try this demo" walkthrough, and a screenshot captured from the running app.
- Test suite: **671 passed, 0 failed** (29 test files, 1 deprecation warning from a library). Command: `python -m pytest`.
- Style: `ruff check .` and `ruff format --check .` pass.
- Two GitHub Actions workflows pass on Linux: the style check and tests, and the server-mode run under Docker.
- A fresh clone was set up word for word from the root README on Windows PowerShell. Setup, the one-command launch, three single-project launches and the command-line reset all worked.
- Every project's walkthrough was followed on two differently dated copies of the demo data and gave the result its README states on both.
- Server mode was built and run with Docker Compose on a GitHub Actions Linux runner: all four services became healthy and a smoke script's 16 checks passed, twice in a row. It has not been run on a long-lived server.

## The twenty projects

"Walkthrough checked" means the README's "Try this demo" was followed from start to finish, on demo data generated on the day of the check and again on data generated five days later with the clock frozen to match, through Streamlit's test harness and the functions the app calls. Five apps were also checked in a real browser (see "Browser check on the public repository").

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

Each project has its own test file; the rest of the tests cover the foundation: schema, seed, data safety, server-mode files, documentation links, the server login and the name deny-list.

## The six named walkthroughs

Each gave the result the specification names, on both copies of the data.

- **02:** two calls about the same complaint add two incoming calls and one complaint; the new complaint shows 2 calls.
- **08:** a record edited offline and on the server. Sync reported 0 applied and 1 conflict; the record stayed at the server version until a staff member chose and confirmed, then it saved as a new version.
- **11:** recording a parcel for the walkthrough household moved its coverage end date to 29 days from the day of recording, and the household left next week's forecast (households due fell by 1).
- **12:** 100 parcels dispatched, 96 distributed and 3 returned gave 1 parcel unaccounted for.
- **16:** assigning the vehicle that is overdue for service was refused with the reason shown, and nothing was assigned.
- **17:** with the position simulator stopped, vehicles were not stale at 10 minutes and were flagged stale at 16 minutes (the limit is 15). Switching the simulator back on cleared the flags once new positions arrived.

## Browser check on the public repository

A fresh clone of the public repository was set up from its README, launched with the one command, and opened in Chrome:

- **Homepage:** 20 of 20 demos ready.
- **02:** a complaint call was logged with an Arabic note (it typed right-to-left), then a second call was linked to the same complaint. The page reported the second call as a repeat; calls received rose by 2, new complaints by 1 and open complaints by 1.
- **12:** the walkthrough period showed 100 planned, 100 dispatched, 96 distributed, 3 returned and 1 unaccounted for, with the formula spelled out.
- **16:** assigning VEH-04 to the draft convoy was refused on the page with the reason "not ready... Service overdue".
- **17:** the board was live, with positions updated less than a minute before and the seeded stale vehicle flagged.
- **04:** the page loaded with an Arabic request shown.

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

The stack was built and started with `docker compose` on a GitHub Actions Linux runner, and `scripts/server_smoke.py` ran against it. Observed, in two consecutive runs: the homepage and all twenty apps answered their health endpoint; the API reported healthy and its documentation loaded; a write without credentials or with a wrong login was refused, and a write with the staff login or the API token succeeded and appeared in the audit log; the scheduler's heartbeat was recent and its start-up jobs (position simulator, alert evaluation) had succeeded; new position reports were arriving; the backup service had written a backup and the restore script restored it into a scratch path with the integrity check passing; every published port was bound to the loopback address; and the API came back after a restart. Static tests also check the Compose file (restart policy, health check, shared volume and time zone per service; no port on all interfaces; placeholders only in `.env.example`), and the login gate on data-changing pages is covered by tests.

## Fallbacks used in place of the specified technique

One fallback was used:

- **Project 16:** the suggested stop order uses a nearest-neighbour heuristic instead of a full route optimizer. Its README says so under "Limitations".

Two design choices are worth knowing, though the specification allows them: Project 19 searches with a lexical TF-IDF index (no synonyms), and in Project 20 the position simulator runs as a scheduler job, so the Compose file has no separate simulator container.

No project replaced a required workflow with a simpler one.

## Not verified

- **Server mode on a real server:** it ran only on a CI runner, for minutes. Not verified: a long-lived server, access over a private network from another machine, a host reboot, a browser login to the apps, the scheduled backups over days, and a restore over a live database.
- **Browser click-through of every app:** a browser check was done on a fresh clone of the public repository for the homepage and Projects 02, 04, 12, 16 and 17 (see below). The other fifteen apps were not opened in a browser; their walkthroughs were followed through the test harness.
- **macOS:** the README's macOS and Linux commands were not typed by hand; Linux is covered only by the GitHub Actions run.
- **The scheduler ticking on its own:** job registration and each job's "run now" path are tested; a real scheduled tick was observed once at start-up, not over hours.
- **The homepage reset button** was checked by a test, not by pressing it. The launcher opening a browser window, and stopping it with Ctrl+C, were not exercised.
- **The API documentation page** (`/docs`) loads its scripts from the internet. `/reference` works offline.

## Known limitations, one line per project

- 01: the category suggestion is only as good as the small classifier; about a quarter of suggestions are left blank.
- 02: complaint topics are a fixed list chosen by the operator, not detected from the note.
- 03: 90.8% accuracy on held-out synthetic notes, with 23% of predictions left blank at the default threshold; Shelter is the weakest category at 83%. Real notes would score lower than synthetic ones.
- 04: matching is keyword and distance scoring over a small fictional directory.
- 05: extraction relies on patterns and keywords; unusual phrasing leaves fields flagged as missing.
- 06: matching uses spelling normalization and similarity; it suggests pairs, and staff decide.
- 07: validation rules are fixed in code; the scheduled run needs the scheduler to be running.
- 08: the device is simulated inside the same database.
- 09: area-level totals only; the map is an invented grid.
- 10: the forecast is recent average demand plus planned distributions; no seasonality.
- 11: coverage is a person-days calculation from editable assumptions, not consumption data.
- 12: reconciles by site, item and period; staff notes do not change the figures.
- 13: assignments are a greedy rule-based suggestion, not an optimized roster.
- 14: positions are simulated; "stale" is a single configurable limit.
- 15: the advisory level is a documented scoring rule over verified incidents, confirmed or overridden by a duty officer.
- 16: nearest-neighbour stop order; the screenshot shows an example convoy, not the walkthrough's quantities.
- 17: an area name is hidden on the map when a vehicle sits on it; panel staleness limits default to constants unless set in the settings table.
- 18: only "households reached" is compared between field updates and records; the briefing lists the five newest field updates to stay on one page, with the rest in the source records.
- 19: lexical search with a hand-tuned relevance threshold; the library is English only.
- 20: one shared staff login and one API token in server mode; "run now" for a job is role-gated and audited but has no staff confirmation form.

Across projects: long text in tables is shortened, with the full text in an expander or a detail view.

## What remains to check by hand

1. **Fictional names.** Heads of household are random combinations of common first and family names, which Project 06 needs for its spelling-variant checks. Three combinations that may match public figures are blocked by a deny-list and a test. Any common combination can match a real person: read the two name lists in `shared/seed_content.py` and extend the deny-list if needed.
2. **Open the remaining apps in a browser**, in particular 08 and 11 (named walkthroughs not yet clicked through) and the Arabic notes in Projects 03 and 05.
3. **Run server mode on the real server** (`DEPLOYMENT.md`, then `python3 scripts/server_smoke.py`), reach it over the private network from another machine, and log in through a browser.
4. **Read the root README** as a first-time visitor.
