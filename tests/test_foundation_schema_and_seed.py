"""Schema and seed: every table filled, links intact, deterministic, walkthrough scenarios present."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from conftest import ANCHOR

from shared import config, db, fleet, food, security, seed


def scenarios(conn: sqlite3.Connection) -> dict:
    return json.loads(db.get_setting("demo.scenarios", conn=conn) or "{}")


def test_tests_never_use_default_database(db_path: Path) -> None:
    assert config.db_path() == db_path
    assert config.db_path() != config.DEFAULT_DB_PATH


def test_every_table_has_rows(conn: sqlite3.Connection) -> None:
    empty = [
        t
        for t in db.table_names(conn)
        if t not in seed.TABLES_ALLOWED_EMPTY and conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0
    ]
    assert empty == []
    assert len(db.table_names(conn)) >= 60


def test_foreign_keys_are_intact(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_seed_is_deterministic(tmp_path: Path) -> None:
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    seed.reset_database(a, ANCHOR)
    seed.reset_database(b, ANCHOR)

    def dump(p: Path) -> list:
        c = sqlite3.connect(p)
        try:
            out = []
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
                out.append((t, c.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()))
            return out
        finally:
            c.close()

    assert dump(a) == dump(b)


def test_reset_rebuilds_database(db_path: Path) -> None:
    with db.transaction() as c:
        c.execute("DELETE FROM calls WHERE complaint_id IS NULL")
    seed.reset_database(db_path, ANCHOR)
    assert db.scalar("SELECT COUNT(*) FROM calls") == 300


def test_stock_balances_received_minus_dispatched(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT warehouse_id, item_id, received, dispatched, on_hand FROM v_stock_on_hand").fetchall()
    assert rows
    for r in rows:
        assert r["received"] - r["dispatched"] == r["on_hand"]
        assert r["on_hand"] >= 0, tuple(r)
    disp = conn.execute("SELECT SUM(quantity) FROM dispatches").fetchone()[0]
    ledger = conn.execute("SELECT SUM(quantity) FROM stock_movements WHERE reason = 'dispatch'").fetchone()[0]
    assert disp == ledger


def test_parcels_and_meals_are_separate_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(distributions)")}
    assert {"parcel_quantity", "rte_meal_quantity"} <= cols
    assert "quantity" not in cols


def test_repeat_calls_do_not_add_complaints(conn: sqlite3.Connection) -> None:
    complaints = conn.execute("SELECT COUNT(*) FROM complaints").fetchone()[0]
    complaint_calls = conn.execute("SELECT COUNT(*) FROM calls WHERE complaint_id IS NOT NULL").fetchone()[0]
    repeats = conn.execute("SELECT COUNT(*) FROM calls WHERE complaint_id IS NOT NULL AND is_repeat = 1").fetchone()[0]
    assert complaint_calls == complaints + repeats
    assert repeats > 0


def test_p02_scenario(conn: sqlite3.Connection) -> None:
    s = scenarios(conn)["p02"]
    row = conn.execute("SELECT status FROM complaints WHERE complaint_id = ?", (s["existing_open_complaint_id"],)).fetchone()
    assert row and row["status"] != "closed"


def test_p08_scenario(conn: sqlite3.Connection) -> None:
    s = scenarios(conn)["p08"]
    rec = conn.execute("SELECT version FROM intake_records WHERE record_id = ?", (s["record_id"],)).fetchone()
    versions = conn.execute("SELECT MAX(version) FROM record_versions WHERE record_id = ?", (s["record_id"],)).fetchone()[0]
    assert rec["version"] == versions == s["server_version"]
    assert conn.execute("SELECT online FROM sync_devices WHERE device_id = ?", (s["device_id"],)).fetchone()[0] == 1


def test_p11_scenario(conn: sqlite3.Connection) -> None:
    s = scenarios(conn)["p11"]
    end = date.fromisoformat(s["current_coverage_end"])
    assert ANCHOR.date() + timedelta(days=2) <= end <= ANCHOR.date() + timedelta(days=6)
    last = conn.execute(
        """SELECT distribution_date, expected_coverage_days FROM distributions WHERE household_id = ?
                           ORDER BY distribution_date DESC LIMIT 1""",
        (s["household_id"],),
    ).fetchone()
    assert food.coverage_end_date(date.fromisoformat(last[0]), last[1]) == end


def test_p12_scenario_100_96_3(conn: sqlite3.Connection) -> None:
    s = scenarios(conn)["p12"]
    args = (s["site_id"], s["item_id"], s["period_start"], s["period_end"])
    dispatched = conn.execute(
        """SELECT SUM(quantity) FROM dispatches WHERE site_id=? AND item_id=?
                                 AND substr(dispatched_at,1,10) BETWEEN ? AND ?""",
        args,
    ).fetchone()[0]
    distributed = conn.execute(
        """SELECT SUM(parcel_quantity) FROM distributions WHERE site_id=? AND parcel_item_id=?
                                  AND distribution_date BETWEEN ? AND ?""",
        args,
    ).fetchone()[0]
    returned = conn.execute(
        """SELECT SUM(quantity) FROM distribution_returns WHERE site_id=? AND item_id=?
                               AND substr(returned_at,1,10) BETWEEN ? AND ?""",
        args,
    ).fetchone()[0]
    assert (dispatched, distributed, returned) == (100, 96, 3)
    assert dispatched - distributed - returned == 1
    dup = conn.execute(
        """SELECT COUNT(*) FROM distributions WHERE site_id=? AND household_id=? AND distribution_date BETWEEN ? AND ?""",
        (s["site_id"], s["duplicate_household_id"], s["period_start"], s["period_end"]),
    ).fetchone()[0]
    assert dup >= 2
    unmatched = conn.execute(
        """SELECT COUNT(*) FROM distributions WHERE site_id=? AND household_id=? AND dispatch_id IS NULL
                                AND distribution_date BETWEEN ? AND ?""",
        (s["site_id"], s["unmatched_household_id"], s["period_start"], s["period_end"]),
    ).fetchone()[0]
    assert unmatched == 1


def test_p16_scenario(conn: sqlite3.Connection) -> None:
    s = scenarios(conn)["p16"]
    readiness = fleet.fleet_readiness(conn, ANCHOR.date())
    assert readiness[s["overdue_vehicle_id"]].status == "not_ready"
    assert any("Service overdue" in r for r in readiness[s["overdue_vehicle_id"]].reasons)
    assert readiness[s["grounded_vehicle_id"]].status == "not_ready"
    assert readiness[s["insurance_expired_vehicle_id"]].status == "not_ready"
    assert readiness["VEH-05"].status == "due_for_service"
    assert readiness["VEH-01"].status == "ready"
    assert s["blocked_area_id"] in security.blocked_areas(conn)
    assert conn.execute("SELECT status FROM convoys WHERE convoy_id = ?", (s["draft_convoy_id"],)).fetchone()[0] == "draft"


def test_p17_scenario_positions_fresh_at_anchor(conn: sqlite3.Connection) -> None:
    s = scenarios(conn)["p17"]
    last = {r["vehicle_id"]: r["reported_at"] for r in conn.execute("SELECT * FROM v_last_position")}
    for vid in s["simulated_vehicle_ids"]:
        assert not fleet.is_stale(last[vid], ANCHOR, 15), vid
    assert fleet.is_stale(last[s["already_stale_vehicle_id"]], ANCHOR, 15)
    # After the simulator stops, fresh vehicles become stale once the limit passes.
    later = ANCHOR + timedelta(minutes=20)
    assert all(fleet.is_stale(last[v], later, 15) for v in s["simulated_vehicle_ids"])


def test_advisories_match_documented_rule(conn: sqlite3.Connection) -> None:
    cur = security.current_advisories(conn)
    assert cur["AR-07"]["final_level"] == 2
    assert cur["AR-11"]["final_level"] == 1
    assert cur["AR-04"]["is_override"] == 1 and cur["AR-04"]["override_reason"]
    assert cur["AR-02"]["suggested_level"] == 0  # only unverified reports there
    for area, row in cur.items():
        decided = datetime.fromisoformat(row["decided_at"])
        incidents = [
            dict(r) for r in conn.execute("SELECT * FROM incidents WHERE area_id = ? AND occurred_at <= ?", (area, row["decided_at"]))
        ]
        assert security.suggest_level(incidents, decided) == row["suggested_level"], area


def test_p06_planted_duplicates_exist(conn: sqlite3.Connection) -> None:
    s = scenarios(conn)["p06"]
    assert len(s["duplicate_pairs"]) == 8
    merged = conn.execute("SELECT status, merged_into FROM households WHERE household_id = 'HH-00369'").fetchone()
    assert tuple(merged) == ("merged", "HH-00200")
    assert conn.execute("SELECT COUNT(*) FROM merge_history").fetchone()[0] == 2
