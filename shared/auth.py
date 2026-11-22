"""Server-mode login helpers (Phase 7). Local demo mode needs no account.

Every value is read from the environment at call time (see .env.example):
  PORTFOLIO_MODE=server        switches the checks on (shared.config.mode())
  PORTFOLIO_STAFF_USERNAME     staff login for anything that changes data in the apps
  PORTFOLIO_STAFF_PASSWORD
  PORTFOLIO_API_TOKEN          bearer token the API accepts for writes in server mode
                               (the API also accepts the staff login above as HTTP Basic)

A value that is missing, empty or still the placeholder 'change-me' refuses every
login, so a server never runs with the example credentials. Comparisons use
hmac.compare_digest. No secret is ever stored in a file in this repository.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import os

from shared import config

PLACEHOLDER = "change-me"
USERNAME_VAR = "PORTFOLIO_STAFF_USERNAME"
PASSWORD_VAR = "PORTFOLIO_STAFF_PASSWORD"
TOKEN_VAR = "PORTFOLIO_API_TOKEN"
SESSION_KEY = "_staff_login"

# Staff roles allowed to perform each write action, shared by the API and the Project 20 app.
ROLES: dict[str, tuple[str, ...]] = {
    "positions": ("field_worker", "coordinator", "admin"),
    "acknowledge": ("coordinator", "duty_officer", "warehouse_officer", "admin"),
    "evaluate": ("coordinator", "duty_officer", "admin"),
    "rules": ("coordinator", "admin"),
    "settings": ("coordinator", "admin"),
    "simulator": ("coordinator", "admin"),
    "jobs": ("coordinator", "admin"),
    "sync": ("field_worker", "coordinator", "admin"),  # a device sends its queued changes
    "sync_office": ("caseworker", "coordinator", "admin"),  # server-side edits and conflict choices
}


def role_allowed(role: str | None, action: str) -> bool:
    return role is not None and role in ROLES[action]


def server_mode() -> bool:
    return config.mode() == "server"


def _secret(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    if not value or value == PLACEHOLDER:
        return None
    return value


def _same(given: str | None, expected: str | None) -> bool:
    if expected is None or given is None:
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def login_configured() -> bool:
    return _secret(USERNAME_VAR) is not None and _secret(PASSWORD_VAR) is not None


def api_token_configured() -> bool:
    return _secret(TOKEN_VAR) is not None


def check_staff_login(username: str | None, password: str | None) -> bool:
    """True only when both match the configured (non-placeholder) values."""
    user_ok = _same(username, _secret(USERNAME_VAR))
    pass_ok = _same(password, _secret(PASSWORD_VAR))
    return user_ok and pass_ok


def check_api_token(token: str | None) -> bool:
    """True only when the token matches PORTFOLIO_API_TOKEN (never the placeholder)."""
    return _same(token, _secret(TOKEN_VAR))


def bearer_token(authorization: str | None) -> str | None:
    """The token from an 'Authorization: Bearer <token>' header value."""
    if not authorization:
        return None
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def basic_credentials(authorization: str | None) -> tuple[str, str] | None:
    """(username, password) from an 'Authorization: Basic <base64>' header value."""
    if not authorization:
        return None
    scheme, _, encoded = authorization.strip().partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    return (username, password) if sep else None


def check_api_authorization(authorization: str | None) -> bool:
    """Server-mode API login: the staff login (HTTP Basic) or the API token (Bearer), both read from the environment."""
    if check_api_token(bearer_token(authorization)):
        return True
    creds = basic_credentials(authorization)
    return creds is not None and check_staff_login(*creds)


def require_staff_login() -> str | None:
    """Streamlit gate. Local mode: does nothing and returns None.

    Server mode: shows a login form and stops the page until the staff login
    succeeds; afterwards returns the logged-in username for this browser session.
    """
    if not server_mode():
        return None
    import streamlit as st

    if st.session_state.get(SESSION_KEY):
        if st.sidebar.button("Log out", key="_staff_logout"):
            st.session_state.pop(SESSION_KEY, None)
            st.rerun()
        return str(st.session_state[SESSION_KEY])
    st.info("Server mode: staff login required.")
    if not login_configured():
        st.error("The staff login is not configured on this server (set PORTFOLIO_STAFF_USERNAME and PORTFOLIO_STAFF_PASSWORD in .env).")
        st.stop()
    with st.form("_staff_login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in", type="primary")
    if submitted:
        if check_staff_login(username, password):
            st.session_state[SESSION_KEY] = username
            st.rerun()
        st.error("Login failed.")
    st.stop()
    return None  # pragma: no cover - st.stop() ends the script run
