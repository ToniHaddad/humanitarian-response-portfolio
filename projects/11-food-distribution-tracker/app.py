import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p11_food as p11  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from shared import clock, confirm, db, food, ui  # noqa: E402

ui.page_header(project_number=11)

TODAY = clock.today()
ITEM_NAMES = {p11.STANDARD_PARCEL: "Standard food parcel", p11.SMALL_PARCEL: "Small food parcel", p11.RTE_MEAL: "Ready-to-eat meal"}
EDIT_PREFIX = "p11_edit_"

conn = db.connect()

# -- flash message from the previous run (set just before st.rerun) --------------
flash = st.session_state.pop("p11_flash", None)
if flash:
    kind, text = flash
    (st.success if kind == "ok" else st.error)(text)

# -- sidebar ------------------------------------------------------------------------
acting = ui.staff_selector(("field_worker", "coordinator"), key="p11_staff", label="Acting as (recording distributions)")
sites = p11.food_sites(conn)
site_names = {s["site_id"]: f"{s['site_id']} · {s['name']}" for s in sites}
site_id = st.sidebar.selectbox("Distribution site", list(site_names), format_func=lambda s: site_names[s], key="p11_site")
mode = st.sidebar.radio(
    "Assumptions used in the tables",
    ["Saved values", "Edited values (what-if)"],
    key="p11_mode",
    help="Section 4 always shows both side by side. Recording and saved plans always use the saved values.",
)
st.sidebar.caption(f"Demo clock: {clock.now():%Y-%m-%d %H:%M}")

# -- assumptions: saved values and the edited (what-if) values ----------------------
saved = food.load_assumptions(conn)
rows = p11.assumption_rows(conn)
for r in rows:
    st.session_state.setdefault(EDIT_PREFIX + r["assumption_key"], float(r["value"]))
edited = dict(saved)
for r in rows:
    edited[r["assumption_key"]] = float(st.session_state[EDIT_PREFIX + r["assumption_key"]])
in_use = edited if mode.startswith("Edited") else saved
in_use_label = "edited (what-if) assumptions" if mode.startswith("Edited") else "saved assumptions"


def finish(kind: str, text: str) -> None:
    st.session_state["p11_flash"] = (kind, text)
    conn.close()
    st.rerun()


def show_date(value: date | None, empty: str = "no parcels recorded") -> str:
    return value.isoformat() if value else empty


