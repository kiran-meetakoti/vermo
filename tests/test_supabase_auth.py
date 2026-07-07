"""Tests for auth/supabase_auth.py's change_password flow.

The GoTrue HTTP layer is scripted (monkeypatched _request) — these tests pin
the security-relevant call sequence, not Supabase itself: re-authenticate with
the CURRENT password first, and send the update with the browser session's own
token (so a "revoke other sessions on password change" server setting can't
log out the session that made the change).
"""

import types

import pytest

from auth import supabase_auth


@pytest.fixture()
def auth(monkeypatch):
    fake_st = types.SimpleNamespace(
        session_state={"user_email": "person@example.com", "session_token": "browser-access-token"},
        query_params={},
    )
    monkeypatch.setattr(supabase_auth, "st", fake_st)
    return supabase_auth


def script_requests(monkeypatch, responses):
    """Replace _request with a scripted double; records every call made."""
    calls = []

    def fake_request(path, payload=None, token=None, method="POST"):
        calls.append({"path": path, "payload": payload, "token": token, "method": method})
        return responses[len(calls) - 1]

    monkeypatch.setattr(supabase_auth, "_request", fake_request)
    return calls


def test_change_password_happy_path_uses_session_token_for_update(auth, monkeypatch):
    calls = script_requests(monkeypatch, [
        (200, {"access_token": "reauth-token", "refresh_token": "r", "user": {"id": "u1", "email": "person@example.com"}}),
        (200, {"id": "u1", "email": "person@example.com"}),
    ])
    ok, message = auth.change_password("oldpassword1", "newpassword22")
    assert ok and message == "Password updated."

    reauth, update = calls
    assert reauth["path"].endswith("grant_type=password")
    assert reauth["payload"] == {"email": "person@example.com", "password": "oldpassword1"}
    assert update["method"] == "PUT" and update["path"] == "/user"
    assert update["payload"] == {"password": "newpassword22"}
    # The update must ride the browser session's token, not the re-auth one.
    assert update["token"] == "browser-access-token"


def test_change_password_wrong_current_never_updates(auth, monkeypatch):
    calls = script_requests(monkeypatch, [
        (400, {"error_description": "Invalid login credentials"}),
    ])
    ok, message = auth.change_password("wrongpassword", "newpassword22")
    assert ok is False
    assert "incorrect" in message.lower()
    assert len(calls) == 1  # no PUT /user attempted


def test_change_password_local_validation_needs_no_network(auth, monkeypatch):
    calls = script_requests(monkeypatch, [])
    assert auth.change_password("oldpassword1", "short")[0] is False
    assert auth.change_password("samepassword1", "samepassword1")[0] is False
    assert calls == []


def test_change_password_requires_login(monkeypatch):
    monkeypatch.setattr(supabase_auth, "st", types.SimpleNamespace(session_state={}, query_params={}))
    ok, message = supabase_auth.change_password("a-password-123", "b-password-123")
    assert ok is False
    assert "signed in" in message.lower()


def test_change_password_surfaces_gotrue_update_error(auth, monkeypatch):
    script_requests(monkeypatch, [
        (200, {"access_token": "reauth-token", "refresh_token": "r", "user": {"id": "u1", "email": "person@example.com"}}),
        (422, {"msg": "New password should be different from the old password."}),
    ])
    ok, message = auth.change_password("oldpassword1", "newpassword22")
    assert ok is False
    assert "different" in message.lower()
