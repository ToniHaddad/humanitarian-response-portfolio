"""Project 11 logic: record food distributions, household coverage end dates, the
due-for-support forecast per site, editable assumptions and planned distributions.

Units are kept apart everywhere: parcels are counted in parcels (standard and small),
ready-to-eat meals in meals. Every coverage figure comes from shared.food.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from shared import clock, confirm, db, food
from shared.seed_content import SITE_CATCHMENT, SITE_WAREHOUSE

CHANNEL = "app:11"
STANDARD_PARCEL = food.STANDARD_PARCEL
SMALL_PARCEL = food.SMALL_PARCEL
RTE_MEAL = "ITM-RTE"
PARCEL_ITEMS: tuple[str, ...] = (STANDARD_PARCEL, SMALL_PARCEL)
NEW_ARRIVAL_DAYS = 60  # households registered this recently also get ready-to-eat meals
PLAN_ROLES: tuple[str, ...] = ("coordinator",)
ASSUMPTION_ROLES: tuple[str, ...] = ("coordinator",)

AREA_SITE: dict[str, str] = {area: site for site, areas in SITE_CATCHMENT.items() for area in areas}


# -- reference data ------------------------------------------------------------
def food_sites(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Distribution sites that serve a household catchment (the only sites food is handed out at)."""
    rows = conn.execute("SELECT site_id, name, area_id FROM sites ORDER BY site_id").fetchall()
    return [dict(r) for r in rows if r["site_id"] in SITE_CATCHMENT]


def active_households(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT household_id, head_name, household_size, area_id, registered_at FROM households WHERE status = 'active' ORDER BY household_id"
    ).fetchall()
    return [dict(r) for r in rows]


def assumption_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT assumption_key, value, unit, description, updated_at, updated_by FROM forecast_assumptions ORDER BY assumption_key"
    ).fetchall()
    return [dict(r) for r in rows]


# -- coverage (always through shared.food) --------------------------------------
def preview_distribution(
    distribution_date: date,
    household_size: int,
    parcel_item_id: str | None,
    parcel_quantity: int,
    rte_meal_quantity: int,
    assumptions: dict[str, float],
) -> dict[str, Any]:
    """What the page shows before saving.

    Parcel coverage (days and end date) comes from parcels only; it is what
    expected_coverage_days stores. Meal coverage is a separate figure and never
    extends the parcel coverage.
    """
    item = parcel_item_id if parcel_quantity else None
    cov = food.coverage(household_size, item, parcel_quantity, rte_meal_quantity, assumptions)
    return {
        "parcel_days": cov.parcel_days,
        "meal_days": cov.meal_days,
        "expected_coverage_days": cov.parcel_days,
        "parcel_coverage_end": food.coverage_end_date(distribution_date, cov.parcel_days) if parcel_quantity else None,
        "meal_coverage_end": food.coverage_end_date(distribution_date, cov.meal_days) if cov.meal_days else None,
    }


# -- household coverage -----------------------------------------------------------
@dataclass(frozen=True)
class HouseholdCoverage:
    household_id: str
    head_name: str
    household_size: int
    area_id: str
    registered_on: date
    forecast_site_id: str | None
    site_source: str  # "last distribution", "catchment" or "none"
    distributions: int
    last_distribution_id: str | None
    last_distribution_date: date | None
    last_stored_days: int | None  # expected_coverage_days (parcel days) saved at recording time
    last_recomputed_days: int | None  # parcel days of the same distribution, with the assumptions in use
    last_meal_days: int | None  # meal days of the same distribution (separate figure, never added)
    coverage_end: date | None  # latest parcel coverage end (None: no parcels recorded)

    def days_left(self, today: date) -> int | None:
        """Days of parcel food left after today (0: coverage ends today; negative: ended that many days ago)."""
        return None if self.coverage_end is None else (self.coverage_end - today).days


