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
    """Parse a stored or caller-supplied timestamp into naive local time."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def normalize(value: str | datetime | None) -> str:
    """Any caller timestamp (offset, space separator, datetime, None) as the stored form.

    Stored timestamps are compared as text, so every one must be naive local time
    in the one format ISO. None means now.
    """
    if value is None:
        return iso()
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return iso(parsed.replace(microsecond=0))
