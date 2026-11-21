"""Staff confirmation for consequential actions.

Domain functions that change something consequential (referrals, merges, sync
conflicts, convoy dispatch, advisory overrides, reconciliation sign-off, briefing
approval) take a `confirmed_by` staff ID and call require_confirmation first.
The Streamlit side of the same rule is shared.ui.confirm_action.
"""

from __future__ import annotations

import sqlite3


class ConfirmationRequired(Exception):
    """Raised when a consequential action is attempted without staff confirmation."""


def require_confirmation(
    conn: sqlite3.Connection,
    confirmed_by: str | None,
    allowed_roles: tuple[str, ...] | None = None,
) -> str:
    """Return the confirming staff ID, or raise ConfirmationRequired."""
    if not confirmed_by or not str(confirmed_by).strip():
        raise ConfirmationRequired("A staff member must confirm this action.")
    row = conn.execute("SELECT role, active FROM staff WHERE staff_id = ?", (confirmed_by,)).fetchone()
    if row is None or not row["active"]:
        raise ConfirmationRequired(f"Unknown or inactive staff ID: {confirmed_by}")
    if allowed_roles and row["role"] not in allowed_roles and row["role"] != "admin":
        raise ConfirmationRequired(f"{confirmed_by} ({row['role']}) cannot confirm this action; needs one of {', '.join(allowed_roles)}.")
    return confirmed_by