def household_coverage(conn: sqlite3.Connection, assumptions: dict[str, float]) -> dict[str, HouseholdCoverage]:
    """Parcel coverage end date and forecasting site for every active household.

    Parcel coverage end = the latest food.coverage_end_date(distribution_date, parcel_days)
    over the household's distributions with parcels, where parcel_days is recomputed from
    the stored quantities with the given assumptions. Ready-to-eat meals never extend it.
    Site = site of the most recent distribution, else the site whose catchment holds the
    household's area.
    """
    households = active_households(conn)
    dist_rows = conn.execute(
        """SELECT d.distribution_id, d.distribution_date, d.site_id, d.household_id, d.household_size,
                  d.parcel_item_id, d.parcel_quantity, d.rte_meal_quantity, d.expected_coverage_days
           FROM distributions d JOIN households h ON h.household_id = d.household_id
           WHERE h.status = 'active'
           ORDER BY d.distribution_date, d.recorded_at, d.distribution_id"""
    ).fetchall()
    by_household: dict[str, list[sqlite3.Row]] = {}
    for row in dist_rows:
        by_household.setdefault(row["household_id"], []).append(row)

    result: dict[str, HouseholdCoverage] = {}
    for h in households:
        rows = by_household.get(h["household_id"], [])
        end: date | None = None
        last = rows[-1] if rows else None
        last_cov: food.Coverage | None = None
        for row in rows:
            item = row["parcel_item_id"] if row["parcel_quantity"] else None
            cov = food.coverage(row["household_size"], item, row["parcel_quantity"], row["rte_meal_quantity"], assumptions)
            if row["parcel_quantity"] > 0:  # a meals-only distribution gives no parcel coverage
                row_end = food.coverage_end_date(date.fromisoformat(row["distribution_date"]), cov.parcel_days)
                end = row_end if end is None else max(end, row_end)
            if row is last:
                last_cov = cov
        if last is not None:
            site, source = last["site_id"], "last distribution"
        elif h["area_id"] in AREA_SITE:
            site, source = AREA_SITE[h["area_id"]], "catchment"
        else:
            site, source = None, "none"
        result[h["household_id"]] = HouseholdCoverage(
            household_id=h["household_id"],
            head_name=h["head_name"],
            household_size=h["household_size"],
            area_id=h["area_id"],
            registered_on=date.fromisoformat(h["registered_at"][:10]),
            forecast_site_id=site,
            site_source=source,
            distributions=len(rows),
            last_distribution_id=last["distribution_id"] if last else None,
            last_distribution_date=date.fromisoformat(last["distribution_date"]) if last else None,
            last_stored_days=last["expected_coverage_days"] if last else None,
            last_recomputed_days=last_cov.parcel_days if last_cov else None,
            last_meal_days=last_cov.meal_days if last_cov else None,
            coverage_end=end,
        )
    return result


def households_at_site(conn: sqlite3.Connection, site_id: str, assumptions: dict[str, float]) -> list[HouseholdCoverage]:
    return [hc for hc in household_coverage(conn, assumptions).values() if hc.forecast_site_id == site_id]


# -- forecast ----------------------------------------------------------------------
def is_due(coverage_end: date | None, window_end: date) -> bool:
    """Due when the parcel food runs out on or before the window's last day: parcel coverage end before window end.

    A household with no parcels recorded (no parcel coverage end) is due. Meals never count here.
    """
    return coverage_end is None or coverage_end < window_end


@dataclass(frozen=True)
class DueHousehold:
    household: HouseholdCoverage
    parcel_item_id: str
    parcels: int  # unit: parcels
    new_arrival: bool
    rte_meals: int  # unit: meals


@dataclass(frozen=True)
class Forecast:
    """Households due at one site in a window and what they need.

    Parcels (standard and small, unit: parcels) and ready-to-eat meals (unit: meals)
    are separate fields. No field adds parcels and meals together.
    """

    site_id: str
    window_start: date
    window_end: date
    due: list[DueHousehold] = field(default_factory=list)

    @property
    def households_due(self) -> int:
        return len(self.due)

    @property
    def standard_parcels(self) -> int:
        return sum(d.parcels for d in self.due if d.parcel_item_id == STANDARD_PARCEL)

    @property
    def small_parcels(self) -> int:
        return sum(d.parcels for d in self.due if d.parcel_item_id == SMALL_PARCEL)

    @property
    def parcels_total(self) -> int:
        """Standard plus small parcels, both counted in parcels. Meals are never included."""
        return self.standard_parcels + self.small_parcels

    @property
    def rte_meals(self) -> int:
        return sum(d.rte_meals for d in self.due)

    def households_for(self, item_id: str) -> int:
        if item_id == RTE_MEAL:
            return sum(1 for d in self.due if d.rte_meals > 0)
        return sum(1 for d in self.due if d.parcel_item_id == item_id)

    def summary(self) -> dict[str, int]:
        """Headline figures, each in its own unit (see SUMMARY_UNITS)."""
        return {
            "households_due": self.households_due,
            "standard_parcels": self.standard_parcels,
            "small_parcels": self.small_parcels,
            "parcels_total": self.parcels_total,
            "rte_meals": self.rte_meals,
        }

    def item_quantities(self) -> dict[str, int]:
        """Quantity per item in that item's unit: parcels for the two parcel items, meals for ITM-RTE."""
        return {STANDARD_PARCEL: self.standard_parcels, SMALL_PARCEL: self.small_parcels, RTE_MEAL: self.rte_meals}


