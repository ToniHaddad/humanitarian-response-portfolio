import sys
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import p16_convoy as p16  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from shared import charts, clock, confirm, db, security, stock, ui  # noqa: E402

ui.page_header(project_number=16)

acting = ui.staff_selector(
    ("coordinator", "warehouse_officer", "field_worker"), key="p16_staff", label="Acting as (planning and checklist)"
)
st.sidebar.caption(f"Demo clock: {clock.now():%Y-%m-%d %H:%M}")
st.sidebar.caption("Vehicle readiness comes from Project 14's rule and blocked areas from Project 15's advisories.")

ERRORS = (p16.PlanError, confirm.ConfirmationRequired, stock.InsufficientStock)
STATUS_KIND = {
    "draft": "warn",
    "planned": "warn",
    "approved": "ok",
    "departed": "ok",
    "arrived": "ok",
    "returned": "ok",
    "cancelled": "bad",
}
READY_KIND = {"ready": "ok", "due_for_service": "warn", "not_ready": "bad"}
READY_LABEL = {"ready": "Ready", "due_for_service": "Due for service", "not_ready": "NOT READY"}

for kind, message in st.session_state.pop("p16_flash", []):
    getattr(st, kind)(message)


def act(fn: Callable[[Any], Any], success: Callable[[Any], str] | str) -> None:
    """Run one domain step in a transaction; on success flash a message and rerun, on refusal show why."""
    try:
        with db.transaction() as conn:
            result = fn(conn)
    except ERRORS as exc:
        st.error(str(exc))
        reasons = getattr(exc, "reasons", None)
        if reasons and len(reasons) > 1:
            st.markdown("\n".join(f"- {r}" for r in reasons))
        return
    msg = success(result) if callable(success) else success
    st.session_state["p16_flash"] = [("success", msg)]
    st.rerun()


with db.transaction() as _c:
    convoys = p16.list_convoys(_c)

# -- convoy choice ------------------------------------------------------------
ui.section("Convoys", "Pick a convoy to plan, approve or track, or create a new one.")
ui.table(
    pd.DataFrame(
        [
            {
                "Convoy": c["convoy_id"],
                "Name": c["name"],
                "Planned date": c["planned_date"],
                "Origin": c["origin_warehouse_id"],
                "Status": c["status"],
                "Coordinator": c["coordinator_id"],
                "Vehicles (count)": c["vehicles"],
                "Stops (count)": c["stops"],
                "Load lines (count)": c["load_lines"],
            }
            for c in convoys
        ]
    )
)
ids = [c["convoy_id"] for c in convoys]
names = {c["convoy_id"]: f"{c['convoy_id']} · {c['name']} ({c['status']}, {c['planned_date']})" for c in convoys}
default_idx = next((i for i, c in enumerate(convoys) if c["status"] == "draft"), 0)
if st.session_state.get("p16_select_new") in ids:
    st.session_state["p16_convoy"] = st.session_state.pop("p16_select_new")
cid = st.selectbox("Convoy", ids, index=default_idx, format_func=lambda c: names[c], key="p16_convoy")

with st.expander("Create a new convoy"):
    warehouses = db.query_all("SELECT warehouse_id, name FROM warehouses ORDER BY warehouse_id")
    coordinators = [s["staff_id"] for s in ui.staff_options(("coordinator",))]
    with st.form("p16_new_convoy"):
        new_name = st.text_input("Name", key="p16_new_name")
        new_date = st.date_input("Planned date", value=clock.today() + timedelta(days=1), min_value=clock.today(), key="p16_new_date")
        new_wh = st.selectbox(
            "Origin warehouse",
            [w["warehouse_id"] for w in warehouses],
            format_func=lambda w: f"{w} · {next(x['name'] for x in warehouses if x['warehouse_id'] == w)}",
        )
        new_crd = st.selectbox("Coordinator", coordinators)
        created = st.form_submit_button("Create draft convoy")
    if created:

        def _create(conn: Any) -> str:
            new_id = p16.create_convoy(conn, new_name, new_date, new_wh, new_crd, acting or new_crd)
            st.session_state["p16_select_new"] = new_id
            return new_id

        act(_create, lambda new_id: f"Created {new_id} as a draft with its pre-departure checklist.")

