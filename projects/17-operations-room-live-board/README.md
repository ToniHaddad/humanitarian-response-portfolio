# 17 · Operations room live board

## Problem
An operations room needs one wall display that shows the whole picture at a glance and does not need anyone to click: where the vehicles are, which convoys are out, how many requests and callbacks are open or overdue, which areas have a movement advisory, what stock is low, and how today's distributions are going against plan. A wall display that silently freezes is worse than none, so every panel states when its own data last changed and turns visibly stale if its source stops reporting.

## Intended user
Operations room staff watching a screen from across the room. Nobody operates it.

## Technique
**Straightforward calculation** (counts, thresholds and age comparisons) over the shared database, refreshed with `st.fragment(run_every=...)`. No model, no writes.

- Vehicle staleness: `shared.fleet.is_stale` with the limit `shared.fleet.stale_limit_minutes` (setting `stale_position_minutes`). Exactly at the limit is not stale; just past it is.
- Panel badge: `shared.ui.freshness_badge`. A panel's "last updated" is the newest timestamp in its own source data; the panel is stale when that is older than its limit (same `is_stale` rule).
- Map: `shared.charts.grid_map` on the invented 100 x 100 km grid, no tiles. Stale vehicles are large red crosses and are also listed under the map.
- Advisories: `shared.security.current_advisories` (areas above level 0). Stock: `v_stock_on_hand` against `item_lead_times.reorder_threshold` (strictly below; at the threshold is fine). Open alerts: table `alerts`. Overdue callbacks: `shared.alerts.overdue_callbacks`.
- Distributions against plan: today's non-cancelled `distribution_plans` by item unit against `distributions.parcel_quantity` (parcels) and `distributions.rte_meal_quantity` (meals). The two are shown separately and never added.
- Time comes from `shared.clock`.

## Inputs
The shared SQLite database, read only: `position_reports` (view `v_last_position`), `vehicles`, `convoys`, `convoy_vehicles`, `convoy_stops`, `convoy_events`, `service_requests`, `request_events`, `advisories` (view `v_current_advisory`), `areas`, `stock_movements` (view `v_stock_on_hand`), `item_lead_times`, `warehouses`, `items`, `alerts`, `alert_rules`, `distribution_plans`, `distributions`, `settings` (`stale_position_minutes`, `board_refresh_seconds`). Fictional seeded data only. No libraries beyond `requirements.txt`.

## Assumptions
- The board opens the database with `PRAGMA query_only = ON`, so it cannot write even by mistake; there are no buttons, selectors or sidebar. It refreshes every `board_refresh_seconds` (30 s in the seed, never below 5 s).
- Vehicle limit: the configured `stale_position_minutes`. The map panel's own badge uses the newest report of any vehicle, so it turns stale only when the whole position feed has stopped; single vehicles are flagged individually.
- Panels fed by people rather than a feed get fixed limits in `p17_board.PANEL_LIMIT_MINUTES`: convoys 24 h (newest `convoy_events`), requests 4 h (newest `request_events` or request), advisories 24 h, stock and alerts 24 h (newest `stock_movements` or alert), distributions 4 h (newest `recorded_at`).
- Requests panel: "open" is every request not closed; "not yet assigned" is status `new`.
- Convoy panel lists convoys that are departed, approved or planned; a departed convoy flags its stale vehicles.

## Limitations
- Built for a 1920 x 1080 screen in a full-screen browser (F11). Type scales with the page but small screens will scroll.
- The map shows labels that can overlap when vehicles are close together. Axes are not equal-scaled (the grid is stretched to the panel).
- At most the first four rows of each list are shown so the page fits one screen.
- Limits for the human-fed panels are constants, not settings (shared settings are not edited by this project).
- The board does not start or stop the simulator; Project 20 does.

## Launch
```powershell
.\.venv\Scripts\python.exe run.py 17
.\.venv\Scripts\streamlit.exe run projects/17-operations-room-live-board/app.py --server.port 8517
```
Open http://localhost:8517 and press F11.

## Screenshot
![Operations room live board](screenshot.png)

The same board after the simulator was stopped and time moved past the limit: ![All vehicles stale](screenshot_simulator_stopped.png)

## Try this demo
Use a fresh database copy (`PORTFOLIO_DB_PATH=data/agent17.db`, `python -m shared.seed --reset`). IDs come from the setting `demo.scenarios`, key `p17`.
1. Start the board (port 8517). Observed: seven vehicles reporting and `VEH-07` (the seeded stale vehicle) shown as a red cross, flagged "VEH-07 STALE" with its age, and the red-bordered "critical VEH-07" alert listed under Stock alerts. The map panel's badge says "Updated N min ago".
2. Stop the position simulator through its switch: set `position_simulator_enabled` to `0` (Project 20 does this from its page; the walkthrough test does it with one `UPDATE settings`). No new position arrives.
3. Move time on with the frozen clock (`PORTFOLIO_FROZEN_NOW` set to the seed anchor plus 10 minutes, then plus 16). Observed with the domain functions: at +0 and +10 minutes only `VEH-07` is stale; at +16 minutes (past the 15-minute limit) all eight vehicles are flagged stale, each named under the map, convoy `CNV-002` shows `VEH-01 STALE` and `VEH-02 STALE`, and the map panel itself turns stale (dashed red border, striped grey, badge "STALE: last update 16 min ago"). `screenshot_simulator_stopped.png` is the real board at that moment.
4. (Not run here.) Switching the simulator on again should clear the flags on the next refresh, once new reports arrive.

## Tests
`tests/test_p17_live_board.py` covers: a vehicle is not stale exactly at the limit and is stale one second past it; the seeded stale vehicle; the stopped-simulator walkthrough (no new rows, vehicle flagged after the limit); a panel's freshness turning stale past its limit and every panel going stale when its source stops; the board writing nothing (row counts of key tables and `audit_log` unchanged, and a write attempt on the read-only connection fails); parcels and meals kept separate; stock alert strictly below the reorder threshold; advisories above Normal only; overdue callback counts; and the app starting (`app_test(17)`).