SUMMARY_UNITS: dict[str, str] = {
    "households_due": "households",
    "standard_parcels": "parcels",
    "small_parcels": "parcels",
    "parcels_total": "parcels",
    "rte_meals": "meals",
}
SUMMARY_LABELS: dict[str, str] = {
    "households_due": "Households due",
    "standard_parcels": "Standard parcels",
    "small_parcels": "Small parcels",
    "parcels_total": "All parcels (standard + small)",
    "rte_meals": "Ready-to-eat meals",
}


def is_new_arrival(registered_on: date, today: date) -> bool:
    return registered_on >= today - timedelta(days=NEW_ARRIVAL_DAYS)


def forecast(
    conn: sqlite3.Connection,
    site_id: str,
    window_start: date,
    window_end: date,
    assumptions: dict[str, float],
    today: date | None = None,
) -> Forecast:
    """Households at the site due for support in [window_start, window_end] and the parcels and meals they need."""
    if window_end < window_start:
        raise ValueError("The window end must not be before its start.")
    today = today or clock.today()
    due: list[DueHousehold] = []
    for hc in sorted(households_at_site(conn, site_id, assumptions), key=lambda h: (h.coverage_end or date.min, h.household_id)):
        if not is_due(hc.coverage_end, window_end):
            continue
        item, parcels = food.parcel_plan(hc.household_size, assumptions)
        new = is_new_arrival(hc.registered_on, today)
        meals = int(round(hc.household_size * assumptions["rte_meals_per_person_per_visit"])) if new else 0
        due.append(DueHousehold(household=hc, parcel_item_id=item, parcels=parcels, new_arrival=new, rte_meals=meals))
    return Forecast(site_id=site_id, window_start=window_start, window_end=window_end, due=due)


def compare_forecasts(saved: Forecast, edited: Forecast) -> list[dict[str, Any]]:
    """One row per measure, each in its own unit: saved value, edited value and the change."""
    s, e = saved.summary(), edited.summary()
    return [
        {"measure": SUMMARY_LABELS[k], "unit": SUMMARY_UNITS[k], "saved": s[k], "edited": e[k], "change": e[k] - s[k]}
        for k in ("households_due", "standard_parcels", "small_parcels", "rte_meals")
    ]


def household_coverage_change(
    conn: sqlite3.Connection, household_id: str, saved: dict[str, float], edited: dict[str, float]
) -> dict[str, Any]:
    """A household's coverage end with the saved and the edited assumptions."""
    before = household_coverage(conn, saved).get(household_id)
    after = household_coverage(conn, edited).get(household_id)
    if before is None or after is None:
        raise ValueError(f"{household_id} is not an active household.")
    shift = None
    if before.coverage_end and after.coverage_end:
        shift = (after.coverage_end - before.coverage_end).days
    return {
        "household_id": household_id,
        "saved_end": before.coverage_end,
        "edited_end": after.coverage_end,
        "shift_days": shift,
        "saved_last_days": before.last_recomputed_days,
        "edited_last_days": after.last_recomputed_days,
    }


# -- recording a distribution ------------------------------------------------------
def dispatch_options(conn: sqlite3.Connection, site_id: str, item_id: str, distribution_date: date) -> list[dict[str, Any]]:
    """Dispatches of this item to this site on the distribution day or the evening before."""
    rows = conn.execute(
        """SELECT dispatch_id, warehouse_id, quantity, dispatched_at FROM dispatches
           WHERE site_id = ? AND item_id = ? AND date(dispatched_at) BETWEEN ? AND ?
           ORDER BY dispatched_at, dispatch_id""",
        (site_id, item_id, (distribution_date - timedelta(days=1)).isoformat(), distribution_date.isoformat()),
    ).fetchall()
    return [dict(r) for r in rows]