with db.transaction() as _c:
    cv = p16.get_convoy(_c, cid)
    planned = cv["planned_date"]
    readiness = p16.readiness_on(_c, planned)
    vehicles_all = db.query_all("SELECT * FROM vehicles ORDER BY vehicle_id", conn=_c)
    drivers_all = db.query_all("SELECT * FROM drivers ORDER BY driver_id", conn=_c)
    assigned = p16.assigned_vehicles(_c, cid)
    stops = p16.stop_details(_c, cid)
    lines = p16.load_lines(_c, cid)
    plan = p16.plan_suggestion(_c, cid)
    stock_rows = p16.stock_check(_c, cid)
    on_hand = stock.on_hand(_c, cv["origin_warehouse_id"])
    items = db.query_all("SELECT * FROM items ORDER BY item_id", conn=_c)
    sites = db.query_all(
        "SELECT s.site_id, s.name, s.area_id, a.name_en AS area_name FROM sites s JOIN areas a ON a.area_id = s.area_id ORDER BY s.site_id",
        conn=_c,
    )
    blocked = security.blocked_areas(_c)
    current_adv = security.current_advisories(_c)
    checks = p16.checklist(_c, cid)
    found_blockers = p16.blockers(_c, cid)
    trip_log = p16.events(_c, cid)

editable = cv["status"] in p16.EDITABLE
item_by_id = {i["item_id"]: i for i in items}
st.markdown(
    f"### {cid} · {cv['name']} &nbsp; {ui.badge(cv['status'].upper(), STATUS_KIND.get(cv['status'], 'ok'))}",
    unsafe_allow_html=True,
)
st.markdown(f"Planned date: **{planned}**")
total_kg = round(sum(v["load_kg"] for v in assigned), 2)
ui.metric_row(
    [
        ("Origin warehouse", cv["origin_warehouse_id"]),
        ("Coordinator", cv["coordinator_id"]),
        ("Vehicles (count)", len(assigned)),
        ("Stops (count)", len(stops)),
        ("Total load (kg)", f"{total_kg:g}"),
    ]
)
if not editable:
    st.info(f"This convoy is {cv['status']}: the plan is locked. Use the status section at the bottom to track it.")

# -- 1. vehicles and drivers ------------------------------------------------------
ui.section(
    "1. Vehicles and drivers",
    f"Readiness on the planned date {planned} (rule from Project 14). A vehicle marked not ready cannot be assigned; "
    "due for service is allowed with a warning. Trucks need a class C licence.",
)
ui.table(
    pd.DataFrame(
        [
            {
                "Vehicle": v["vehicle_id"],
                "Type": v["vehicle_type"],
                "Capacity (kg)": v["load_capacity_kg"],
                "Home depot": v["home_warehouse_id"],
                "Readiness": READY_LABEL[readiness[v["vehicle_id"]].status],
                "Why not ready": "; ".join(readiness[v["vehicle_id"]].reasons),
                "Warnings": "; ".join(readiness[v["vehicle_id"]].warnings),
            }
            for v in vehicles_all
        ]
    )
)
if assigned:
    st.markdown("**Assigned to this convoy**")
    for v in assigned:
        line = (
            f"{ui.badge(READY_LABEL[v['readiness']], READY_KIND[v['readiness']])} **{v['vehicle_id']}** ({v['vehicle_type']}, "
            f"{float(v['load_capacity_kg']):g} kg) driven by **{v['driver_id']}** (class {v['licence_class']}, licence to {v['licence_expiry']}); "
            f"load {v['load_kg']:g} kg of {float(v['load_capacity_kg']):g} kg"
        )
        if v["overloaded"]:
            line += " " + ui.badge(f"OVERLOADED by {round(v['load_kg'] - float(v['load_capacity_kg']), 2):g} kg", "bad")
        st.markdown(line, unsafe_allow_html=True)
        for r in v["reasons"]:
            st.error(f"{v['vehicle_id']} not ready: {r}")
        for w in v["warnings"]:
            st.warning(f"{v['vehicle_id']}: {w}")
        for d in v["driver_problems"]:
            st.error(d)
else:
    st.caption("No vehicles assigned yet.")

