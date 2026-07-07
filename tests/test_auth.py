"""Tests for local username/password auth, including the password-reset flow.

local_auth.py is importable on its own (it defines functions and imports
streamlit but runs no app code), so we point AUTH_DB at a temp file and stub
the module's `st` reference — the same isolation used to hand-verify the reset
flow when it was added.
"""

import types

import pytest

from auth import local_auth

EMAIL = "person@example.com"
PASSWORD = "originalpass123"


@pytest.fixture()
def auth(tmp_path, monkeypatch):
    monkeypatch.setattr(local_auth, "AUTH_DB", tmp_path / "auth.db")
    fake_st = types.SimpleNamespace(session_state={}, query_params={})
    monkeypatch.setattr(local_auth, "st", fake_st)
    local_auth.init_auth_db()
    return local_auth


def test_register_then_login(auth):
    ok, _ = auth.register(EMAIL, PASSWORD, "Person")
    assert ok
    ok, _ = auth.login(EMAIL, PASSWORD)
    assert ok
    assert auth.st.session_state.get("user_email") == EMAIL


def test_register_rejects_duplicate_email(auth):
    assert auth.register(EMAIL, PASSWORD, "Person")[0] is True
    ok, msg = auth.register(EMAIL, "anotherpass123", "Person Again")
    assert ok is False
    assert "already exists" in msg


def test_register_rejects_short_password(auth):
    ok, msg = auth.register(EMAIL, "short", "Person")
    assert ok is False
    assert "8 characters" in msg


def test_register_rejects_invalid_email(auth):
    assert auth.register("not-an-email", PASSWORD, "Person")[0] is False


def test_login_wrong_password_fails(auth):
    auth.register(EMAIL, PASSWORD, "Person")
    ok, msg = auth.login(EMAIL, "wrongpassword")
    assert ok is False
    assert "Incorrect password" in msg


def test_login_unknown_email_fails(auth):
    ok, msg = auth.login("nobody@example.com", PASSWORD)
    assert ok is False
    assert "No account" in msg


def test_password_is_not_stored_in_plaintext(auth):
    auth.register(EMAIL, PASSWORD, "Person")
    with auth._connect() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE email = ?", (EMAIL,)).fetchone()
    assert PASSWORD not in row["password_hash"]


def test_reset_password_updates_credentials(auth):
    auth.register(EMAIL, PASSWORD, "Person")

    ok, _ = auth.reset_password(EMAIL, "brandnewpass456")
    assert ok

    # Old password no longer works; new one does.
    assert auth.login(EMAIL, PASSWORD)[0] is False
    assert auth.login(EMAIL, "brandnewpass456")[0] is True


def test_reset_password_unknown_email_fails(auth):
    ok, msg = auth.reset_password("nobody@example.com", "brandnewpass456")
    assert ok is False
    assert "No account" in msg


def test_reset_password_rejects_short_password(auth):
    auth.register(EMAIL, PASSWORD, "Person")
    ok, msg = auth.reset_password(EMAIL, "short")
    assert ok is False
    assert "8 characters" in msg


def test_reset_password_invalidates_existing_sessions(auth):
    auth.register(EMAIL, PASSWORD, "Person")
    ok, _ = auth.login(EMAIL, PASSWORD)
    assert ok
    token = auth.st.session_state["session_token"]
    assert auth.resolve_session(token) is not None

    auth.reset_password(EMAIL, "brandnewpass456")
    # The old session token must no longer resolve after a reset.
    assert auth.resolve_session(token) is None
