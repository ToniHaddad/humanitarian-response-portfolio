"""Parcel and ready-to-eat meal coverage (used by the seed, Projects 10, 11, 12, 17, 18).

Units are kept apart: parcels are counted in parcels, ready-to-eat meals in meals.
Parcel coverage (days) comes from parcels only. Meal coverage (days) is a separate
figure. The two are never added, as quantities or as days.

The editable source of every assumption is the forecast_assumptions table
(load_assumptions). items.person_days_per_unit is not used.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from typing import NamedTuple

# Default assumptions; the editable values live in the forecast_assumptions table.
DEFAULT_ASSUMPTIONS: dict[str, tuple[float, str, str]] = {
    "person_days.ITM-FP-STD": (150.0, "person-days per parcel", "Food in one standard parcel: 5 people for 30 days."),
    "person_days.ITM-FP-SML": (60.0, "person-days per parcel", "Food in one small parcel: 2 people for 30 days."),
    "meals_per_person_per_day": (3.0, "meals per person per day", "Ready-to-eat meals one person eats in a day."),
    "persons_per_standard_parcel": (5.0, "people per parcel", "Household members one standard parcel is planned for."),
    "rte_meals_per_person_per_visit": (6.0, "meals per person", "Ready-to-eat meals given per household member when meals are included."),
    "small_parcel_max_household_size": (
        2.0,
        "people",
        "Households of up to this many people get one small parcel instead of standard parcels.",
    ),
}

STANDARD_PARCEL = "ITM-FP-STD"
SMALL_PARCEL = "ITM-FP-SML"


class Coverage(NamedTuple):
    """Days of food from one distribution, per unit. Never add the two fields."""

    parcel_days: int  # from parcels only: this is the household's parcel coverage
    meal_days: int  # from ready-to-eat meals only, shown separately


def load_assumptions(conn: sqlite3.Connection) -> dict[str, float]:
    values = {k: v[0] for k, v in DEFAULT_ASSUMPTIONS.items()}
    for row in conn.execute("SELECT assumption_key, value FROM forecast_assumptions"):
        values[row[0]] = float(row[1])
    return values


def default_assumptions() -> dict[str, float]:
    return {k: v[0] for k, v in DEFAULT_ASSUMPTIONS.items()}


def person_days_per_parcel(assumptions: dict[str, float], parcel_item_id: str) -> float:
    """Person-days of food in one parcel of this item, from the assumptions."""
    key = f"person_days.{parcel_item_id}"
    if key not in assumptions:
        raise ValueError(f"no person-days assumption for parcel item {parcel_item_id}")
    return float(assumptions[key])


def parcel_coverage_days(household_size: int, parcel_quantity: int, person_days_per_parcel: float) -> int:
    """Whole days a household's parcels last: parcels x person-days / people."""
    if household_size <= 0:
        raise ValueError("household_size must be positive")
    if parcel_quantity < 0 or person_days_per_parcel < 0:
        raise ValueError("quantities must not be negative")
    return math.floor(parcel_quantity * person_days_per_parcel / household_size)


def meal_coverage_days(household_size: int, rte_meal_quantity: int, meals_per_person_per_day: float) -> int:
    """Whole days a household's ready-to-eat meals last: meals / (people x meals per day)."""
    if household_size <= 0:
        raise ValueError("household_size must be positive")
    if rte_meal_quantity < 0 or meals_per_person_per_day <= 0:
        raise ValueError("invalid meal quantities")
    return math.floor(rte_meal_quantity / (household_size * meals_per_person_per_day))


def coverage(
    household_size: int,
    parcel_item_id: str | None,
    parcel_quantity: int,
    rte_meal_quantity: int,
    assumptions: dict[str, float],
) -> Coverage:
    """Parcel coverage and meal coverage of one distribution, kept separate.

    parcel_days is what distributions.expected_coverage_days stores.
    """
    parcel_days = 0
    if parcel_quantity:
        if not parcel_item_id:
            raise ValueError("parcel_item_id is required when parcel_quantity > 0")
        parcel_days = parcel_coverage_days(household_size, parcel_quantity, person_days_per_parcel(assumptions, parcel_item_id))
    meal_days = meal_coverage_days(household_size, rte_meal_quantity, assumptions["meals_per_person_per_day"])
    return Coverage(parcel_days=parcel_days, meal_days=meal_days)


def coverage_end_date(distribution_date: date, days: int) -> date:
    """Last day the food is expected to last (distribution day counts as day 1)."""
    return distribution_date + timedelta(days=max(days, 1) - 1)


def standard_parcels_for(household_size: int, persons_per_parcel: float = 5.0) -> int:
    """Standard parcels a household is planned for: one per persons_per_parcel people, rounded up."""
    return max(1, math.ceil(household_size / persons_per_parcel))


def parcel_plan(household_size: int, assumptions: dict[str, float]) -> tuple[str, int]:
    """The one parcel-size rule (seed, Project 11 forecast and plans, so Project 10's demand).

    Households of up to small_parcel_max_household_size people get one small parcel;
    larger households get one standard parcel per persons_per_standard_parcel people,
    rounded up. Returns (parcel item_id, quantity in parcels).
    """
    if household_size <= 0:
        raise ValueError("household_size must be positive")
    if household_size <= assumptions["small_parcel_max_household_size"]:
        return SMALL_PARCEL, 1
    return STANDARD_PARCEL, standard_parcels_for(household_size, assumptions["persons_per_standard_parcel"])