if editable:
    c1, c2, c3 = st.columns([2, 2, 1])
    veh_labels = {
        v[
            "vehicle_id"
        ]: f"{v['vehicle_id']} · {v['vehicle_type']} · {v['load_capacity_kg']:g} kg · {READY_LABEL[readiness[v['vehicle_id']].status]}"
        for v in vehicles_all
    }
    drv_labels = {d["driver_id"]: f"{d['driver_id']} · class {d['licence_class']} · licence to {d['licence_expiry']}" for d in drivers_all}
    pick_v = c1.selectbox("Vehicle", list(veh_labels), format_func=lambda v: veh_labels[v], key="p16_vehicle")
    pick_d = c2.selectbox("Driver", list(drv_labels), format_func=lambda d: drv_labels[d], key="p16_driver")
    c3.write("")
    if c3.button("Assign vehicle", key="p16_assign", type="primary"):

        def _assign(conn: Any) -> Any:
            return p16.assign_vehicle(conn, cid, pick_v, pick_d, acting or cv["coordinator_id"])

        act(
            _assign,
            lambda r: f"Assigned {pick_v} with {pick_d}." + (f" Warning: {'; '.join(r.warnings)}" if r.warnings else ""),
        )
    if assigned:
        r1, r2 = st.columns([3, 1])
        rm_v = r1.selectbox("Remove a vehicle", [v["vehicle_id"] for v in assigned], key="p16_rm_vehicle")
        r2.write("")
        if r2.button("Remove vehicle", key="p16_rm_vehicle_btn"):
            act(lambda conn: p16.remove_vehicle(conn, cid, rm_v, acting or cv["coordinator_id"]), f"Removed {rm_v}.")

# -- 2. stops ---------------------------------------------------------------------
ui.section(
    "2. Destination stops and the coordinator's order",
    f"Areas with an active advisory at level {security.BLOCKING_LEVEL} ({security.LEVEL_NAMES[security.BLOCKING_LEVEL]}) or higher are blocked (Project 15).",
)
if stops:
    ui.table(
        pd.DataFrame(
            [
                {
                    "Order in use": s["stop_seq"],
                    "Site": s["site_id"],
                    "Name": s["name"],
                    "Area": f"{s['area_id']} {s['area_name']}",
                    "Advisory": s["advisory"],
                    "Blocked": "BLOCKED" if s["blocked"] else "",
                    "Main vehicle": s["vehicle_id"] or "",
                }
                for s in stops
            ]
        )
    )
    for s in stops:
        if s["blocked"]:
            st.error(s["block_reason"] + ". Remove this stop before approval.")
else:
    st.caption("No stops yet.")


def _site_label(site_id: str) -> str:
    s = next(x for x in sites if x["site_id"] == site_id)
    lvl = current_adv.get(s["area_id"], {}).get("final_level")
    tag = f" · advisory {lvl} {security.LEVEL_NAMES[lvl]}" if lvl else ""
    return f"{site_id} · {s['name']} · {s['area_id']} {s['area_name']}{tag}" + (" · BLOCKED" if s["area_id"] in blocked else "")


if editable:
    stop_ids = [s["site_id"] for s in stops]
    free_sites = [s["site_id"] for s in sites if s["site_id"] not in stop_ids]
    a1, a2 = st.columns([3, 1])
    if free_sites:
        add_site = a1.selectbox("Add a destination site", free_sites, format_func=_site_label, key="p16_add_site")
        a2.write("")
        if a2.button("Add stop", key="p16_add_stop"):
            act(lambda conn: p16.add_stop(conn, cid, add_site, acting or cv["coordinator_id"]), f"Added stop {add_site}.")
    if stops:
        b1, b2 = st.columns([3, 1])
        rm_site = b1.selectbox("Remove a stop (its load lines go too)", stop_ids, format_func=_site_label, key="p16_rm_site")
        b2.write("")
        if b2.button("Remove stop", key="p16_rm_stop"):
            act(lambda conn: p16.remove_stop(conn, cid, rm_site, acting or cv["coordinator_id"]), f"Removed stop {rm_site}.")
        with st.form("p16_manual_form"):
            st.markdown(f"Coordinator's manual order (now: {' → '.join(plan['manual_order'])}). Pick every stop in visiting order.")
            new_order = st.multiselect(
                "Manual stop order", stop_ids, default=plan["manual_order"], key=f"p16_manual_{cid}_{'-'.join(plan['manual_order'])}"
            )
            save_order = st.form_submit_button("Save manual order")
        if save_order:
            act(
                lambda conn: p16.set_manual_order(conn, cid, list(new_order), acting or cv["coordinator_id"]),
                lambda km: f"Manual order saved: {' → '.join(new_order)} ({km:g} km).",
            )

