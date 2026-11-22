"""Server mode files: static checks of docker-compose.yml, .env.example and .gitignore, and a real
backup-and-restore run on a seeded temporary database. Docker itself is not run by these tests."""

from __future__ import annotations

import importlib.util
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"


def _load_script(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_compose_has_the_expected_services(compose: dict[str, Any]) -> None:
    assert {"apps", "api", "scheduler", "backup"} <= set(compose["services"])
    assert "portfolio-data" in compose["volumes"]


def test_every_service_restarts_and_has_a_health_check(compose: dict[str, Any]) -> None:
    for name, svc in compose["services"].items():
        assert svc.get("restart") == "unless-stopped", name
        check = svc.get("healthcheck") or {}
        assert check.get("test") and check.get("interval") and check.get("retries"), f"{name} has no complete health check"


def test_every_service_shares_the_database_volume_and_one_timezone(compose: dict[str, Any]) -> None:
    for name, svc in compose["services"].items():
        assert any(str(v).startswith("portfolio-data:") for v in svc["volumes"]), name
        assert svc["environment"]["TZ"] == "${TZ:-UTC}", name
        assert svc["environment"]["PORTFOLIO_MODE"] == "server", name


def test_no_port_is_published_on_all_interfaces(compose: dict[str, Any]) -> None:
    published = 0
    for name, svc in compose["services"].items():
        for mapping in svc.get("ports", []):
            published += 1
            text = str(mapping)
            assert text.startswith("${SERVER_BIND_ADDRESS:-127.0.0.1}:"), f"{name}: {text} is not bound to SERVER_BIND_ADDRESS"
            assert "0.0.0.0" not in text and "[::]" not in text, f"{name}: {text}"
    assert published >= 2  # the apps and the API


def test_compose_contains_no_secret_or_real_address() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    addresses = set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text))
    assert addresses <= {"127.0.0.1", "0.0.0.0"}  # 0.0.0.0 only as the container-internal listen address
    assert not re.search(r"PASSWORD|TOKEN", text)


def test_env_example_holds_only_placeholders() -> None:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", 1) for line in lines if line.strip() and not line.startswith("#"))
    assert values["SERVER_BIND_ADDRESS"] == "127.0.0.1"
    for key in ("PORTFOLIO_STAFF_USERNAME", "PORTFOLIO_STAFF_PASSWORD", "PORTFOLIO_API_TOKEN"):
        assert values[key] in ("change-me", "CHANGE_ME"), key
    text = "\n".join(lines)
    assert set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)) <= {"127.0.0.1"}
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), "looks like an email address"
    assert not re.search(r"\b(?=[A-Za-z0-9_\-]{24,}\b)(?=\w*\d)(?=\w*[A-Za-z])[A-Za-z0-9_\-]+\b", text), "looks like a key"
    assert not re.search(r"\b(?:sk|ghp|xox[bp]|AKIA)[-_A-Za-z0-9]{8,}", text)


def test_env_file_is_ignored_and_example_is_not() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignore
    assert "!.env.example" in ignore
    docker_ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in docker_ignore


def test_placeholder_credentials_are_refused_by_the_login(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared import auth

    values = dict(line.split("=", 1) for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines() if "=" in line and line[0] != "#")
    monkeypatch.setenv("PORTFOLIO_STAFF_USERNAME", values["PORTFOLIO_STAFF_USERNAME"])
    monkeypatch.setenv("PORTFOLIO_STAFF_PASSWORD", values["PORTFOLIO_STAFF_PASSWORD"])
    monkeypatch.setenv("PORTFOLIO_API_TOKEN", values["PORTFOLIO_API_TOKEN"])
    assert not auth.login_configured()
    assert not auth.api_token_configured()


def test_dockerfile_and_entrypoint_exist() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements.txt" in dockerfile and "ENTRYPOINT" in dockerfile and "USER " in dockerfile
    assert "exec" in (ROOT / "scripts" / "docker-entrypoint.sh").read_text(encoding="utf-8")


def _seeded_database(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE households (household_id TEXT PRIMARY KEY, area TEXT)")
    conn.execute("CREATE TABLE parcels (parcel_id INTEGER PRIMARY KEY, household_id TEXT REFERENCES households)")
    conn.executemany("INSERT INTO households VALUES (?, ?)", [(f"HH-{i:03d}", f"Area {i % 4}") for i in range(40)])
    conn.executemany("INSERT INTO parcels(household_id) VALUES (?)", [(f"HH-{i % 40:03d}",) for i in range(120)])
    conn.commit()
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("households", "parcels")}
    conn.close()
    return counts


def _counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("households", "parcels")}
    finally:
        conn.close()


