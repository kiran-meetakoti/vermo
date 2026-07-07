"""Local username/password auth for multi-user Vermo.

Self-contained on purpose: a separate auth.db, no dependency on the
existing portfolio.db schema, and no wiring into streamlit_app.py yet.
Swapping this for Supabase Auth later means replacing this module's
functions (register/login/current_user) without touching callers much,
since callers will only ever see a user_id string.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import streamlit as st

AUTH_DB = Path(__file__).resolve().parent.parent / "data" / "auth.db"
PBKDF2_ITERATIONS = 200_000

# Streamlit's session_state resets on every browser refresh (a refresh opens a
# brand-new WebSocket session) — there is no Streamlit-native way to persist
# login across that. A random session token stored in the URL query params
# survives a refresh, and is looked up against this table to restore the
# login. INACTIVITY_TIMEOUT controls how long an unused token stays valid;
# every authenticated page load "touches" the token, resetting that window.
INACTIVITY_TIMEOUT = timedelta(days=7)


def _connect() -> sqlite3.Connection:
    AUTH_DB.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(AUTH_DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                display_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_active_at TEXT NOT NULL
            )
            """
        )


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS).hex()


def register(email: str, password: str, display_name: str) -> tuple[bool, str]:
    email = email.strip().lower()
    if not email or "@" not in email:
        return False, "Enter a valid email address."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    salt = os.urandom(16)
    password_hash = _hash_password(password, salt)
    with _connect() as conn:
        existing = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return False, "An account with this email already exists."
        conn.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(uuid4()),
                email,
                password_hash,
                salt.hex(),
                display_name.strip() or email.split("@")[0],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return True, "Account created."


def _create_session(user_id: str) -> str:
    token = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, last_active_at) VALUES (?, ?, ?, ?)",
            (token, user_id, now, now),
        )
    return token


def _touch_session(token: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET last_active_at = ? WHERE token = ?",
            (datetime.now(timezone.utc).isoformat(), token),
        )


def _delete_session(token: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def resolve_session(token: str) -> dict | None:
    """Look up a session token, expiring it if it's past INACTIVITY_TIMEOUT.

    Pure DB lookup, no Streamlit dependency — also used by main.py (FastAPI)
    to authenticate API requests via the same session token Streamlit issues,
    instead of trusting a client-supplied user_id."""
    with _connect() as conn:
        session_row = conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
        if not session_row:
            return None
        last_active = datetime.fromisoformat(session_row["last_active_at"])
        if datetime.now(timezone.utc) - last_active > INACTIVITY_TIMEOUT:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return None
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (session_row["user_id"],)).fetchone()
    if not user_row:
        return None
    return {"id": user_row["id"], "email": user_row["email"], "display_name": user_row["display_name"]}


def _start_session(user_row: sqlite3.Row) -> None:
    token = _create_session(user_row["id"])
    st.session_state["user_id"] = user_row["id"]
    st.session_state["user_email"] = user_row["email"]
    st.session_state["display_name"] = user_row["display_name"]
    st.session_state["session_token"] = token
    st.query_params["session"] = token


def reset_password(email: str, new_password: str) -> tuple[bool, str]:
    """Update a user's password directly, no email round-trip.

    This app runs locally with no email service configured, so recovery
    trusts whoever can reach the login page with the account's email —
    the same trust boundary the app already relies on.
    """
    email = email.strip().lower()
    if not email or "@" not in email:
        return False, "Enter a valid email address."
    if len(new_password) < 8:
        return False, "Password must be at least 8 characters."
    salt = os.urandom(16)
    password_hash = _hash_password(new_password, salt)
    with _connect() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not existing:
            return False, "No account with that email."
        conn.execute(
            "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
            (password_hash, salt.hex(), existing["id"]),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (existing["id"],))
    return True, "Password updated. Log in with your new password."


def login(email: str, password: str) -> tuple[bool, str]:
    email = email.strip().lower()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        return False, "No account with that email."
    salt = bytes.fromhex(row["password_salt"])
    candidate = _hash_password(password, salt)
    if not hmac.compare_digest(candidate, row["password_hash"]):
        return False, "Incorrect password."
    _start_session(row)
    return True, "Logged in."


def logout() -> None:
    token = st.session_state.get("session_token")
    if token:
        _delete_session(token)
    for key in ("user_id", "user_email", "display_name", "session_token"):
        st.session_state.pop(key, None)
    st.query_params.pop("session", None)


def current_user() -> dict | None:
    if "user_id" in st.session_state:
        token = st.session_state.get("session_token")
        if token:
            _touch_session(token)
        return {
            "id": st.session_state["user_id"],
            "email": st.session_state.get("user_email"),
            "display_name": st.session_state.get("display_name"),
        }

    # Not in this WebSocket session (e.g. the page was just refreshed) — try to
    # restore from the session token carried in the URL query params instead.
    token = st.query_params.get("session")
    if not token:
        return None
    user = resolve_session(token)
    if not user:
        st.query_params.pop("session", None)
        return None
    st.session_state["user_id"] = user["id"]
    st.session_state["user_email"] = user["email"]
    st.session_state["display_name"] = user["display_name"]
    st.session_state["session_token"] = token
    _touch_session(token)
    return user


def require_login() -> dict:
    """Render the shared login/signup page (auth/login_ui.py) and st.stop()
    until the user is authenticated. Returns the current_user() dict on every
    rerun after a successful login."""
    init_auth_db()
    user = current_user()
    if user:
        return user

    from auth.login_ui import render_login_page

    def register_with_hint(email: str, password: str, display_name: str) -> tuple[bool, str]:
        ok, message = register(email, password, display_name)
        return ok, (message + " Log in from the other tab.") if ok else message

    render_login_page(login=login, register=register_with_hint, reset_password=reset_password)
    raise RuntimeError("unreachable: render_login_page calls st.stop()")