# -- 3. load plan -----------------------------------------------------------------
ui.section(
    "3. Load plan",
    f"Stock comes from {cv['origin_warehouse_id']} (on hand = received minus dispatched). Weight = quantity x unit weight. "
    "Over capacity is overloaded; exactly at capacity is fine.",
)
oh = on_hand.merge(pd.DataFrame(items)[["item_id", "name", "unit", "unit_weight_kg"]], on="item_id")
planned_by_item = {r["item_id"]: r["planned"] for r in stock_rows}
oh["planned on this convoy"] = oh["item_id"].map(planned_by_item).fillna(0).astype(int)
ui.table(
    oh[["item_id", "name", "unit", "unit_weight_kg", "on_hand", "planned on this convoy"]].rename(
        columns={
            "item_id": "Item",
            "name": "Name",
            "unit": "Unit",
            "unit_weight_kg": "Unit weight (kg)",
            "on_hand": "On hand (units)",
            "planned on this convoy": "Planned on this convoy (units)",
        }
    )
)
if lines:
    ui.table(
        pd.DataFrame(
            [
                {
                    "Line": ln["load_id"],
                    "Stop": ln["site_id"],
                    "Vehicle": ln["vehicle_id"],
                    "Item": f"{ln['item_id']} {ln['item_name']}",
                    "Quantity": p16.qty_label(int(ln["quantity"]), ln["unit"]),
                    "Weight (kg)": ln["weight_kg"],
                }
                for ln in lines
            ]
        )
    )
if assigned:
    cap_df = pd.DataFrame(
        [
            {
                "Vehicle": v["vehicle_id"],
                "Capacity (kg)": float(v["load_capacity_kg"]),
                "Loaded (kg)": v["load_kg"],
                "Room left (kg)": v["remaining_kg"],
                "Status": "OVERLOADED" if v["overloaded"] else "Within capacity",
            }
            for v in assigned
        ]
    )
    ui.table(cap_df)
    for v in assigned:
        if v["overloaded"]:
            st.error(
                f"{v['vehicle_id']} is overloaded: {v['load_kg']:g} kg on a {float(v['load_capacity_kg']):g} kg vehicle. Approval is blocked."
            )
if editable and stops and assigned:
    with st.form("p16_load_form"):
        l1, l2, l3, l4 = st.columns(4)
        ld_site = l1.selectbox("Stop", [s["site_id"] for s in stops], key="p16_ld_site")
        ld_item = l2.selectbox(
            "Item", list(item_by_id), format_func=lambda i: f"{i} · {item_by_id[i]['name']} ({item_by_id[i]['unit']})", key="p16_ld_item"
        )
        ld_qty = l3.number_input("Quantity (units of the item)", min_value=1, value=10, step=1, key="p16_ld_qty")
        ld_veh = l4.selectbox("Vehicle", [v["vehicle_id"] for v in assigned], key="p16_ld_vehicle")
        add_line = st.form_submit_button("Add load line")
    if add_line:

        def _added(r: dict[str, Any]) -> str:
            v = r["vehicle"]
            msg = f"Added {r['weight_kg']:g} kg to {ld_veh} ({v['load_kg']:g} of {float(v['load_capacity_kg']):g} kg)."
            return msg + (" This vehicle is now OVERLOADED." if v["overloaded"] else "")

        act(lambda conn: p16.add_load(conn, cid, ld_site, ld_item, int(ld_qty), ld_veh, acting or cv["coordinator_id"]), _added)
    if lines:
        d1, d2 = st.columns([3, 1])
        rm_line = d1.selectbox(
            "Remove a load line",
            [ln["load_id"] for ln in lines],
            format_func=lambda i: next(
                f"Line {x['load_id']}: {x['site_id']} {x['item_id']} {x['quantity']} on {x['vehicle_id']}"
                for x in lines
                if x["load_id"] == i
            ),
            key="p16_rm_line",
        )
        d2.write("")
        if d2.button("Remove line", key="p16_rm_line_btn"):
            act(lambda conn: p16.remove_load(conn, cid, int(rm_line), acting or cv["coordinator_id"]), f"Removed load line {rm_line}.")