def record_distribution(
    conn: sqlite3.Connection,
    *,
    distribution_date: date,
    site_id: str,
    household_id: str,
    household_size: int,
    parcel_item_id: str | None,
    parcel_quantity: int,
    rte_meal_quantity: int,
    recorded_by: str,
    dispatch_id: str | None = None,
) -> dict[str, Any]:
    """Write one distributions row; expected_coverage_days = parcel days with the saved assumptions.

    Meal days are returned for display but never stored in or added to the parcel coverage.
    Call inside db.transaction() (next_id needs the write lock). Routine data entry: no staff confirmation, but the acting staff is recorded and audited.
    It never creates dispatches or stock movements (stock is Project 10's ledger).
    """
    if not recorded_by or conn.execute("SELECT 1 FROM staff WHERE staff_id = ? AND active = 1", (recorded_by,)).fetchone() is None:
        raise ValueError("Choose the acting staff member before recording.")
    if site_id not in SITE_CATCHMENT:
        raise ValueError(f"{site_id} is not a food distribution site.")
    hh = conn.execute("SELECT status FROM households WHERE household_id = ?", (household_id,)).fetchone()
    if hh is None or hh["status"] != "active":
        raise ValueError(f"{household_id} is not an active household.")
    if distribution_date > clock.today():
        raise ValueError("A distribution cannot be recorded for a future date.")
    if household_size <= 0:
        raise ValueError("Household size must be at least 1.")
    if parcel_quantity < 0 or rte_meal_quantity < 0:
        raise ValueError("Quantities must not be negative.")
    if parcel_quantity == 0 and rte_meal_quantity == 0:
        raise ValueError("Enter at least one parcel or one ready-to-eat meal.")
    if parcel_quantity > 0 and parcel_item_id not in PARCEL_ITEMS:
        raise ValueError("Choose the parcel type: standard or small food parcel.")
    item = parcel_item_id if parcel_quantity > 0 else None
    if dispatch_id is not None:
        allowed = {d["dispatch_id"] for d in dispatch_options(conn, site_id, item or "", distribution_date)}
        if dispatch_id not in allowed:
            raise ValueError(f"{dispatch_id} is not a dispatch of this parcel type to {site_id} for {distribution_date}.")
    same_day = conn.execute(
        "SELECT distribution_id FROM distributions WHERE household_id = ? AND distribution_date = ?",
        (household_id, distribution_date.isoformat()),
    ).fetchone()
    if same_day is not None:
        raise ValueError(
            f"{household_id} already has distribution {same_day[0]} on {distribution_date}. "
            "Recording it again would count the same distribution twice."
        )

    assumptions = food.load_assumptions(conn)
    cov = food.coverage(household_size, item, parcel_quantity, rte_meal_quantity, assumptions)
    days = cov.parcel_days
    dist_id = db.next_id(conn, "distributions", "distribution_id", "DST-", 5)
    row = {
        "distribution_id": dist_id,
        "distribution_date": distribution_date.isoformat(),
        "site_id": site_id,
        "household_id": household_id,
        "household_size": household_size,
        "parcel_item_id": item,
        "parcel_quantity": parcel_quantity,
        "rte_meal_quantity": rte_meal_quantity,
        "expected_coverage_days": days,
        "dispatch_id": dispatch_id,
        "recorded_by": recorded_by,
        "recorded_at": clock.iso(),
    }
    conn.execute(
        f"INSERT INTO distributions({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
        list(row.values()),
    )
    db.audit(
        conn,
        actor_id=recorded_by,
        channel=CHANNEL,
        action="record_distribution",
        entity_table="distributions",
        entity_id=dist_id,
        after=row,
    )
    return dict(
        row,
        meal_days=cov.meal_days,
        coverage_end=food.coverage_end_date(distribution_date, days) if parcel_quantity else None,
    )


# -- editable assumptions ---------------------------------------------------------
# Zero is allowed for these (0 meals per visit; 0 = no household gets a small parcel); others must be > 0.
NON_NEGATIVE_ASSUMPTIONS: frozenset[str] = frozenset({"rte_meals_per_person_per_visit", "small_parcel_max_household_size"})


def validate_assumption(key: str, value: float) -> None:
    if key in NON_NEGATIVE_ASSUMPTIONS:
        if value < 0:
            raise ValueError(f"{key} must not be negative.")
    elif value <= 0:
        raise ValueError(f"{key} must be greater than zero.")


