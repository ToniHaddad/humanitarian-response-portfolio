"""Parcel and ready-to-eat meal coverage (used by the seed, Projects 11, 12, 17, 18).

Units are kept apart: parcels are counted in parcels, ready-to-eat meals in meals.
They are converted to days of coverage separately and never added as quantities.
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta

# Default assumptions; the editable values live in the forecast_assumptions table.
DEFAULT_ASSUMPTIONS: dict[str, tuple[float, str, str]] = {
    "person_days.ITM-FP-STD": (150.0, "person-days per parcel", "Food in one standard parcel: 5 people for 30 days."),
    "person_days.ITM-FP-SML": (60.0, "person-days per parcel", "Food in one small parcel: 2 people for 30 days."),
    "meals_per_person_per_day": (3.0, "meals per person per day", "Ready-to-eat meals one person eats in a day."),
    "persons_per_standard_parcel": (5.0, "people per parcel", "Household members one standard parcel is planned for."),
    "rte_meals_per_person_per_visit": (6.0, "meals per person", "Ready-to-eat meals given per household member when meals are included."),
}


def load_assumptions(conn: sqlite3.Connection) -> dict[str, float]:
    values = {k: v[0] for k, v in DEFAULT_ASSUMPTIONS.items()}
    for row in conn.execute("SELECT assumption_key, value FROM forecast_assumptions"):
        values[row[0]] = float(row[1])
    return values


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


def coverage_days(
    household_size: int,
    parcel_quantity: int,
    rte_meal_quantity: int,
    person_days_per_parcel: float,
    meals_per_person_per_day: float,
) -> int:
    """Expected days of food coverage: parcel days, then meal days on top.

    The two units are converted to days separately; only the days are added.
    """
    return parcel_coverage_days(household_size, parcel_quantity, person_days_per_parcel) + meal_coverage_days(
        household_size, rte_meal_quantity, meals_per_person_per_day
    )


def coverage_end_date(distribution_date: date, days: int) -> date:
    """Last day the food is expected to last (distribution day counts as day 1)."""
    return distribution_date + timedelta(days=max(days, 1) - 1)


def standard_parcels_for(household_size: int, persons_per_parcel: float = 5.0) -> int:
    """Standard parcels a household is planned for: one per persons_per_parcel people, rounded up."""
    return max(1, math.ceil(household_size / persons_per_parcel))
