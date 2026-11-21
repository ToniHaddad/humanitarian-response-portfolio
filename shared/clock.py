"""One clock for the whole portfolio so tests and demos can freeze time.

Set PORTFOLIO_FROZEN_NOW="2026-11-16T09:00:00" (or call set_now) to freeze it.
"""

from __future__ import annotations

import os
from datetime import date, datetime

_frozen: datetime | None = None

ISO = "%Y-%m-%dT%H:%M:%S"


def set_now(value: datetime | None) -> None:
    """Freeze the clock at value, or unfreeze with None."""
    global _frozen
    _frozen = value.replace(microsecond=0) if value else None


def now() -> datetime:
    if _frozen is not None:
        return _frozen
    env = os.environ.get("PORTFOLIO_FROZEN_NOW")
    if env:
        return datetime.fromisoformat(env).replace(microsecond=0)
    return datetime.now().replace(microsecond=0)


def today() -> date:
    return now().date()


def iso(value: datetime | None = None) -> str:
    return (value or now()).strftime(ISO)


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value)
