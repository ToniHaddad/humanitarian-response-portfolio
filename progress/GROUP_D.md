# Group D progress (field operations)
Director: Group D. Last updated: 2026-11-22 (round 2 review done)

## Projects
| # | Folder | Built | Tests pass | Walkthrough checked | Screenshot | Notes | Not verified | Next step |
|---|--------|-------|------------|---------------------|------------|-------|--------------|-----------|
| 13 | volunteer-shift-planner | yes | yes (test_p13_shifts.py) | yes, on fresh DB via domain functions: 6 shifts, 13 unfilled, 3 overlaps, 10 to review, 13 suggestions; cancel #21 -> 2 overlaps, 14 unfilled; accept VOL-046 -> 13 | real capture (viewed) | Four roles only, no interpreter role; confirm/cancel/accept need a coordinator | Click-through of the Streamlit forms by hand | none |
| 14 | fleet-readiness-and-tracking | yes | yes (test_p14_fleet.py) | yes: 4 ready, 1 due, 3 not ready; VEH-07 stale at 301 min; limit 400 clears it; CNV-002 41 reports, events planned/approved/departed | real capture (viewed) | Review fix: last position now read from view v_last_position | Click-through of forms by hand | none |
| 15 | security-situation-log | yes | yes (test_p15_security.py) | yes: AR-07 Restricted 7.0, AR-04 override, AR-02 0 -> 9.0 after verifying 2 reports; override without reason refused; with reason ADV-0015 | real capture (viewed) | Unverified never feeds the rule; decisions need duty officer | Click-through of forms by hand | none |
| 16 | convoy-dispatch-planner | yes | yes (test_p16_convoy.py, 40 tests) | yes, on fresh DB: assigning VEH-04 to CNV-004 refused with "VEH-04 is not ready on 2026-11-24: Service overdue since 2026-11-02 (every 180 days)", nothing assigned; full flow 200.82 vs 125.96 km, 7 dispatches, draft/approved/departed/arrived/returned | real capture (viewed, full page mid-walkthrough) | Uses shared/fleet.py and shared/security.py only; approve, dispatch and cancel need a coordinator | Click-through of forms by hand | none |

## Gate (Phase 5, Group D part)
- [x] 13, 14, 15, 16 meet the definition of done
- [x] Project 16 walkthrough: overdue vehicle blocked with reason shown
- Whole suite passes; ruff check passes; ruff format --check flags only scripts/healthcheck.py (not Group D).
