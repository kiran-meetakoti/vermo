"""Tests for FastAPI session-token auth (require_user_id in main.py).

The roadmap's Stage 1.5 gap was that every endpoint trusted a client-supplied
user_id query param. These tests pin down the fix: user identity comes only
from the Bearer session token that auth/local_auth issues, a missing/invalid
token is a 401, and one user's token can never read or write another user's
data — even when a user_id query param names the other user explicitly.

main.connect() and local_auth._connect() read their DB-path module globals at
call time, so pointing DATABASE_FILE / AUTH_DB at tmp_path isolates the whole
stack; LEGACY_DATA_FILE is pointed at a missing file so the JSON bootstrap in
init_database() stays out of the picture.
"""

import pytest
from fastapi.testclient import TestClient

import main
from auth import local_auth


def _make_user(email: str) -> tuple[str, str]:
    """Register a user directly against auth.db and open a session for them,
    without going through the Streamlit login flow. Returns (user_id, token)."""
    ok, msg = local_auth.register(email, "testpassword123", email.split("@")[0])
    assert ok, msg
    with local_auth._connect() as conn:
        user_id = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]
    token = local_auth._create_session(user_id)
    return user_id, token


HOLDING = {
    "name": "Vanguard FTSE All-World",
    "ticker": "VWCE",
    "market": "Global",
    "value_eur": 1000.0,
    "return_percent": 5.0,
    "asset_class": "ETFs & Funds",
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DATABASE_FILE", tmp_path / "portfolio.db")
    monkeypatch.setattr(main, "LEGACY_DATA_FILE", tmp_path / "no-legacy.json")
    monkeypatch.setattr(local_auth, "AUTH_DB", tmp_path / "auth.db")
    local_auth.init_auth_db()
    main.init_database()
    return TestClient(main.app)


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("path", ["/api/dashboard", "/api/holdings", "/api/imports", "/api/snapshots"])
def test_data_routes_require_token(client, path):
    assert client.get(path).status_code == 401


def test_refresh_and_write_routes_require_token(client):
    assert client.post("/api/prices/refresh").status_code == 401
    assert client.post("/api/holdings", json=HOLDING).status_code == 401
    assert client.delete("/api/holdings/some-id").status_code == 401


def test_garbage_token_rejected(client):
    response = client.get("/api/holdings", headers=auth_header("not-a-real-token"))
    assert response.status_code == 401


def test_non_bearer_authorization_rejected(client):
    response = client.get("/api/holdings", headers={"Authorization": "Basic abc123"})
    assert response.status_code == 401


def test_valid_token_resolves_to_its_own_user(client):
    user_id, token = _make_user("owner@example.com")
    created = client.post("/api/holdings", json=HOLDING, headers=auth_header(token))
    assert created.status_code == 201
    assert created.json()["user_id"] == user_id

    listed = client.get("/api/holdings", headers=auth_header(token))
    assert listed.status_code == 200
    assert [h["ticker"] for h in listed.json()] == ["VWCE"]


def test_users_cannot_see_each_others_data(client):
    _, token_a = _make_user("alice@example.com")
    _, token_b = _make_user("bob@example.com")
    assert client.post("/api/holdings", json=HOLDING, headers=auth_header(token_a)).status_code == 201

    assert client.get("/api/holdings", headers=auth_header(token_b)).json() == []
    assert client.get("/api/holdings", headers=auth_header(token_a)).json() != []


def test_user_id_query_param_is_ignored(client):
    """The Stage 1.5 regression check: naming someone else's user_id in the
    query string must not grant access to their data — identity comes from the
    token alone."""
    user_a, token_a = _make_user("alice@example.com")
    _, token_b = _make_user("bob@example.com")
    client.post("/api/holdings", json=HOLDING, headers=auth_header(token_a))

    response = client.get(f"/api/holdings?user_id={user_a}", headers=auth_header(token_b))
    assert response.status_code == 200
    assert response.json() == []


def test_cross_user_delete_is_rejected(client):
    _, token_a = _make_user("alice@example.com")
    _, token_b = _make_user("bob@example.com")
    holding_id = client.post("/api/holdings", json=HOLDING, headers=auth_header(token_a)).json()["id"]

    assert client.delete(f"/api/holdings/{holding_id}", headers=auth_header(token_b)).status_code == 404
    # Still there for its owner, and deletable by them.
    assert client.delete(f"/api/holdings/{holding_id}", headers=auth_header(token_a)).status_code == 204


def test_expired_session_rejected(client):
    _, token = _make_user("owner@example.com")
    stale = "2020-01-01T00:00:00+00:00"
    with local_auth._connect() as conn:
        conn.execute("UPDATE sessions SET last_active_at = ? WHERE token = ?", (stale, token))

    assert client.get("/api/holdings", headers=auth_header(token)).status_code == 401
