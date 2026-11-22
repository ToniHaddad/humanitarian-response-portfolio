# 10 · Warehouse stock forecast

## Problem

A warehouse officer must know what is on hand, how fast it is leaving, and when each item will run out, early enough to reorder before the delivery lead time makes it too late. This tool tracks stock received, dispatched and on hand by warehouse and item, and uses recent demand, planned distributions and delivery lead times to estimate stockout dates and suggest replenishment quantities.

## Intended user

Warehouse and supply officers (and coordinators who approve stock movements).

## Technique

Straightforward calculation: a day-by-day projection of the stock balance. No statistical model.

## Inputs

- The stock ledger `stock_movements` (read through `shared.stock` / view `v_stock_on_hand`): the only source of on-hand stock.
- `distribution_plans` (written by Project 11): planned quantity per warehouse, item and date.
- `item_lead_times`: lead time, safety days and reorder threshold per warehouse and item.
- Visible inputs in the sidebar: warehouse, recent-demand window (default 28 days) and forecast horizon (default 90 days).

## Assumptions

- **Recent demand per day** = (ledger `dispatched` minus `received` with reason `distribution_return`, over the window of days before today) divided by the window length. Never below zero. Movements dated today are not in the window.
- **Planned distributions** = plans with status `planned`, planned date today or later, and no dispatch row. Each plan's quantity is demand on its date at its warehouse for its item.
- **No double counting** (a planned distribution is the same activity as the demand it will cause): for a warehouse-item pair, from today up to and including its last planned date the daily demand is the planned quantities only (zero on days without a plan). After the last planned date, and for pairs with no plans, the recent daily demand is used.
- **Stockout date** = first day within the horizon on which the projected balance goes below zero (that day's demand cannot be fully met). A balance of exactly zero is not a stockout. Otherwise "None within N days".
- **Reorder by** = stockout date minus the lead time. "ORDER NOW / OVERDUE" when that date is today or earlier.
- **Suggested replenishment** = projected demand over (lead time + safety days + 28 days) minus on hand, never below zero, rounded up to the next 10.
- **Below threshold** = on hand lower than the item's reorder threshold.
- Every quantity is in its item's unit (parcel, meal, kit, piece, pack). Different items are never added together and a chart shows one item at a time.

## Limitations

- Demand outside planned distributions is the flat recent average; no seasonality, trend or uncertainty range.
- Stock already on order is not tracked (there is no purchase-order table), so suggested replenishment does not subtract incoming deliveries.
- A dispatch against a plan marks the whole plan dispatched even if the dispatched quantity differs from the planned one.
- Receipts are limited to the reasons `supplier_delivery` and `transfer_in`. Returns (Project 12) and write-offs are not entered here.
- No extra library beyond the portfolio's `requirements.txt`.

## Launch

```
python run.py 10
streamlit run projects/10-warehouse-stock-forecast/app.py --server.port 8510
```

Port 8510.

## Screenshot

![Warehouse stock forecast](screenshot.png)

## Try this demo

Start from freshly seeded data (`python -m shared.seed --reset`). The seed dates plans and movements relative to the seed time, so the calendar dates on the page differ from day to day; the steps below therefore use "today" (the date in the sidebar line "Today in the demo") and changes, never calendar dates. Pick warehouse **WH-01 · Central Depot** in the sidebar and look at the item *Standard food parcel* (`ITM-FP-STD`, unit parcel). Note its starting values: call the on-hand figure H and the stockout date S.

1. **Received minus dispatched equals on hand.** In the stock table, on the `ITM-FP-STD` row, on hand equals Received minus Dispatched (on the standard seed 623 - 470 = 153; these totals do not change with the seed date).
2. **Stockout date and replenishment.** In the forecast table, same row: the stockout date is a date a few weeks after today (on the standard seed today + 29 days), the reorder-by date is the stockout date minus the lead time (21 days, shown in the row), planned in horizon is the sum of the open plans for this item and warehouse dated today or later (`PLN-0052` and `PLN-0058`, 70 + 80 = 150 parcels, on dates about 19 and 28 days from today), recent demand is about 3 parcels per day, and a suggested replenishment is shown (80 on the standard seed). The row is not flagged below threshold while on hand is at or above the reorder threshold shown in the row (153 against 120). The "How this is calculated" box explains each column. The chart (item *Standard food parcel*) shows the balance falling in steps on the plan dates, then sloping down at the recent rate until it crosses zero; its caption repeats the stockout and reorder-by dates.
3. **Record a receipt.** Under "Record a receipt" choose *Standard food parcel*, quantity 100, reason `supplier_delivery`, choose `WHO-01` (a warehouse officer; coordinators are accepted too, other roles are not offered) and press "Confirm receipt" once without the tick: "Tick the confirmation box first. Nothing was changed." Then tick the box and press it again. Expected: a green message; on hand becomes H + 100 and Received rises by 100 (Dispatched unchanged); the new `received` movement is at the top of the ledger list; the stockout date moves later than S (on the standard seed to today + 62 days, reorder-by to today + 41 days) and the suggested replenishment falls (to 0 on the standard seed).
4. **Dispatch against a plan (optional).** Under "Record a dispatch" choose plan `PLN-0052` (70 parcels to its site), tick the box and press "Confirm dispatch". Expected: a green message with the new dispatch ID; on hand drops by 70 and Dispatched rises by 70; the plan is marked dispatched and disappears from the plan list; planned in horizon for the item drops by 70 (150 to 80 on the standard seed) because a dispatched plan is no longer planned demand.

## Tests

`tests/test_p10_stock_forecast.py` (run `python -m pytest tests/test_p10_stock_forecast.py`) covers: stock balances by warehouse and item (received minus dispatched equals on hand from the view and from the stock table; dispatch rows equal ledger dispatches); recent demand (net of returns, window edges, today excluded, never negative); plan selection (status, date, already-dispatched plans excluded); the no-double-counting rule; stockout date with a hand-built known answer, none-in-horizon and exact-zero cases; reorder-by and the order-now flag; replenishment rounding; below-threshold flag; a future plan changing the forecast; receipts and dispatches (on hand changes, plan marked dispatched, audit rows, refused without confirmation or for the wrong role, dispatch beyond on hand refused); and the app starting and recording a receipt and a dispatch through its confirmation forms.
