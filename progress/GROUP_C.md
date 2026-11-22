# Group C progress (needs and supply chain)

Director: Group C. Database copy: `data/directorC.db`. Round 2 review done on a freshly seeded copy.

| # | Folder | Built | Tests pass | Walkthrough checked | Screenshot | Notes | Not verified | Next step |
|---|--------|-------|------------|---------------------|------------|-------|--------------|-----------|
| 09 | unmet-needs-map | yes | yes (13) | yes: 125 / 49 / 76 / 5 not counted, Riverbend 18-6=12 (frozen anchor 2026-11-16) | retaken, full page, current | area totals only, invented grid, gap explained on page | none | none |
| 10 | warehouse-stock-forecast | yes | yes (30) | yes: WH-01 ITM-FP-STD 623 - 470 = 153; receipt of 100 gives 253, stockout +62 days | retaken, current seed | README numbers refreshed to current seed | none | none |
| 11 | food-distribution-tracker | yes | yes (24) | yes: HH-00037 coverage ends today+4; due next week (28 due); after recording 30 days, ends today+29, not due (27 due); edit 150 -> 120 gives 28 -> 33 due | retaken, current seed | README coverage rule and numbers fixed to parcels-only model; recording a distribution now needs staff confirmation | none | none |
| 12 | distribution-reconciliation-report | yes | yes (18) | yes: 100 / 100 / 96 / 3 = 1 unaccounted; duplicate HH-00042, unmatched DST-00581; notes, sign-off, Markdown with unresolved list | retaken, current seed | none | none | none |

## Gate (Phase 2)
- [x] All four meet the definition of done
- [x] Stock balances reconcile (24 warehouse-item pairs: received - dispatched = on hand, no mismatch; dispatches 8706 = ledger dispatched 8706; returns 314 = ledger distribution_return receipts 314)
- [x] Project 11 walkthrough gives its expected result
- [x] Project 12 walkthrough gives 1 parcel unaccounted for
- Full suite: 581 passed; `ruff check .` and `ruff format --check .` clean.