# -- 4. suggestion ------------------------------------------------------------------
ui.section(
    "4. Suggested stop order and vehicle split",
    "Optimization (nearest-neighbour heuristic): from the warehouse, always drive to the closest remaining stop, then return. "
    "Stops in blocked areas are left out. Vehicle split: first-fit decreasing (heaviest stop first onto the largest vehicle with room). "
    "Distances are on the invented grid with a 1.3 road factor.",
)
if not stops:
    st.caption("Add stops to see a suggestion.")
else:
    ui.metric_row(
        [
            ("Manual order distance (km)", f"{plan['manual_distance_km']:g}"),
            ("Suggested order distance (km)", f"{plan['suggested_distance_km']:g}"),
            ("Difference (km, manual minus suggested)", f"{plan['saving_km']:g}"),
        ]
    )
    st.markdown(f"**Manual order:** {plan['origin_id']} → {' → '.join(plan['manual_order'])} → {plan['origin_id']}")
    st.markdown(
        f"**Suggested order:** {plan['origin_id']} → {' → '.join(plan['suggested_order']) or '(no allowed stops)'} → {plan['origin_id']}"
    )
    if plan["manual_blocked"]:
        st.warning(
            "The manual order includes stops in blocked areas: "
            + ", ".join(plan["manual_blocked"])
            + ". Its distance includes them; the suggestion leaves them out."
        )
    for e in plan["excluded"]:
        st.error("Left out of the suggestion: " + e["reason"])
    if plan["saving_km"] < 0:
        st.info("The manual order is shorter than the nearest-neighbour suggestion here; the heuristic is not always optimal.")
    if plan["split"]:
        ui.table(
            pd.DataFrame(
                [
                    {
                        "Stop": ln["site_id"],
                        "Vehicle": ln["vehicle_id"],
                        "Item": ln["item_id"],
                        "Quantity": p16.qty_label(int(ln["quantity"]), item_by_id[ln["item_id"]]["unit"]),
                        "Weight (kg)": ln["weight_kg"],
                    }
                    for ln in plan["split"]
                ]
            )
        )
        st.caption("Suggested weight per vehicle: " + ", ".join(f"{v} {w:g} kg" for v, w in plan["split_weights"].items()))
        for v in plan["split_overloaded"]:
            st.error(f"Even the suggested split overloads {v}: add a vehicle or reduce the load.")
    elif plan["split_note"]:
        st.warning(plan["split_note"])
    pts = [{"x_km": plan["origin"][0], "y_km": plan["origin"][1], "label": plan["origin_id"], "kind": "Origin warehouse"}]
    for s in stops:
        pts.append({"x_km": s["x_km"], "y_km": s["y_km"], "label": s["site_id"], "kind": "Blocked stop" if s["blocked"] else "Stop"})
    fig = charts.grid_map(pd.DataFrame(pts), label="label", color="kind", title=None, height=440, stagger_labels=True)
    for name, order, dash in (("Manual order", plan["manual_order"], "dot"), ("Suggested order", plan["suggested_order"], "solid")):
        if order:
            route = [plan["origin"], *[plan["positions"][s] for s in order], plan["origin"]]
            fig.add_trace(go.Scatter(x=[p[0] for p in route], y=[p[1] for p in route], mode="lines", name=name, line={"dash": dash}))
    st.plotly_chart(fig, width="stretch", key="p16_map")
    if editable and st.button("Apply the suggested order and split", key="p16_apply"):
        act(
            lambda conn: p16.apply_suggestion(conn, cid, acting or cv["coordinator_id"]),
            lambda r: f"Using the suggested order {' → '.join(r['suggested_order'])} ({r['suggested_distance_km']:g} km) and split.",
        )

# -- 5. checklist ------------------------------------------------------------------
ui.section("5. Pre-departure checklist", "Each tick records the acting staff member (sidebar) and the time.")
if not checks:
    if st.button("Generate checklist", key="p16_gen_check"):
        act(lambda conn: p16.generate_checklist(conn, cid, acting or cv["coordinator_id"]), "Checklist generated.")
