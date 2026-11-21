"""Shared helpers: coverage, readiness, staleness, advisory rule, confirmation, classifier, ports, registry."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest
from conftest import ANCHOR

from shared import classifier, confirm, db, fleet, food, geo, ports, security
from shared.projects import GROUPS, all_projects

# -- food coverage ---------------------------------------------------------


def test_coverage_depends_on_household_size() -> None:
    assert food.parcel_coverage_days(5, 1, 150) == 30
    assert food.parcel_coverage_days(3, 1, 150) == 50
    assert food.parcel_coverage_days(8, 2, 150) == 37


def test_coverage_with_edited_assumption() -> None:
    assert food.coverage_days(5, 1, 0, 150, 3) == 30
    assert food.coverage_days(5, 1, 0, 100, 3) == 20  # parcel edited to 100 person-days


def test_meals_converted_to_days_separately() -> None:
    # 30 meals for 5 people at 3 meals a day = 2 days; added as days, not as quantities.
    assert food.meal_coverage_days(5, 30, 3) == 2
    assert food.coverage_days(5, 1, 30, 150, 3) == 32


def test_coverage_end_date_counts_distribution_day() -> None:
    assert food.coverage_end_date(date(2026, 11, 1), 30) == date(2026, 11, 30)


def test_coverage_rejects_bad_household_size() -> None:
    with pytest.raises(ValueError):
        food.parcel_coverage_days(0, 1, 150)


# -- fleet -----------------------------------------------------------------

BASE_VEHICLE = dict(
    vehicle_id="VEH-T",
    last_service_date="2026-10-01",
    service_interval_days=180,
    last_service_odometer=10000,
    service_interval_km=10000,
    odometer_km=12000,
    registration_expiry="2027-06-01",
    insurance_expiry="2027-06-01",
    permit_expiry="2027-06-01",
)


def test_vehicle_ready() -> None:
    assert fleet.assess_vehicle(BASE_VEHICLE, [], date(2026, 11, 16)).status == "ready"


def test_vehicle_overdue_by_days_is_not_ready_with_reason() -> None:
    r = fleet.assess_vehicle(dict(BASE_VEHICLE, last_service_date="2026-05-01"), [], date(2026, 11, 16))
    assert r.status == "not_ready" and "Service overdue" in r.reasons[0]


def test_vehicle_overdue_by_km_and_expired_documents() -> None:
    r = fleet.assess_vehicle(dict(BASE_VEHICLE, odometer_km=20000, insurance_expiry="2026-11-15"), [], date(2026, 11, 16))
    assert r.status == "not_ready" and len(r.reasons) == 2


def test_grounding_defect_blocks_but_minor_does_not() -> None:
    d = [{"defect_id": "D1", "severity": "grounding", "description": "brakes", "resolved_at": None}]
    assert fleet.assess_vehicle(BASE_VEHICLE, d, date(2026, 11, 16)).status == "not_ready"
    d = [{"defect_id": "D2", "severity": "minor", "description": "latch", "resolved_at": None}]
    assert fleet.assess_vehicle(BASE_VEHICLE, d, date(2026, 11, 16)).status == "ready"


def test_due_for_service_soon_is_still_ready() -> None:
    r = fleet.assess_vehicle(dict(BASE_VEHICLE, last_service_date="2026-05-25"), [], date(2026, 11, 16))
    assert r.status == "due_for_service" and r.ready


def test_stale_position_at_limit() -> None:
    now = datetime(2026, 11, 16, 10, 0)
    assert not fleet.is_stale(now - timedelta(minutes=15), now, 15)  # exactly at the limit: not stale
    assert fleet.is_stale(now - timedelta(minutes=15, seconds=1), now, 15)
    assert not fleet.is_stale(now - timedelta(minutes=14), now, 15)
    assert fleet.is_stale(None, now, 15)


def test_record_position_rejects_outside_grid(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        fleet.record_position(conn, "VEH-01", 120.0, 10.0)
    assert fleet.record_position(conn, "VEH-01", 40.0, 50.0) > 0


# -- security --------------------------------------------------------------


def _inc(hours: float, severity: int, status: str = "verified") -> dict:
    return {"occurred_at": (ANCHOR - timedelta(hours=hours)).isoformat(), "severity": severity, "verification_status": status}


def test_advisory_levels_at_thresholds() -> None:
    assert security.level_for_score(2.9) == 0
    assert security.level_for_score(3) == 1
    assert security.level_for_score(6) == 2
    assert security.level_for_score(12) == 3


def test_unverified_and_old_incidents_are_ignored() -> None:
    assert security.suggest_level([_inc(5, 5, "unverified"), _inc(10, 5, "discounted")], ANCHOR) == 0
    assert security.suggest_level([_inc(24 * 8, 5)], ANCHOR) == 0
    assert security.advisory_score([_inc(10, 4), _inc(72, 4)], ANCHOR) == 6.0


def test_override_needs_reason_in_database(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""INSERT INTO advisories(advisory_id, area_id, suggested_level, final_level, is_override, override_reason,
                        rule_basis_json, decided_by, decided_at) VALUES ('ADV-T','AR-01',1,0,1,'  ','{}','DO-01','2026-11-16T10:00:00')""")


