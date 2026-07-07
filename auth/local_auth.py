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
    """Render a login/signup form and st.stop() until the user is authenticated.

    Call this at the top of streamlit_app.py once wired in. Returns the
    current_user() dict on every rerun after a successful login.
    """
    init_auth_db()
    user = current_user()
    if user:
        return user

    st.markdown(
        """
        <style>
        #MainMenu, header, footer{visibility:hidden}
        .stApp{background:#0c1320}
        .block-container{padding:0 !important;max-width:100% !important}
        section[data-testid="stSidebar"]{display:none}
        div[data-testid="stHorizontalBlock"]{gap:0 !important;align-items:stretch}

        /* ── Left marketing panel ── */
        .vermo-panel{
            min-height:100vh;padding:64px 56px;position:relative;overflow:hidden;
            background:
                radial-gradient(circle at 18% 12%, #1ab58533, transparent 45%),
                radial-gradient(circle at 85% 85%, #16806a4d, transparent 50%),
                linear-gradient(160deg,#0c1320 0%,#0e1b1c 55%,#0c2420 100%);
        }
        .vermo-panel::before{
            content:'';position:absolute;inset:0;opacity:.5;
            background-image:radial-gradient(rgba(255,255,255,.06) 1px, transparent 1px);
            background-size:26px 26px;mask-image:radial-gradient(ellipse 80% 60% at 30% 20%, black, transparent);
        }
        .vermo-panel-inner{position:relative;z-index:1;max-width:420px}
        .vermo-mark{
            width:46px;height:46px;border-radius:12px;
            background:linear-gradient(135deg,#16806a,#1ab585);
            display:flex;align-items:center;justify-content:center;
            color:white;font-weight:900;font-size:21px;margin-bottom:36px;
            box-shadow:0 8px 22px rgba(26,181,133,.35)
        }
        .vermo-panel-title{
            font-size:34px;font-weight:800;color:#f4f7fa;line-height:1.22;
            letter-spacing:-.6px;margin-bottom:14px
        }
        .vermo-panel-sub{
            font-size:15px;color:#9aa8b8;line-height:1.6;margin-bottom:42px;font-weight:400
        }
        .vermo-feature{display:flex;align-items:flex-start;gap:13px;margin-bottom:22px}
        .vermo-feature-icon{
            width:30px;height:30px;border-radius:8px;flex-shrink:0;
            background:rgba(26,181,133,.14);border:1px solid rgba(26,181,133,.3);
            display:flex;align-items:center;justify-content:center;font-size:14px
        }
        .vermo-feature-text{font-size:13.5px;color:#c4cdd8;line-height:1.55;padding-top:5px}
        .vermo-feature-text strong{color:#eef2f6;font-weight:700}
        .vermo-quote{
            margin-top:48px;padding-top:24px;border-top:1px solid rgba(255,255,255,.08);
            font-size:12.5px;color:#7c8a9a;line-height:1.6
        }

        /* ── Right form panel ──
           Streamlit renders each st.markdown/widget call as a separate sibling
           element — a <div> opened in one call and closed in another never
           actually wraps anything, so the panel chrome must be applied to the
           real column element Streamlit generates, not a custom wrapper div. */
        div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(2){
            background:#f7f9fb;display:flex;flex-direction:column;
            align-items:center;justify-content:center;
            min-height:100vh;padding:48px 24px;
            color:#172033 !important;
        }
        /* Broad fallback: force readable dark text on the whole right panel first,
           then override specific pieces (buttons, placeholders, muted captions)
           below with more specific selectors so they still win the cascade. */
        div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(2) *{
            color:#172033;
        }
        /* Cap the form's width and let the flex-centered column do the centering,
           instead of nesting st.columns inside st.columns — that nested-column
           trick squeezes to an oddly narrow strip on smaller viewports. */
        div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"]:nth-of-type(2) > div{
            width:100%;max-width:380px;
        }
        .vermo-form-head{margin-bottom:28px}
        .vermo-form-title{font-size:24px;line-height:1.3;font-weight:800;color:#172033 !important;letter-spacing:-.4px;margin-bottom:6px;white-space:nowrap}
        .vermo-form-sub{font-size:13.5px;color:#667085 !important;font-weight:500}

        div[data-testid="stForm"]{
            background:transparent;border:none;padding:0;box-shadow:none
        }
        /* Backgrounds are set on BOTH the root wrapper (input + the password
           show/hide button, as one pill) AND the input itself — belt and
           suspenders, since which layer actually paints the visible box
           varies and only setting one of them left the other showing through
           as an unstyled dark box. */
        div[data-testid="stForm"] div[data-testid="stTextInputRootElement"]{
            border:1.5px solid #dde3ea !important;border-radius:10px !important;
            background:#ffffff !important;overflow:hidden;
            transition:border-color .15s, box-shadow .15s
        }
        div[data-testid="stForm"] div[data-testid="stTextInputRootElement"]:focus-within{
            border-color:#16806a !important;box-shadow:0 0 0 3px #16806a1f !important
        }
        div[data-testid="stForm"] div[data-testid="stTextInput"] input{
            border:none !important;background:#ffffff !important;
            padding:12px 14px !important;font-size:14px !important;
            color:#172033 !important;caret-color:#172033 !important;
        }
        div[data-testid="stForm"] div[data-testid="stTextInput"] input::placeholder{
            color:#9aa3b0 !important;opacity:1 !important
        }
        div[data-testid="stForm"] div[data-testid="stTextInputRootElement"] button{
            background:#ffffff !important;border:none !important
        }
        div[data-testid="stForm"] div[data-testid="stTextInputRootElement"] button svg{
            fill:#667085 !important
        }
        div[data-testid="stForm"] label p{
            font-size:12.5px !important;font-weight:700 !important;color:#3c4656 !important;
            letter-spacing:.2px
        }
        div[data-testid="stForm"] div[data-testid="stFormSubmitButton"] button{
            width:100%;background:#16806a;border:1px solid #16806a;border-radius:10px;
            padding:12px 0;font-weight:700;font-size:14.5px;margin-top:10px;
            box-shadow:0 4px 14px rgba(22,128,106,.22);transition:opacity .15s, transform .1s
        }
        div[data-testid="stForm"] div[data-testid="stFormSubmitButton"] button,
        div[data-testid="stForm"] div[data-testid="stFormSubmitButton"] button *{
            color:#ffffff !important
        }
        div[data-testid="stForm"] div[data-testid="stFormSubmitButton"] button:hover{opacity:.9}
        div[data-testid="stForm"] div[data-testid="stFormSubmitButton"] button:active{transform:scale(.99)}
        div[data-testid="stTabs"] button[role="tab"]{font-weight:700;font-size:14px}
        div[data-testid="stTabs"] button[role="tab"] p{color:#445063 !important}
        div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] p{color:#16806a !important}
        div[data-testid="stTabs"] div[data-baseweb="tab-highlight"]{background:#16806a}

        @media (max-width: 900px){
            .vermo-panel{display:none}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([1, 1], gap="small")

    with left:
        st.markdown(
            """
            <div class="vermo-panel">
              <div class="vermo-panel-inner">
                <div class="vermo-mark">V</div>
                <div class="vermo-panel-title">Your wealth,<br/>one clear view.</div>
                <div class="vermo-panel-sub">
                  Track investments across India, Germany, and global markets —
                  alongside debt, budget, and everything else you own.
                </div>
                <div class="vermo-feature">
                  <span class="vermo-feature-icon">📈</span>
                  <span class="vermo-feature-text"><strong>Live portfolio tracking</strong><br/>India + Global markets, real-time prices, one currency view.</span>
                </div>
                <div class="vermo-feature">
                  <span class="vermo-feature-icon">🏦</span>
                  <span class="vermo-feature-text"><strong>Debt &amp; budget, together</strong><br/>Loan payoff modeling and monthly cash flow in the same place.</span>
                </div>
                <div class="vermo-feature">
                  <span class="vermo-feature-icon">🏠</span>
                  <span class="vermo-feature-text"><strong>Beyond the market</strong><br/>Real estate, cash, and private investments included in your net worth.</span>
                </div>
                <div class="vermo-quote">"The clearest way to see what you actually own."</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right:
        tab_login, tab_signup = st.tabs(["Log in", "Create account"])

        with tab_login:
            if st.session_state.get("show_password_reset"):
                st.markdown(
                    """
                    <div class="vermo-form-head">
                      <div class="vermo-form-title">Reset your password</div>
                      <div class="vermo-form-sub">Enter your account email and choose a new password.</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                with st.form("reset_password_form"):
                    reset_email = st.text_input("Email", placeholder="you@example.com")
                    reset_password_input = st.text_input(
                        "New password", type="password", placeholder="At least 8 characters",
                    )
                    if st.form_submit_button("Reset password", type="primary"):
                        ok, message = reset_password(reset_email, reset_password_input)
                        if ok:
                            st.session_state["show_password_reset"] = False
                            st.success(message)
                        else:
                            st.error(message)
                if st.button("← Back to log in"):
                    st.session_state["show_password_reset"] = False
                    st.rerun()
            else:
                st.markdown(
                    """
                    <div class="vermo-form-head">
                      <div class="vermo-form-title">Welcome back</div>
                      <div class="vermo-form-sub">Log in to see where things stand.</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                with st.form("login_form"):
                    email = st.text_input("Email", placeholder="you@example.com")
                    password = st.text_input("Password", type="password", placeholder="••••••••")
                    if st.form_submit_button("Log in", type="primary"):
                        ok, message = login(email, password)
                        if ok:
                            st.rerun()
                        else:
                            st.error(message)
                if st.button("Forgot password?"):
                    st.session_state["show_password_reset"] = True
                    st.rerun()

        with tab_signup:
            st.markdown(
                """
                <div class="vermo-form-head">
                  <div class="vermo-form-title">Create your account</div>
                  <div class="vermo-form-sub">Takes under a minute — no card required.</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            with st.form("signup_form"):
                new_name = st.text_input("Display name", placeholder="How should we address you?")
                new_email = st.text_input("Email", key="signup_email", placeholder="you@example.com")
                new_password = st.text_input(
                    "Password", type="password", key="signup_password",
                    placeholder="At least 8 characters",
                )
                if st.form_submit_button("Create account", type="primary"):
                    ok, message = register(new_email, new_password, new_name)
                    if ok:
                        st.success(message + " Log in from the other tab.")
                    else:
                        st.error(message)

    st.stop()