else:
    done = sum(1 for r in checks if r["checked"])
    st.caption(f"{done} of {len(checks)} items ticked.")
    can_tick = cv["status"] in ("draft", "planned", "approved")
    for r in checks:
        label = r["label"] + (f" (ticked by {r['checked_by']} at {r['checked_at']})" if r["checked"] else "")
        val = st.checkbox(label, value=bool(r["checked"]), key=f"p16_chk_{cid}_{r['item_key']}", disabled=not can_tick)
        if can_tick and val != bool(r["checked"]):
            act(
                lambda conn, key=r["item_key"], v=val: p16.set_check(conn, cid, key, v, acting or cv["coordinator_id"]),
                f"{'Ticked' if val else 'Unticked'}: {r['label']}.",
            )

# -- 6. approval and status ------------------------------------------------------------
ui.section("6. Approval, dispatch and tracking", "Status: draft or planned → approved → departed → arrived → returned (or cancelled).")
if cv["status"] in ("draft", "planned", "approved"):
    if found_blockers:
        st.error(f"Approval and dispatch are blocked ({len(found_blockers)} reason(s)):")
        st.markdown("\n".join(f"- {b}" for b in found_blockers))
    else:
        st.success(
            "No blockers: every vehicle is ready and within capacity, every stop is open, stock is available and the checklist is complete."
        )

if cv["status"] == "draft" and st.button("Mark as planned", key="p16_mark_planned"):
    act(lambda conn: p16.mark_planned(conn, cid, acting or cv["coordinator_id"]), f"{cid} is now planned.")
if cv["status"] in ("draft", "planned"):
    summary = (
        f"Approve **{cid} {cv['name']}** for {planned} from {cv['origin_warehouse_id']}: {len(assigned)} vehicle(s), "
        f"{len(stops)} stop(s), {total_kg:g} kg. Only a coordinator can approve."
    )
    staff = ui.confirm_action(
        "p16_approve", summary, roles=p16.APPROVER_ROLES, button_label="Approve convoy", default_staff=cv["coordinator_id"]
    )
    if staff:
        act(lambda conn: p16.approve(conn, cid, staff), f"{cid} approved by {staff}.")
if cv["status"] == "approved":
    summary = (
        f"Dispatch **{cid}** now: {len(lines)} load line(s) leave {cv['origin_warehouse_id']} and are recorded in the stock ledger. "
        "Readiness and every blocker are checked again first."
    )
    staff = ui.confirm_action(
        "p16_depart", summary, roles=p16.APPROVER_ROLES, button_label="Dispatch convoy", default_staff=cv["coordinator_id"]
    )
    if staff:
        act(lambda conn: p16.depart(conn, cid, staff), lambda d: f"{cid} departed. Dispatches recorded: {', '.join(d)}.")
if cv["status"] == "departed" and st.button("Mark arrived", key="p16_arrived"):
    act(lambda conn: p16.mark_arrived(conn, cid, acting or cv["coordinator_id"]), f"{cid} arrived.")
if cv["status"] == "arrived" and st.button("Mark returned", key="p16_returned"):
    act(lambda conn: p16.mark_returned(conn, cid, acting or cv["coordinator_id"]), f"{cid} returned.")
if cv["status"] in p16.TRANSITIONS["cancelled"]:
    with st.expander("Cancel this convoy"):
        reason = st.text_input("Reason for cancelling (required)", key="p16_cancel_reason")
        staff = ui.confirm_action(
            "p16_cancel", f"Cancel **{cid}**. Reason: {reason or 'MISSING'}", roles=p16.APPROVER_ROLES, button_label="Cancel convoy"
        )
        if staff:
            act(lambda conn: p16.cancel(conn, cid, staff, reason), f"{cid} cancelled.")

st.markdown("**Trip log (status changes)**")
ui.table(pd.DataFrame(trip_log).rename(columns={"at": "At", "status": "Status", "staff_id": "Staff", "note": "Note"}))
timestamps = {k: cv[k] for k in ("approved_at", "departed_at", "arrived_at", "returned_at") if cv[k]}
if timestamps:
    st.caption(" · ".join(f"{k.replace('_at', '').capitalize()}: {v}" for k, v in timestamps.items()))
dispatches = db.query_df(
    "SELECT dispatch_id, site_id, item_id, quantity, dispatched_at, dispatched_by FROM dispatches WHERE convoy_id = ? ORDER BY dispatch_id",
    (cid,),
)
if not dispatches.empty:
    st.markdown("**Stock dispatched on this convoy (one ledger row each)**")
    ui.table(dispatches)
