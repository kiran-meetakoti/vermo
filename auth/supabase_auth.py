"""Supabase Auth (GoTrue) backend — used when VERMO_BACKEND=postgres.

Same public surface as auth/local_auth.py (register/login/logout,
current_user/require_login for Streamlit, resolve_session for FastAPI), but
credentials live in Supabase's auth.users — the same ids that own every row
in the Postgres tables, and that RLS policies check against.

Sessions are GoTrue JWTs: a short-lived access token plus a rotating refresh
token. The refresh token is mirrored into st.query_params (like local_auth's
session token) so a browser reload can restore the session; every refresh
rotation updates it. No Supabase SDK — the auth REST API is four endpoints,
called with urllib and the publishable (anon) key from .env.

Password reset: not offered in-app here. GoTrue's recovery flow emails a
link whose token arrives in the URL hash fragment, which never reaches the
Streamlit server — the proper reset page comes with the Stage 5 frontend
(see MULTI_USER_ROADMAP Stage 4 note). Until then, reset via the Supabase
dashboard.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import streamlit as st

import db

_MIN_PASSWORD_LEN = 8


def _auth_url(path: str) -> str:
    base = db._env("SUPABASE_URL").rstrip("/")
    if not base:
        raise RuntimeError("SUPABASE_URL is not configured (.env)")
    return f"{base}/auth/v1{path}"


def _request(path: str, payload: dict | None = None, token: str | None = None,
             method: str = "POST") -> tuple[int, dict]:
    """Call a GoTrue endpoint. Returns (status_code, response_json)."""
    key = db._env("SUPABASE_ANON_KEY")
    if not key:
        raise RuntimeError("SUPABASE_ANON_KEY is not configured (.env)")
    headers = {"apikey": key, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(_auth_url(path), data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read()
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        except (ValueError, AttributeError):
            return error.code, {}


def _error_message(status: int, body: dict) -> str:
    message = body.get("msg") or body.get("message") or body.get("error_description") or ""
    if "invalid login credentials" in message.lower():
        return "Incorrect email or password."
    return message or f"Authentication failed (HTTP {status})."


def _user_dict(user: dict) -> dict:
    email = user.get("email", "")
    metadata = user.get("user_metadata") or {}
    return {
        "id": user["id"],
        "email": email,
        "display_name": metadata.get("display_name") or (email.split("@")[0] if email else "there"),
    }


def _store_session(session: dict) -> None:
    user = _user_dict(session["user"])
    st.session_state["user_id"] = user["id"]
    st.session_state["user_email"] = user["email"]
    st.session_state["display_name"] = user["display_name"]
    st.session_state["session_token"] = session["access_token"]
    st.session_state["_sb_refresh_token"] = session["refresh_token"]
    st.session_state["_sb_expires_at"] = time.time() + float(session.get("expires_in", 3600))
    # Refresh token in the URL lets a full browser reload restore the session,
    # mirroring local_auth's ?session= param. It rotates on every refresh.
    st.query_params["session"] = session["refresh_token"]


def register(email: str, password: str, display_name: str) -> tuple[bool, str]:
    email = email.strip().lower()
    if not email or "@" not in email:
        return False, "Enter a valid email address."
    if len(password) < _MIN_PASSWORD_LEN:
        return False, f"Password must be at least {_MIN_PASSWORD_LEN} characters."
    status, body = _request("/signup", {
        "email": email,
        "password": password,
        "data": {"display_name": display_name.strip() or email.split("@")[0]},
    })
    if status != 200:
        return False, _error_message(status, body)
    if body.get("access_token"):
        # Email confirmation disabled → we already have a session.
        _store_session(body)
        return True, "Account created."
    return True, "Account created. Check your email to confirm it, then log in."


def login(email: str, password: str) -> tuple[bool, str]:
    status, body = _request("/token?grant_type=password", {
        "email": email.strip().lower(),
        "password": password,
    })
    if status != 200 or "access_token" not in body:
        return False, _error_message(status, body)
    _store_session(body)
    return True, "Logged in."


def change_password(current_password: str, new_password: str) -> tuple[bool, str]:
    """Change the signed-in user's password. Requires the current password —
    an unlocked laptop must not be enough to take over the account."""
    email = st.session_state.get("user_email")
    token = st.session_state.get("session_token")
    if not email or not token:
        return False, "Not signed in."
    if len(new_password) < _MIN_PASSWORD_LEN:
        return False, f"New password must be at least {_MIN_PASSWORD_LEN} characters."
    if new_password == current_password:
        return False, "New password must be different from the current one."
    # Re-authenticate before changing anything.
    status, body = _request("/token?grant_type=password", {"email": email, "password": current_password})
    if status != 200 or "access_token" not in body:
        return False, "Current password is incorrect."
    # Update via the browser's own session token — if GoTrue is configured to
    # revoke other sessions on password change, this one must survive.
    status, body = _request("/user", {"password": new_password}, token=token, method="PUT")
    if status != 200:
        return False, _error_message(status, body)
    return True, "Password updated."


def logout() -> None:
    token = st.session_state.get("session_token")
    if token:
        _request("/logout", {}, token=token)
    for key in ("user_id", "user_email", "display_name", "session_token",
                "_sb_refresh_token", "_sb_expires_at"):
        st.session_state.pop(key, None)
    st.query_params.pop("session", None)


def resolve_session(access_token: str) -> dict | None:
    """Validate an access token against GoTrue; used by main.py (FastAPI) the
    same way local_auth.resolve_session is on SQLite. Returns the user dict
    or None."""
    if not access_token:
        return None
    status, body = _request("/user", token=access_token, method="GET")
    if status != 200 or "id" not in body:
        return None
    return _user_dict(body)


def _refresh(refresh_token: str) -> bool:
    status, body = _request("/token?grant_type=refresh_token", {"refresh_token": refresh_token})
    if status != 200 or "access_token" not in body:
        return False
    _store_session(body)
    return True


def current_user() -> dict | None:
    # Fast path: this WebSocket session already authenticated. Refresh the
    # access token shortly before it expires so long-lived tabs keep working.
    if "user_id" in st.session_state:
        if time.time() > st.session_state.get("_sb_expires_at", 0) - 60:
            if not _refresh(st.session_state.get("_sb_refresh_token", "")):
                logout()
                return None
        return {
            "id": st.session_state["user_id"],
            "email": st.session_state.get("user_email"),
            "display_name": st.session_state.get("display_name"),
        }

    # Page reload: restore from the refresh token carried in the URL.
    refresh_token = st.query_params.get("session")
    if not refresh_token:
        return None
    if not _refresh(refresh_token):
        st.query_params.pop("session", None)
        return None
    return current_user()


def require_login() -> dict:
    """Render the shared login/signup page and st.stop() until authenticated."""
    user = current_user()
    if user:
        return user
    from auth.login_ui import render_login_page
    render_login_page(login=login, register=register, reset_password=None)
    raise RuntimeError("unreachable: render_login_page calls st.stop()")