# -- confirmation ----------------------------------------------------------


def test_confirmation_required(conn: sqlite3.Connection) -> None:
    with pytest.raises(confirm.ConfirmationRequired):
        confirm.require_confirmation(conn, None)
    with pytest.raises(confirm.ConfirmationRequired):
        confirm.require_confirmation(conn, "NOBODY")
    with pytest.raises(confirm.ConfirmationRequired):
        confirm.require_confirmation(conn, "OPR-01", allowed_roles=("coordinator",))
    assert confirm.require_confirmation(conn, "CRD-01", allowed_roles=("coordinator",)) == "CRD-01"


# -- db helpers ------------------------------------------------------------


def test_next_id_and_audit(conn: sqlite3.Connection) -> None:
    assert db.next_id(conn, "complaints", "complaint_id", "CMP-", 4) == "CMP-0029"
    before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    db.audit(
        conn,
        actor_id="OPR-01",
        channel="test",
        action="update",
        entity_table="calls",
        entity_id="CALL-000001",
        before={"a": 1},
        after={"a": 2},
    )
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before + 1
    assert conn.execute("SELECT actor_role FROM audit_log ORDER BY audit_id DESC LIMIT 1").fetchone()[0] == "operator"


# -- classifier ------------------------------------------------------------


def test_classifier_leaves_low_confidence_blank(conn: sqlite3.Connection) -> None:
    model = classifier.load_or_train(conn)
    sure = classifier.predict(model, "Family of 6 has no food left, asking for a food parcel and rice.", 0.6)
    assert sure.category == "food"
    vague = classifier.predict(model, "xyz qq", 0.6)
    assert vague.category is None and vague.top_category
    assert classifier.predict(model, "   ", 0.6).category is None
    arabic = classifier.predict(model, "لا توجد مياه في المبنى منذ أمس، بحاجة إلى مياه شرب.", 0.5)
    assert arabic.category == "water_hygiene"


# -- geo, ports, registry --------------------------------------------------


def test_route_length() -> None:
    assert geo.distance_km((0, 0), (3, 4)) == 6.5
    assert geo.route_km([(0, 0), (3, 4)], return_to_start=True) == 13.0


def test_ports() -> None:
    assert ports.project_port(1) == 8501 and ports.project_port("20") == 8520
    assert ports.HOMEPAGE_PORT == 8500 and ports.API_PORT == 8600
    with pytest.raises(ValueError):
        ports.project_port(21)


def test_registry_has_twenty_projects_in_five_groups() -> None:
    projects = all_projects()
    assert [p.number for p in projects] == list(range(1, 21))
    assert {p.group for p in projects} == set(GROUPS)
    for p in projects:
        assert p.folder.name == f"{p.code}-{p.slug}"
        assert p.user and p.task and p.status in ("in_progress", "built")
