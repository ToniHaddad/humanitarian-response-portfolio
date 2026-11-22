# 12 · Distribution reconciliation

## Problem
After a distribution round, the numbers rarely agree: parcels were planned, dispatched from the warehouse, handed to households and some came back. Finance reviewers need to see what is unaccounted for and why, before they sign anything off.

## Intended user
Distribution coordinators and finance reviewers.

## Technique
Straightforward calculation with rule-based flags (no model). For one site, one parcel item and a period (dates inclusive):

- planned = sum of `distribution_plans.planned_quantity` in the period, cancelled plans excluded
- dispatched = sum of `dispatches.quantity` to the site for the item (by dispatch date)
- distributed = sum of `distributions.parcel_quantity` at the site for the item; every row counts, including flagged ones
- returned = sum of `distribution_returns.quantity` at the site for the item (by return date)
- **unaccounted = dispatched - distributed - returned** (negative means more was handed out than dispatched)

"Not dispatched against plan" (planned - dispatched) is shown separately and is not part of unaccounted. Flags: a household with more than one distribution row for the same item in the period (`duplicate:<household_id>`); a distribution row with no dispatch, or a dispatch for another site or item (`unmatched:<distribution_id>`); and the unaccounted quantity itself (`unaccounted:<item_id>`).

## Inputs
Seeded tables `distribution_plans`, `dispatches`, `distributions`, `distribution_returns`, plus `reconciliation_notes` and `reconciliation_reports` (written by this project). The four figures are read from their own tables, not from `stock_movements`: a return also appears there as a receipt and would be counted twice.

## Assumptions
- Parcels only (`ITM-FP-STD` and `ITM-FP-SML` are reconciled separately, never added together). Ready-to-eat meals are a different unit and are not part of this reconciliation.
- The latest note for a discrepancy decides whether it is resolved.
- Notes are keyed by site, period and discrepancy key (not by item), so a household flagged for both parcel items shares one note thread.
- Sign-off needs a coordinator (or admin) to confirm.

## Limitations
- Matching is by IDs only: a distribution row is "unmatched" if it has no dispatch or its dispatch is for another site or item; quantities are not compared line by line.
- One site, one item and one continuous period per report.
- Local demo mode: a sidebar selector stands in for sign-in.

## Launch
```
python run.py 12
streamlit run projects/12-distribution-reconciliation-report/app.py --server.port 8512
```
Port 8512.

## Screenshot
![Distribution reconciliation page](screenshot.png)

## Try this demo
Dates in the seed are relative to the demo date; the page opens on the seeded scenario.
1. Open the page. The site `SITE-03`, the period (the seven days from 12 to 6 days before the demo date) and item `ITM-FP-STD` are already selected from the seeded scenario. The figures show 100 planned, 100 dispatched, 96 distributed, 3 returned and **1 parcel unaccounted for**. The explanation reads `100 - 96 - 3 = 1`.
2. In the discrepancy list see three flags: `unaccounted:ITM-FP-STD`, one duplicate household `duplicate:HH-.....` (two distribution rows) and one unmatched record `unmatched:DST-.....` (no dispatch recorded). The household and record IDs depend on the demo date; the seeded scenario (`demo.scenarios` → `p12`) names them. With the test anchor (`python -m shared.seed --reset --anchor 2026-11-16T10:00:00`) they are `duplicate:HH-00042` (DST-00501, DST-00580) and `unmatched:DST-00581` (household HH-00341), period 2026-11-04 to 2026-11-10.
3. Add a resolution note to each. Mark the duplicate and the unmatched record resolved; leave the unaccounted parcel unresolved.
4. Choose a coordinator in "Confirming staff member", tick the box and press "Approve report". The report ID is shown with a download button.
5. Download the Markdown: it has the four figures, the formula, each discrepancy with its latest note, and the "Unresolved items" section lists only `unaccounted:ITM-FP-STD`.

## Tests
`tests/test_p12_reconciliation.py`: the 100/96/3 scenario gives 1 unaccounted; zero activity, negative unaccounted, inclusive period boundaries, cancelled plans, other items and sites, the ledger not being used, meals not mixed with parcels; duplicate and unmatched detection on the seed and on hand-built rows; notes (latest decides, audited, validated); approval refused without confirmation, report written with the exact unresolved keys; the app starts without error.