# ==================================================================================
ui.section(
    "1. Record a distribution",
    f"Site: {site_names[site_id]} (change it in the sidebar). Parcel coverage (the expected days of coverage that is saved) "
    "uses the saved assumptions and is shown before you save. Parcels are counted in parcels, ready-to-eat meals in meals; "
    "meal coverage is shown separately and never added to parcel coverage.",
)
households = p11.active_households(conn)
at_site_ids = {hc.household_id for hc in p11.households_at_site(conn, site_id, saved)}
households.sort(key=lambda h: (h["household_id"] not in at_site_ids, h["household_id"]))
hh_by_id = {h["household_id"]: h for h in households}
hid = st.selectbox(
    "Household (households of this site first; type to search)",
    list(hh_by_id),
    format_func=lambda h: f"{h} · {hh_by_id[h]['head_name']} · {hh_by_id[h]['household_size']} people · {hh_by_id[h]['area_id']}",
    key="p11_rec_household",
)
c1, c2, c3, c4, c5 = st.columns(5)
dist_date = c1.date_input("Distribution date", value=TODAY, max_value=TODAY, key="p11_rec_date")
size = int(
    c2.number_input(
        "Household size (people)", min_value=1, max_value=30, value=int(hh_by_id[hid]["household_size"]), key=f"p11_rec_size_{hid}"
    )
)
default_item, default_qty = food.parcel_plan(size, saved)
item = c3.radio(
    "Food parcel type",
    list(p11.PARCEL_ITEMS),
    index=list(p11.PARCEL_ITEMS).index(default_item),
    format_func=lambda i: ITEM_NAMES[i],
    key=f"p11_rec_item_{hid}",
)
qty = int(c4.number_input("Parcel quantity (parcels)", min_value=0, max_value=20, value=default_qty, key=f"p11_rec_qty_{hid}"))
meals = int(c5.number_input("Ready-to-eat meals (meals)", min_value=0, max_value=500, value=0, step=1, key=f"p11_rec_meals_{hid}"))
dispatches = p11.dispatch_options(conn, site_id, item, dist_date) if qty > 0 else []
dispatch_labels = {
    d["dispatch_id"]: f"{d['dispatch_id']} · {d['quantity']} parcels from {d['warehouse_id']} at {d['dispatched_at']}" for d in dispatches
}
dispatch_id = st.selectbox(
    "Dispatch these parcels came from (optional)",
    [None, *dispatch_labels],
    format_func=lambda d: "Not linked to a dispatch" if d is None else dispatch_labels[d],
    key=f"p11_rec_dispatch_{site_id}_{item}_{dist_date}",
    help="Dispatches of this parcel type to this site on the distribution day or the evening before. Recording never moves stock.",
)
preview = p11.preview_distribution(dist_date, size, item, qty, meals, saved)
ui.metric_row(
    [
        (
            "Parcel coverage (days)",
            preview["parcel_days"],
            "parcels x person-days per parcel / people, rounded down. Saved as the expected days of coverage.",
        ),
        (
            "Parcel coverage ends",
            show_date(preview["parcel_coverage_end"], "no parcels"),
            "the distribution day counts as day 1; meals never extend it",
        ),
        (
            "Meal coverage (days, separate)",
            preview["meal_days"],
            "meals / (people x meals per person per day), rounded down. Shown separately, never added to parcel days.",
        ),
    ]
)
if st.button("Record distribution", type="primary", key="p11_record"):
    try:
        with db.transaction() as wconn:
            res = p11.record_distribution(
                wconn,
                distribution_date=dist_date,
                site_id=site_id,
                household_id=hid,
                household_size=size,
                parcel_item_id=item,
                parcel_quantity=qty,
                rte_meal_quantity=meals,
                recorded_by=acting or "",
                dispatch_id=dispatch_id,
            )
    except ValueError as exc:
        st.error(f"Not recorded: {exc}")
    else:
        finish(
            "ok",
            f"Recorded {res['distribution_id']} for {hid}: {qty} parcels and {meals} meals. "
            f"Parcel coverage {res['expected_coverage_days']} days, ending {show_date(res['coverage_end'], 'no parcels')}; "
            f"meal coverage {res['meal_days']} days (separate).",
        )

# ==================================================================================
ui.section(
    "2. Households at this site and when their parcel coverage ends",
    f"Parcel coverage end = the latest end date over each household's parcel distributions, recomputed from the stored "
    f"quantities with the {in_use_label}. Ready-to-eat meals never extend it; their days are shown in a separate column. "
    "'Stored parcel days' is the value saved when the distribution was recorded. Days left: 0 means parcel coverage ends "
    "today, a negative number means it ended that many days ago. A household belongs to the site of its most recent "
    "distribution, or to the site serving its area if it has none. Click a column header to sort.",
)
at_site = p11.households_at_site(conn, site_id, in_use)
hh_table = pd.DataFrame(
    [
        {
            "Household": hc.household_id,
            "Head of household": hc.head_name,
            "Size (people)": hc.household_size,
            "Distributions": hc.distributions,
            "Last distribution": show_date(hc.last_distribution_date),
            "Stored parcel days (at recording)": hc.last_stored_days,
            "Parcel days (assumptions in use)": hc.last_recomputed_days,
            "Parcel coverage ends": show_date(hc.coverage_end),
            "Days left": hc.days_left(TODAY),
            "Meal days (last distribution, separate)": hc.last_meal_days,
            "Site from": hc.site_source,
        }
        for hc in sorted(at_site, key=lambda h: (h.coverage_end or date.min, h.household_id))
    ]
)
ui.metric_row(
    [
        ("Households at this site", len(at_site)),
        ("Parcel coverage already ended", sum(1 for hc in at_site if hc.coverage_end is None or hc.coverage_end < TODAY)),
        (
            "Parcel coverage ends in the next 7 days",
            sum(1 for hc in at_site if hc.coverage_end and 0 <= (hc.coverage_end - TODAY).days <= 7),
        ),
    ]
)
ui.table(hh_table, height=320, key="p11_households")

# ==================================================================================
ui.section("3. Forecast for a date range", f"For {site_names[site_id]}, with the {in_use_label}.")
f1, f2 = st.columns(2)
win_start = f1.date_input("From", value=TODAY + timedelta(days=1), key="p11_win_start")
win_end = f2.date_input("To", value=TODAY + timedelta(days=7), key="p11_win_end")
if win_end < win_start:
    st.error("The end date is before the start date. Showing the default window: tomorrow to tomorrow + 6 days.")
    win_start, win_end = TODAY + timedelta(days=1), TODAY + timedelta(days=7)
