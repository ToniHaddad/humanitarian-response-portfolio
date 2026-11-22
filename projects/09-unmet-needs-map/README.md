# 09 · Unmet needs map

## Problem
Program managers need to see where requests for help are not being met. Requests and completed assistance sit in different records, so the gap is hard to see by area, aid type and date.

## Intended user
Program managers.

## Technique
Straightforward calculation (counts and ratios over the shared database). The project only reads; it writes nothing. No model, no estimation.

## Inputs
- `service_requests`: created date, area, confirmed `category` (aid type), `closure_outcome`.
- `referrals`: `request_id` and `family_received`.
- `areas`: names, grid centroids (`x_km`, `y_km`) and `population_estimate`.

## Assumptions
How the gap is worked out (also shown on the page):
- **Requests**: service requests created in the date range, first and last day included. Default range: the last 30 days up to today (demo clock).
- **Not a need**: requests closed as `duplicate`, `withdrawn` or `not_eligible`. They are left out of requests and of the gap, and shown as a separate "Not counted" figure.
- **Completed**: a counted request closed as `assisted`, or one with a referral where `family_received = 1`. Each request counts once, even with several referrals.
- **Outstanding (gap)** = requests - completed, per area and aid type. Open, in progress, referred-but-not-received and unreachable requests stay outstanding.
- **Completion rate (%)** = completed / requests (blank when an area has no requests). **Per 1,000 people** = outstanding / area population x 1,000.
- **Aid type** is the confirmed category. Requests with no confirmed category appear as "Not yet categorised"; the model's suggestion is never used.
- Area-level totals only. All 12 areas always appear, with zeros where nothing happened.
- The weekly chart groups requests by the week (Monday start) in which they were created; "completed" counts those same requests that are completed now.

## Limitations
- Food parcels handed out are recorded per household round (distributions), not per request. They are **not** added to completed assistance: doing so would count the same help twice.
- Completion is only what is recorded: a request closed "assisted" or a referral confirmed by the family. Help received but not recorded shows as outstanding.
- A request is dated by when it was created, so a request created before the range but completed inside it is not counted.
- Area totals hide differences inside an area. Map positions are on an invented 100 x 100 km grid, not real places.
- Marker size is outstanding + 0.5 so that areas with no gap stay visible as small dots.

## Launch
```powershell
.\.venv\Scripts\python.exe run.py 9
# or
.\.venv\Scripts\streamlit.exe run projects/09-unmet-needs-map/app.py --server.port 8509
```
Port 8509.

## Screenshot
![Unmet needs map](screenshot.png)

## Try this demo
The seeded dates move with the seed anchor, so the figures below are for a database built with the anchor used by the tests, with the demo clock frozen at the same moment:

```powershell
$env:PORTFOLIO_DB_PATH = "data\agent09.db"
$env:PORTFOLIO_FROZEN_NOW = "2026-11-16T10:00:00"
.\.venv\Scripts\python.exe -m shared.seed --reset --anchor 2026-11-16T10:00:00
.\.venv\Scripts\python.exe run.py 9
```

1. Open the page. The date range is 2026-10-17 to 2026-11-16 (last 30 days). Expected metrics: **125** requests, **49** completed, **76** outstanding, **5** not counted.
2. The area table (sorted by outstanding) starts with Riverbend: 18 requests, 6 completed, **12 outstanding** (33.3 % complete, 0.65 per 1,000 people). The "How the gap is calculated" box shows this as its worked example.
3. Set **Aid type** to **Food**. The largest gap is Pinecrest: **6 requests - 1 completed = 5 outstanding** (0.61 per 1,000 people, population 8,200). The map marker for Pinecrest is the biggest, and the Food column of the area x aid type heatmap shows 5 for Pinecrest.
4. Set **Aid type** to **Not yet categorised**: Riverbend shows 5 requests, 0 completed, 5 outstanding.
5. Set **Area** to Pinecrest and the aid type back to all: the metrics are the area's own totals (10 requests, 3 completed, 7 outstanding) and the weekly chart shows its requests and completed counts.

## Tests
`tests/test_p09_unmet_needs.py` covers: the gap per area and aid type on hand-built requests (assisted, a referral received counted once with two referrals, duplicate / withdrawn / not eligible excluded, open and unreachable outstanding, no-category requests as their own aid type); inclusive date boundaries; aid type and area filters; all 12 areas present with zeros; area totals summing to the overall totals; the per-1,000 figure; the default 30-day range; and the app starting with and without data.

No extra libraries beyond `requirements.txt`.
