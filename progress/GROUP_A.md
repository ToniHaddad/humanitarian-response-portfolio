# Group A progress (call center and casework)

Round 2 review by Director A. Database copy: `data/directorA.db`. Whole suite at review time: 581 passed. Group A test files: 77 passed. `ruff check` and `ruff format --check` pass for the four projects and their tests.

| # | Folder | Built | Tests pass | Walkthrough checked | Screenshot | Notes |
|---|--------|-------|------------|---------------------|------------|-------|
| 01 | 01-call-center-request-dashboard | yes | yes (27) | yes: intake to closure through the domain functions on a fresh seed; AppTest walkthrough in the tests | yes, retaken from the running app | README "Try this demo" numbers were stale after a seed change (REQ-00114, 39/10/14/21); corrected to REQ-00113 and 40/11/13/20 with a note to compare changes. Suggestion comes from `shared.classifier`, blank below 0.6; close needs staff confirmation. |
| 02 | 02-call-center-calls-and-complaints | yes | yes (15) | yes: fresh anchor seed, calls 300 -> 302, complaints 28 -> 29, CMP-0029 has 2 calls (Today tiles 2 -> 4 calls, 0 -> 1 new complaints, 12 -> 13 open) | yes, retaken | Logs every call type; a call links to a request through `calls.request_id`; closing a complaint needs confirmation. |
| 03 | 03-call-note-classifier-lab | yes | yes | yes: 383 train / 217 test, accuracy 76.5%, 43.8% blank, shown accuracy 100%, Health weakest at 44.7%, sample notes as in README | yes, retaken | Per category, per language (ar 81.8%, en 71.0%), confusion matrix, misclassified list, "Try a note". Saves nothing, so no confirmation needed. |
| 04 | 04-beneficiary-referral-tracker | yes | yes | yes: HH-00009 fever need gives General clinic at Harbor Flats Health Point, 0.737, 1.4 km, 10 of 80 places; REF-0031 created, approval refused without confirmation, then approved, accepted, support_received; School fees support excluded | yes (full page, shows matches) | Explanation per match; approval and outcome need staff confirmation. |

## Not verified
- The "Try this demo" clicks were run through the domain functions and the AppTest walkthroughs in the test files, not by hand in a browser. Screenshots are real captures (`scripts/screenshot.py`) of the first screen only for 01, 02, 03.
- Arabic right-to-left rendering was checked in the 04 screenshot only; the Arabic misclassified rows in 03 were not inspected visually.
- Seeded figures in the READMEs depend on the seed date; they were read on a seed built 2026-11-22 (01) and the 2026-11-16 anchor (02).

## Next step
Supervisor merges this table into `PROGRESS.md`.
