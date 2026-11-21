"""Shared pytest fixtures.

Tests never touch the default database (data/portfolio.db):
  * A session-wide guard points PORTFOLIO_DB_PATH at a throwaway path before any test runs.
  * The `db_path` fixture copies a freshly seeded database (built once per session in a
    temp folder, with the clock frozen at ANCHOR) and points PORTFOLIO_DB_PATH at the copy.

Project modules are importable by name: every projects/NN-*/ folder is on sys.path, which
is why project module names must be unique (e.g. p02_complaints.py).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for _folder in sorted((ROOT / "projects").glob("[0-2][0-9]-*")):
    if str(_folder) not in sys.path:
        sys.path.insert(0, str(_folder))

from shared import clock, config, db, seed  # noqa: E402

# The demo clock used by every test: the seed builds data around it and tests run "at" it.
ANCHOR = datetime(2026, 11, 16, 10, 0, 0)


@pytest.fixture(scope="session", autouse=True)
def _never_use_default_db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    guard = tmp_path_factory.mktemp("guard") / "tests-must-use-the-db_path-fixture.db"
    old_db = os.environ.get(config.DB_ENV_VAR)
    old_models = os.environ.get("PORTFOLIO_MODELS_DIR")
    os.environ[config.DB_ENV_VAR] = str(guard)
    os.environ["PORTFOLIO_MODELS_DIR"] = str(tmp_path_factory.mktemp("models"))
    yield
    for key, old in ((config.DB_ENV_VAR, old_db), ("PORTFOLIO_MODELS_DIR", old_models)):
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


@pytest.fixture(scope="session")
def seeded_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A seeded database built once per test session (do not write to it; use db_path)."""
    path = tmp_path_factory.mktemp("template") / "template.db"
    seed.reset_database(path, ANCHOR)
    return path


@pytest.fixture
def db_path(seeded_template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh copy of the seeded database for this test, set as PORTFOLIO_DB_PATH, clock frozen at ANCHOR."""
    path = tmp_path / "portfolio.db"
    shutil.copyfile(seeded_template, path)
    monkeypatch.setenv(config.DB_ENV_VAR, str(path))
    clock.set_now(ANCHOR)
    yield path
    clock.set_now(None)


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """An open connection to this test's database copy (closed after the test)."""
    c = db.connect(db_path)
    yield c
    c.close()


def project_folder(number: int) -> Path:
    matches = sorted((ROOT / "projects").glob(f"{number:02d}-*"))
    assert matches, f"no folder for project {number:02d}"
    return matches[0]


@pytest.fixture
def app_test(db_path: Path):  # noqa: ANN201 - returns a factory
    """Factory: app_test(2) -> streamlit AppTest for projects/02-*/app.py against this test's database."""
    from streamlit.testing.v1 import AppTest

    def _make(number: int, timeout: float = 60) -> AppTest:
        return AppTest.from_file(str(project_folder(number) / "app.py"), default_timeout=timeout)

    return _make