def save_assumptions(conn: sqlite3.Connection, new_values: dict[str, float], confirmed_by: str | None) -> list[str]:
    """Save edited assumptions (changes every forecast): needs a coordinator's confirmation. Returns the changed keys."""
    staff = confirm.require_confirmation(conn, confirmed_by, allowed_roles=ASSUMPTION_ROLES)
    current = {r["assumption_key"]: r for r in assumption_rows(conn)}
    changed: list[str] = []
    for key, value in new_values.items():
        if key not in current:
            raise ValueError(f"Unknown assumption: {key}")
        validate_assumption(key, float(value))
    for key, value in new_values.items():
        before = float(current[key]["value"])
        if float(value) == before:
            continue
        conn.execute(
            "UPDATE forecast_assumptions SET value = ?, updated_at = ?, updated_by = ? WHERE assumption_key = ?",
            (float(value), clock.iso(), staff, key),
        )
        db.audit(
            conn,
            actor_id=staff,
            channel=CHANNEL,
            action="update_assumption",
            entity_table="forecast_assumptions",
            entity_id=key,
            before={"value": before},
            after={"value": float(value)},
            reason="Forecast assumption edited and confirmed in Project 11",
        )
        changed.append(key)
    return changed


# -- planned distributions (feed Project 10) ----------------------------------------
def existing_plans(conn: sqlite3.Connection, site_id: str, start: date, end: date) -> list[dict[str, Any]]:
    """Plans (not cancelled) for this site in the window, so a forecast is not planned twice."""
    rows = conn.execute(
        """SELECT plan_id, planned_date, item_id, planned_quantity, planned_households, status FROM distribution_plans
           WHERE site_id = ? AND planned_date BETWEEN ? AND ? AND status != 'cancelled'
           ORDER BY planned_date, item_id""",
        (site_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [dict(r) for r in rows]


def plan_lines(fc: Forecast) -> list[dict[str, Any]]:
    """Rows a plan would write: one per item with quantity > 0, each in its own unit."""
    units = {STANDARD_PARCEL: "parcels", SMALL_PARCEL: "parcels", RTE_MEAL: "meals"}
    return [
        {"item_id": item, "planned_quantity": qty, "unit": units[item], "planned_households": fc.households_for(item)}
        for item, qty in fc.item_quantities().items()
        if qty > 0
    ]


def save_plan(conn: sqlite3.Connection, fc: Forecast, planned_date: date, confirmed_by: str | None) -> list[str]:
    """Write the forecast as planned distributions for Project 10. Refuses a plan that already exists."""
    staff = confirm.require_confirmation(conn, confirmed_by, allowed_roles=PLAN_ROLES)
    if planned_date < clock.today():
        raise ValueError("The planned date cannot be in the past.")
    lines = plan_lines(fc)
    if not lines:
        raise ValueError("Nothing to plan: no household is due at this site in the window.")
    for line in lines:
        dup = conn.execute(
            """SELECT plan_id, status FROM distribution_plans
               WHERE site_id = ? AND item_id = ? AND planned_date = ? AND status != 'cancelled'""",
            (fc.site_id, line["item_id"], planned_date.isoformat()),
        ).fetchone()
        if dup is not None:
            raise ValueError(
                f"{dup['plan_id']} ({dup['status']}) already plans {line['item_id']} at {fc.site_id} on {planned_date}. "
                "Nothing was saved, so the same distribution is not counted twice."
            )
    warehouse = SITE_WAREHOUSE[fc.site_id]
    ids: list[str] = []
    for line in lines:
        plan_id = db.next_id(conn, "distribution_plans", "plan_id", "PLN-", 4)
        row = {
            "plan_id": plan_id,
            "site_id": fc.site_id,
            "warehouse_id": warehouse,
            "item_id": line["item_id"],
            "planned_date": planned_date.isoformat(),
            "planned_quantity": line["planned_quantity"],
            "planned_households": line["planned_households"],
            "status": "planned",
            "created_by": staff,
        }
        conn.execute(f"INSERT INTO distribution_plans({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))
        db.audit(
            conn,
            actor_id=staff,
            channel=CHANNEL,
            action="create_plan",
            entity_table="distribution_plans",
            entity_id=plan_id,
            after=dict(row, unit=line["unit"], window=f"{fc.window_start}..{fc.window_end}"),
            reason="Planned from the Project 11 forecast",
        )
        ids.append(plan_id)
    return ids
