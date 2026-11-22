import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p10_forecast as fc  # noqa: E402
import streamlit as st  # noqa: E402

from shared import charts, clock, db, stock, ui  # noqa: E402
from shared.confirm import ConfirmationRequired  # noqa: E402

ui.page_header(project_number=10)

today = clock.today()
conn = db.connect()

# -- sidebar inputs -------------------------------------------------------
whs = fc.warehouses(conn)
wh_labels = {w["warehouse_id"]: f"{w['warehouse_id']} · {w['name']}" for w in whs}
warehouse_id = st.sidebar.selectbox("Warehouse", list(wh_labels), format_func=lambda k: wh_labels[k])
window_days = int(st.sidebar.number_input("Recent demand window (days)", min_value=7, max_value=90, value=fc.DEFAULT_WINDOW_DAYS, step=1))
horizon_days = int(st.sidebar.number_input("Forecast horizon (days)", min_value=14, max_value=365, value=fc.DEFAULT_HORIZON_DAYS, step=1))
st.sidebar.caption(f"Today in the demo: {today.isoformat()}")

if "p10_flash" in st.session_state:
    st.success(st.session_state.pop("p10_flash"))

item_rows = fc.items(conn)
item_names = {i["item_id"]: i["name"] for i in item_rows}
item_units = {i["item_id"]: i["unit"] for i in item_rows}

# -- stock table -----------------------------------------------------------
ui.section(
    f"Stock in {wh_labels[warehouse_id]}",
    "Received and dispatched come from the stock ledger; on hand = received minus dispatched. Each row is counted in its own unit.",
)
stock_df = fc.stock_table(conn, warehouse_id)
ui.table(
    stock_df.rename(
        columns={
            "item": "Item",
            "received": "Received",
            "dispatched": "Dispatched",
            "on_hand": "On hand",
            "unit": "Unit",
        }
    )[["Item", "Unit", "On hand", "Received", "Dispatched"]]
)

# -- forecast table --------------------------------------------------------
ui.section(
    "Stockout forecast and replenishment",
    f"{wh_labels[warehouse_id]}: demand window {window_days} days, horizon {horizon_days} days.",
)
forecast_df = fc.forecast_table(conn, warehouse_id, window_days=window_days, horizon_days=horizon_days)
urgent = int((forecast_df["action"] == "ORDER NOW / OVERDUE").sum())
below = int(forecast_df["below_threshold"].sum())
ui.metric_row(
    [
        ("Items to order now or overdue", urgent, "Reorder-by date is today or earlier. A count of items, not a quantity."),
        ("Items below reorder threshold", below, "On hand is lower than the item's reorder threshold. A count of items."),
    ]
)
show = forecast_df.rename(
    columns={
        "item": "Item",
        "unit": "Unit",
        "on_hand": "On hand",
        "recent_daily_demand": "Demand (units/day)",
        "planned_in_horizon": "Planned (units)",
        "stockout_date": "Stockout date",
        "lead_time_days": "Lead time (days)",
        "reorder_by": "Reorder by",
        "suggested_replenishment": "Replenish (units)",
        "below_threshold": "Below threshold",
        "reorder_threshold": "Threshold (units)",
        "action": "Flag",
    }
)[
    [
        "Item",
        "Flag",
        "Unit",
        "On hand",
        "Threshold (units)",
        "Below threshold",
        "Demand (units/day)",
        "Planned (units)",
        "Stockout date",
        "Reorder by",
        "Lead time (days)",
        "Replenish (units)",
    ]
]
ui.table(show)
with st.expander("How this is calculated", expanded=False):
    st.markdown(
        f"""
- **On hand** is read from the stock ledger only (received minus dispatched).
- **Recent demand per day** = (dispatched minus returns received back, over the last **{window_days}** days
  before today) divided by {window_days}. It is in the item's own unit per day.
- **Planned in horizon** = planned distributions (status *planned*, dated today or later, not yet dispatched)
  for this warehouse and item that fall inside the horizon. Plans come from Project 11.
- **No double counting**: from today up to and including the item's last planned date, daily demand is the
  planned quantities only (zero on days without a plan). After the last planned date, and for items with no plans,
  the recent daily demand is used.
- **Stockout date** = the first day the projected balance goes below zero within **{horizon_days}** days
  (that day's demand cannot be fully met). Otherwise "None within {horizon_days} days".
- **Reorder by** = stockout date minus the item's lead time. The flag says *ORDER NOW / OVERDUE* when that date is today or earlier.
- **Suggested replenishment** = projected demand over (lead time + safety days + 28 days) minus on hand,
  never below zero, rounded up to the next 10 units.
- **Below threshold** = on hand is lower than the item's reorder threshold.
"""
    )

# -- projected balance chart ----------------------------------------------
ui.section("Projected balance for one item", "One item at a time, so the axis always has a single unit.")
chart_item = st.selectbox(
    "Item",
    list(item_names),
    index=list(item_names).index("ITM-FP-STD") if "ITM-FP-STD" in item_names else 0,
    format_func=lambda k: f"{item_names[k]} ({item_units[k]})",
    key="p10_chart_item",
)
pair = fc.forecast_pairs(conn, warehouse_id, chart_item, window_days, horizon_days, today)
if pair:
    f = pair[0]
    proj = fc.balance_projection(f, horizon_days, today)
    ytitle = fc.axis_title(f.item, f.unit)
    fig = charts.line(proj, "date", "projected_balance", "Date", f"Projected balance: {ytitle}", markers=False)
    fig.add_hline(y=0, line_dash="dot", line_color="#D55E00")
    fig.add_hline(y=f.reorder_threshold, line_dash="dash", line_color="#E69F00", annotation_text="Reorder threshold")
    st.plotly_chart(fig, width="stretch")
    if f.stockout_date:
        st.caption(
            f"Stockout date {f.stockout_date.isoformat()}; reorder by {f.reorder_by.isoformat()} "  # type: ignore[union-attr]
            f"(lead time {f.lead_time_days} days). Suggested replenishment {f.suggested_replenishment} {fc.plural_unit(f.unit)}."
        )
    else:
        st.caption(f"No stockout within {horizon_days} days. Suggested replenishment {f.suggested_replenishment} {fc.plural_unit(f.unit)}.")