def test_backup_produces_a_restorable_copy(tmp_path: Path) -> None:
    backup_db = _load_script("backup_db")
    restore_db = _load_script("restore_db")
    live = tmp_path / "portfolio.db"
    expected = _seeded_database(live)

    copy = backup_db.backup_database(live, tmp_path / "backups")
    assert copy.exists() and copy.name.startswith("portfolio-") and copy.suffix == ".db"
    assert _counts(copy) == expected

    # Damage the live database, then restore it from the backup.
    conn = sqlite3.connect(live)
    conn.execute("DELETE FROM parcels")
    conn.execute("DELETE FROM households WHERE household_id > 'HH-009'")
    conn.commit()
    conn.close()
    assert _counts(live) != expected

    assert restore_db.main(["--target", str(live), "--dest", str(tmp_path / "backups"), "--latest"]) == 2  # refuses without --yes
    assert _counts(live) != expected
    assert restore_db.main(["--target", str(live), "--dest", str(tmp_path / "backups"), "--latest", "--yes"]) == 0
    assert _counts(live) == expected
    assert list(tmp_path.glob("portfolio.db.before-restore-*"))  # the damaged database was kept, not destroyed
    conn = sqlite3.connect(live)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_backup_keeps_only_the_newest_copies(tmp_path: Path) -> None:
    from datetime import datetime, timedelta

    backup_db = _load_script("backup_db")
    live = tmp_path / "portfolio.db"
    _seeded_database(live)
    start = datetime(2026, 11, 16, 9, 0, 0)
    written = [backup_db.backup_database(live, tmp_path / "b", keep=3, now=start + timedelta(hours=h)) for h in range(5)]
    left = sorted(p.name for p in (tmp_path / "b").glob("*.db"))
    assert left == sorted(p.name for p in written[-3:])
    assert not list((tmp_path / "b").glob(".*.partial"))


def test_backup_command_line_and_missing_database(tmp_path: Path) -> None:
    backup_db = _load_script("backup_db")
    live = tmp_path / "portfolio.db"
    _seeded_database(live)
    assert backup_db.main(["--source", str(live), "--dest", str(tmp_path / "out"), "--keep", "2"]) == 0
    assert len(list((tmp_path / "out").glob("portfolio-*.db"))) == 1
    assert backup_db.main(["--source", str(tmp_path / "nope.db"), "--dest", str(tmp_path / "out")]) == 1


def test_restore_refuses_a_corrupt_backup(tmp_path: Path) -> None:
    restore_db = _load_script("restore_db")
    live = tmp_path / "portfolio.db"
    expected = _seeded_database(live)
    bad = tmp_path / "portfolio-20261116-090000.db"
    bad.write_bytes(b"this is not a database" * 100)
    assert restore_db.main(["--target", str(live), "--backup", str(bad), "--yes"]) == 1
    assert _counts(live) == expected


def test_healthcheck_backup_and_heartbeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    health = _load_script("healthcheck")
    backup_db = _load_script("backup_db")
    live = tmp_path / "portfolio.db"
    _seeded_database(live)
    monkeypatch.setenv("PORTFOLIO_BACKUP_DIR", str(tmp_path / "b"))
    assert not health.backup_ok(3600)
    backup_db.backup_database(live, tmp_path / "b")
    assert health.backup_ok(3600)

    from datetime import datetime, timedelta

    conn = sqlite3.connect(live)
    conn.execute("CREATE TABLE service_heartbeats (service_name TEXT PRIMARY KEY, last_seen_at TEXT)")
    conn.execute("INSERT INTO service_heartbeats VALUES ('scheduler', ?)", (datetime.now().isoformat(timespec="seconds"),))
    conn.execute("INSERT INTO service_heartbeats VALUES ('old', ?)", ((datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"),))
    conn.commit()
    conn.close()
    monkeypatch.setenv("PORTFOLIO_DB_PATH", str(live))
    assert health.heartbeat_ok("scheduler")
    assert not health.heartbeat_ok("old")
    assert not health.heartbeat_ok("missing")