st.markdown(
    f"""
**Rule.** A household is **due** in the window {win_start} to {win_end} when its **parcel coverage** end date is
**before {win_end}**, so its parcels run out on or before the last day of the window. Households whose parcel coverage
already ended (or who have no parcels recorded) are included; a household whose parcel coverage ends on {win_end} itself
still has food for the whole window and is not due. Ready-to-eat meals do not delay this.

- **Parcels** (unit: parcels, shared rule `food.parcel_plan`): households of
  {in_use["small_parcel_max_household_size"]:g} people or fewer get 1 small parcel; larger households get one standard
  parcel per {in_use["persons_per_standard_parcel"]:g} people, rounded up.
- **Ready-to-eat meals** (unit: meals): only for due households registered in the last {p11.NEW_ARRIVAL_DAYS} days (new arrivals),
  {in_use["rte_meals_per_person_per_visit"]:g} meals per person.
- Parcels and meals are different units and are never added together.
"""
)
fc_in_use = p11.forecast(conn, site_id, win_start, win_end, in_use)
ui.metric_row(
    [
        ("Households due (households)", fc_in_use.households_due),
        ("Standard parcels (parcels)", fc_in_use.standard_parcels),
        ("Small parcels (parcels)", fc_in_use.small_parcels),
        ("All parcels (parcels)", fc_in_use.parcels_total, "standard + small parcels; meals are not included"),
        ("Ready-to-eat meals (meals)", fc_in_use.rte_meals, "a separate unit, never added to parcels"),
    ]
)


def due_status(end: date | None) -> str:
    if end is None:
        return "no parcels recorded"
    return "already ended" if end < TODAY else "ends before the window's last day"


ui.table(
    pd.DataFrame(
        [
            {
                "Household": d.household.household_id,
                "Size (people)": d.household.household_size,
                "Parcel coverage ends": show_date(d.household.coverage_end),
                "Status": due_status(d.household.coverage_end),
                "Parcel type": ITEM_NAMES[d.parcel_item_id],
                "Parcels (parcels)": d.parcels,
                "New arrival": "yes" if d.new_arrival else "no",
                "Ready-to-eat meals (meals)": d.rte_meals,
            }
            for d in fc_in_use.due
        ],
        columns=[
            "Household",
            "Size (people)",
            "Parcel coverage ends",
            "Status",
            "Parcel type",
            "Parcels (parcels)",
            "New arrival",
            "Ready-to-eat meals (meals)",
        ],
    ),
    height=300,
    key="p11_due",
)
plans_in_window = p11.existing_plans(conn, site_id, win_start, win_end)
if plans_in_window:
    st.info(
        "Plans already exist for this site in the window. They are part of the warehouse forecast in Project 10; "
        "check them before planning these households again."
    )
    ui.table(pd.DataFrame(plans_in_window), key="p11_existing_plans")

# ==================================================================================
ui.section(
    "4. Assumptions and what-if",
    "Edit the values and press 'Show the effect' to compare the forecast with the saved and the edited values. "
    "Saving them changes every forecast, so it needs a coordinator's confirmation.",
)
ui.table(
    pd.DataFrame(rows).rename(
        columns={
            "assumption_key": "Assumption",
            "value": "Saved value",
            "unit": "Unit",
            "description": "Meaning",
            "updated_at": "Updated at",
            "updated_by": "Updated by",
        }
    ),
    key="p11_assumptions",
)


def reset_edits() -> None:
    # Runs as a callback before the next script run, so it opens its own connection.
    for r in db.query_all("SELECT assumption_key, value FROM forecast_assumptions"):
        st.session_state[EDIT_PREFIX + r["assumption_key"]] = float(r["value"])


with st.form("p11_whatif"):
    cols = st.columns(len(rows))
    for col, r in zip(cols, rows, strict=True):
        key = r["assumption_key"]
        col.number_input(
            f"{key} ({r['unit']})",
            min_value=0.0 if key in p11.NON_NEGATIVE_ASSUMPTIONS else 0.5,
            step=10.0 if key.startswith("person_days.") else 1.0,
            key=EDIT_PREFIX + key,
        )
    st.form_submit_button("Show the effect")
st.button("Reset edits to the saved values", on_click=reset_edits, key="p11_reset")