else:
    st.info("No lead-time record for this warehouse and item.")

# -- recent ledger ----------------------------------------------------------
ui.section(f"Recent stock movements in {wh_labels[warehouse_id]}", "Latest 25 ledger rows.")
ledger = fc.recent_ledger(conn, warehouse_id)
ui.table(
    ledger.rename(
        columns={
            "moved_at": "When",
            "item": "Item",
            "movement_type": "Type",
            "reason": "Reason",
            "quantity": "Quantity",
            "unit": "Unit",
            "reference": "Reference",
            "recorded_by": "Recorded by",
        }
    )[["When", "Item", "Type", "Quantity", "Unit", "Reason", "Reference", "Recorded by"]]
)

# -- forms ------------------------------------------------------------------
left, right = st.columns(2)

with left:
    ui.section("Record a receipt", "Stock arriving at this warehouse. Needs staff confirmation.")
    r_item = st.selectbox("Item received", list(item_names), format_func=lambda k: f"{item_names[k]} ({item_units[k]})", key="p10_r_item")
    r_qty = int(
        st.number_input(f"Quantity received ({fc.plural_unit(item_units[r_item])})", min_value=1, value=100, step=10, key="p10_r_qty")
    )
    r_reason = st.selectbox("Reason", list(fc.RECEIPT_REASONS), key="p10_r_reason")
    r_ref = st.text_input("Reference (delivery note number, optional)", key="p10_r_ref") or None
    staff = ui.confirm_action(
        "receipt",
        f"Record receipt of **{r_qty} {fc.plural_unit(item_units[r_item])}** of {item_names[r_item]} at {wh_labels[warehouse_id]}.",
        roles=fc.ALLOWED_ROLES,
        button_label="Confirm receipt",
    )
    if staff:
        try:
            with db.transaction() as c:
                fc.receive_stock(c, warehouse_id, r_item, r_qty, confirmed_by=staff, reason=r_reason, reference=r_ref)
            st.session_state["p10_flash"] = f"Receipt recorded: {r_qty} {fc.plural_unit(item_units[r_item])} of {item_names[r_item]}."
            st.rerun()
        except (ConfirmationRequired, ValueError) as exc:
            st.error(str(exc))

with right:
    ui.section("Record a dispatch", "Stock leaving this warehouse for a site. Needs staff confirmation.")
    plans = fc.open_plans(conn, warehouse_id=warehouse_id, today=today)
    plan_labels = {"": "No plan (ad hoc dispatch)"}
    for pl in plans.itertuples():
        plan_labels[pl.plan_id] = (
            f"{pl.plan_id} · {pl.planned_date} · {pl.site_id} · {item_names[pl.item_id]} · {pl.planned_quantity} {fc.plural_unit(item_units[pl.item_id])}"
        )
    plan_id = st.selectbox("Planned distribution", list(plan_labels), format_func=lambda k: plan_labels[k], key="p10_d_plan")
    if plan_id:
        pl = plans[plans["plan_id"] == plan_id].iloc[0]
        d_item, d_site, default_qty = str(pl["item_id"]), str(pl["site_id"]), int(pl["planned_quantity"])
        st.caption(f"Item {item_names[d_item]} and site {d_site} come from the plan.")
    else:
        d_item = st.selectbox(
            "Item dispatched", list(item_names), format_func=lambda k: f"{item_names[k]} ({item_units[k]})", key="p10_d_item"
        )
        site_rows = fc.sites(conn)
        site_labels = {s["site_id"]: f"{s['site_id']} · {s['name']}" for s in site_rows}
        d_site = st.selectbox("Destination site", list(site_labels), format_func=lambda k: site_labels[k], key="p10_d_site")
        default_qty = 50
    d_qty = int(
        st.number_input(
            f"Quantity dispatched ({fc.plural_unit(item_units[d_item])})",
            min_value=1,
            value=max(1, default_qty),
            step=10,
            key=f"p10_d_qty_{plan_id or 'adhoc'}",
        )
    )
    st.caption(f"On hand now: {stock.on_hand_qty(conn, warehouse_id, d_item)} {fc.plural_unit(item_units[d_item])}.")
    staff_d = ui.confirm_action(
        "dispatch",
        f"Dispatch **{d_qty} {fc.plural_unit(item_units[d_item])}** of {item_names[d_item]} from {wh_labels[warehouse_id]} to {d_site}"
        + (f", against plan {plan_id}." if plan_id else "."),
        roles=fc.ALLOWED_ROLES,
        button_label="Confirm dispatch",
    )
    if staff_d:
        try:
            with db.transaction() as c:
                new_id = fc.dispatch_stock(c, warehouse_id, d_site, d_item, d_qty, confirmed_by=staff_d, plan_id=plan_id or None)
            st.session_state["p10_flash"] = f"Dispatch {new_id} recorded: {d_qty} {fc.plural_unit(item_units[d_item])} to {d_site}."
            st.rerun()
        except (ConfirmationRequired, ValueError, stock.InsufficientStock) as exc:
            st.error(str(exc))

conn.close()
