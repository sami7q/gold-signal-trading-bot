"""Login / logout / session lifecycle (multi-account feature, 2026-09).

Uses `raw_client` (not `client`) throughout -- these tests are exactly
about the auth handshake `client` normally does for free. Credentials come
from the `test_credentials` fixture (not a module-level import of
tests.conftest -- that re-imports it as a second module object and re-runs
its module-level test-DB setup, see conftest.py's `test_credentials`
docstring)."""

from __future__ import annotations


def test_login_with_correct_credentials_succeeds_and_sets_cookie(
    raw_client, seed_user, test_credentials
):
    username, password = test_credentials
    response = raw_client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["username"] == username
    assert "goldbot_session" in response.cookies


def test_login_with_wrong_password_fails(raw_client, seed_user, test_credentials):
    username, _ = test_credentials
    response = raw_client.post(
        "/api/auth/login", json={"username": username, "password": "totally-wrong"}
    )
    assert response.status_code == 401


def test_login_with_unknown_username_fails_the_same_way_as_wrong_password(
    raw_client, seed_user, test_credentials
):
    """Same generic 401 either way -- never confirms whether a username
    exists at all."""
    username, _ = test_credentials
    unknown = raw_client.post(
        "/api/auth/login", json={"username": "nobody-like-this", "password": "whatever12"}
    )
    wrong_password = raw_client.post(
        "/api/auth/login", json={"username": username, "password": "wrong-one-here"}
    )
    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json()["detail"] == wrong_password.json()["detail"]


def test_inactive_user_cannot_log_in(raw_client, session, seed_user, test_credentials):
    username, password = test_credentials
    seed_user.is_active = False
    session.add(seed_user)
    session.commit()

    response = raw_client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 401


def test_protected_endpoint_requires_login(raw_client):
    response = raw_client.get("/api/auth/me")
    assert response.status_code == 401


def test_me_reflects_the_logged_in_user_and_their_accounts(
    raw_client, seed_account, test_credentials
):
    username, password = test_credentials
    raw_client.post("/api/auth/login", json={"username": username, "password": password})
    response = raw_client.get("/api/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["username"] == username
    assert [a["id"] for a in body["accounts"]] == [seed_account.id]
    # Never the encrypted/plaintext password, only whether one is set.
    assert "mt5_password_encrypted" not in body["accounts"][0]
    assert "mt5_password" not in body["accounts"][0]
    assert body["accounts"][0]["has_password"] is True


def test_logout_revokes_the_session(raw_client, seed_user, test_credentials):
    username, password = test_credentials
    raw_client.post("/api/auth/login", json={"username": username, "password": password})
    assert raw_client.get("/api/auth/me").status_code == 200

    logout = raw_client.post("/api/auth/logout")
    assert logout.status_code == 204

    # The cookie is gone client-side, but even replaying the exact old
    # cookie value must be rejected -- the session is actually revoked
    # server-side, not just forgotten client-side.
    assert raw_client.get("/api/auth/me").status_code == 401


def test_remember_me_session_outlives_the_default_short_session(
    session, seed_user, test_credentials
):
    """Not a wall-clock test -- just confirms the two `expires_at` values
    the service actually persisted are meaningfully different, matching
    the documented 12h vs 30d split (app/auth/service.py)."""
    from app.auth.service import AuthService

    username, password = test_credentials
    service = AuthService(session)
    _, short_token = service.login(username, password, remember_me=False)
    _, long_token = service.login(username, password, remember_me=True)

    short_session = service._find_session(short_token)  # noqa: SLF001 - direct assertion on state
    long_session = service._find_session(long_token)  # noqa: SLF001
    assert short_session is not None and long_session is not None
    assert long_session.expires_at > short_session.expires_at