fc_saved = p11.forecast(conn, site_id, win_start, win_end, saved)
fc_edited = p11.forecast(conn, site_id, win_start, win_end, edited)
st.markdown(f"**Forecast for {site_names[site_id]}, {win_start} to {win_end}: saved and edited assumptions**")
ui.table(
    pd.DataFrame(p11.compare_forecasts(fc_saved, fc_edited)).rename(
        columns={"measure": "Measure", "unit": "Unit", "saved": "Saved values", "edited": "Edited values", "change": "Change"}
    ),
    key="p11_compare",
)
site_hh = sorted(hc.household_id for hc in p11.households_at_site(conn, site_id, saved))
if site_hh:
    due_first = [d.household.household_id for d in fc_saved.due]
    pick = st.selectbox(
        "Household to compare",
        site_hh,
        index=site_hh.index(due_first[0]) if due_first else 0,
        key=f"p11_whatif_household_{site_id}",
    )
    change = p11.household_coverage_change(conn, pick, saved, edited)
    shift = change["shift_days"]
    ui.metric_row(
        [
            ("Parcel coverage ends (saved)", show_date(change["saved_end"])),
            ("Parcel coverage ends (edited)", show_date(change["edited_end"])),
            ("Change (days)", "n/a" if shift is None else f"{shift:+d}"),
            (
                "Due in the window (saved / edited)",
                " / ".join("yes" if p11.is_due(change[k], win_end) else "no" for k in ("saved_end", "edited_end")),
            ),
        ]
    )

changes = {k: v for k, v in edited.items() if k in {r["assumption_key"] for r in rows} and v != saved[k]}
if changes:
    summary = "Save these assumptions? Every forecast and coverage end date will use them.\n\n" + "\n".join(
        f"- `{k}`: {saved[k]:g} → {v:g}" for k, v in changes.items()
    )
    staff = ui.confirm_action("p11_assumptions", summary, roles=p11.ASSUMPTION_ROLES, button_label="Save assumptions")
    if staff:
        try:
            with db.transaction() as wconn:
                saved_keys = p11.save_assumptions(wconn, changes, confirmed_by=staff)
        except (ValueError, confirm.ConfirmationRequired) as exc:
            st.error(f"Not saved: {exc}")
        else:
            finish("ok", f"Saved {len(saved_keys)} assumption(s), confirmed by {staff}: {', '.join(saved_keys)}.")
else:
    st.caption("The edited values equal the saved values: nothing to save.")

# ==================================================================================
ui.section(
    "5. Save the forecast as a planned distribution",
    "Writes one planned row per item (standard parcels and small parcels in parcels, ready-to-eat meals in meals) for "
    "the warehouse forecast in Project 10, using the saved assumptions. A plan that already exists for the same site, "
    "item and date is refused, so nothing is counted twice.",
)
planned_date = st.date_input("Planned distribution date", value=max(win_start, TODAY), min_value=TODAY, key="p11_plan_date")
lines = p11.plan_lines(fc_saved)
if lines:
    ui.table(
        pd.DataFrame(
            [
                {
                    "Item": ITEM_NAMES[ln["item_id"]],
                    "Item ID": ln["item_id"],
                    "Planned quantity": ln["planned_quantity"],
                    "Unit": ln["unit"],
                    "Households": ln["planned_households"],
                }
                for ln in lines
            ]
        ),
        key="p11_plan_lines",
    )
    summary = (
        f"Save a plan for **{site_names[site_id]}** on **{planned_date}** from warehouse "
        f"**{p11.SITE_WAREHOUSE[site_id]}**: "
        + "; ".join(f"{ln['planned_quantity']} {ln['unit']} of {ln['item_id']}" for ln in lines)
        + f" ({fc_saved.households_due} households due, {win_start} to {win_end})."
    )
    staff = ui.confirm_action("p11_plan", summary, roles=p11.PLAN_ROLES, button_label="Save as planned distribution")
    if staff:
        try:
            with db.transaction() as wconn:
                ids = p11.save_plan(wconn, fc_saved, planned_date, confirmed_by=staff)
        except (ValueError, confirm.ConfirmationRequired) as exc:
            st.error(f"Not saved: {exc}")
        else:
            finish("ok", f"Saved plan rows {', '.join(ids)} for {site_id} on {planned_date}, confirmed by {staff}.")
else:
    st.caption("No household is due at this site in the window: nothing to plan.")

conn.close()
