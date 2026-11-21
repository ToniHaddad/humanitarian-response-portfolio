"""Paths and runtime settings. Values are read at call time so tests and agents can override them.

Environment variables (all optional):
  PORTFOLIO_DB_PATH      SQLite file to use. Default: data/portfolio.db
  PORTFOLIO_MODE         'local' (default) or 'server'
  PORTFOLIO_MODELS_DIR   where trained models are cached. Default: data/models
  PORTFOLIO_FROZEN_NOW   freeze the clock, e.g. 2026-11-16T09:00:00 (see shared.clock)
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
PROJECTS_DIR: Path = REPO_ROOT / "projects"
DATA_DIR: Path = REPO_ROOT / "data"
SCHEMA_PATH: Path = Path(__file__).resolve().parent / "schema.sql"
DEFAULT_DB_PATH: Path = DATA_DIR / "portfolio.db"

DB_ENV_VAR = "PORTFOLIO_DB_PATH"

# One seed for every generated value in the demo data.
SEED: int = 4217


def db_path() -> Path:
    """The one place the SQLite path is resolved. Override with PORTFOLIO_DB_PATH."""
    override = os.environ.get(DB_ENV_VAR, "").strip()
    return Path(override) if override else DEFAULT_DB_PATH


def mode() -> str:
    """'local' (default demo mode) or 'server'."""
    return os.environ.get("PORTFOLIO_MODE", "local").strip().lower()


def models_dir() -> Path:
    override = os.environ.get("PORTFOLIO_MODELS_DIR", "").strip()
    if override:
        return Path(override)
    # Keep models next to the database so each agent's copy has its own cache.
    return db_path().parent / "models"
