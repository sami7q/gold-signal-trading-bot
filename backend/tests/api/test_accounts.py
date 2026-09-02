"""MT5 account CRUD + cross-user isolation (multi-account feature, 2026-09).

The critical case in this file is isolation: sami must never see, edit,
archive, or use as X-Account-Id anything belonging to mustafa, and a
cross-user id must come back 404 (never 403 -- see
app/accounts/service.py::get_owned's docstring)."""

from __future__ import annotations

from app.accounts.schemas import LIVE_CONFIRMATION_PHRASE
from app.core.security import hash_password
from app.database.models.user import User


def _other_user(session, *, username="mustafa") -> User:
    user = User(
        username=username, display_name="Mustafa", password_hash=hash_password("irrelevant-pw-1")
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _login(raw_client, test_credentials) -> None:
    username, password = test_credentials
    response = raw_client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def test_list_accounts_only_returns_the_caller_s_own(
    raw_client, session, seed_user, seed_account, account_factory, test_credentials
):
    other = _other_user(session)
    account_factory(session, other, label="Mustafa's account", mt5_login="777001")

    _login(raw_client, test_credentials)
    body = raw_client.get("/api/accounts").json()

    assert [a["id"] for a in body] == [seed_account.id]


def test_create_account_is_owned_by_the_caller(raw_client, seed_user, test_credentials):
    _login(raw_client, test_credentials)
    response = raw_client.post(
        "/api/accounts",
        json={
            "label": "My second account",
            "mt5_login": "555002",
            "mt5_server": "Broker-Demo",
            "mt5_password": "some-mt5-password",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["label"] == "My second account"
    assert body["has_password"] is True
    assert "mt5_password" not in body
    assert "mt5_password_encrypted" not in body


def test_cannot_read_another_user_s_account(
    raw_client, session, seed_user, account_factory, test_credentials
):
    other = _other_user(session)
    other_account = account_factory(session, other, label="Mustafa's account", mt5_login="777002")

    _login(raw_client, test_credentials)
    response = raw_client.get(f"/api/accounts/{other_account.id}")

    # 404, never 403 -- must not confirm the id exists at all.
    assert response.status_code == 404


def test_cannot_update_another_user_s_account(
    raw_client, session, seed_user, account_factory, test_credentials
):
    other = _other_user(session)
    other_account = account_factory(session, other, label="Mustafa's account", mt5_login="777003")

    _login(raw_client, test_credentials)
    response = raw_client.patch(
        f"/api/accounts/{other_account.id}", json={"label": "Hijacked label"}
    )
    assert response.status_code == 404

    session.refresh(other_account)
    assert other_account.label == "Mustafa's account"  # untouched


def test_cannot_archive_another_user_s_account(
    raw_client, session, seed_user, account_factory, test_credentials
):
    other = _other_user(session)
    other_account = account_factory(
        session, other, label="Mustafa's account", mt5_login="777004", is_live=False
    )

    _login(raw_client, test_credentials)
    response = raw_client.patch(f"/api/accounts/{other_account.id}/archive")
    assert response.status_code == 404

    session.refresh(other_account)
    assert other_account.is_active is True


def test_cannot_set_another_user_s_account_live(
    raw_client, session, seed_user, account_factory, test_credentials
):
    other = _other_user(session)
    other_account = account_factory(
        session, other, label="Mustafa's account", mt5_login="777005", is_live=False
    )

    _login(raw_client, test_credentials)
    response = raw_client.post(
        f"/api/accounts/{other_account.id}/set-live",
        json={"confirm_phrase": LIVE_CONFIRMATION_PHRASE},
    )
    assert response.status_code == 404

    session.refresh(other_account)
    assert other_account.is_live is False


def test_cannot_use_another_user_s_account_id_as_x_account_id_header(
    raw_client, session, seed_user, account_factory, test_credentials
):
    """The real isolation boundary in practice: every account-scoped
    endpoint (settings, trades, dashboard, ...) reads X-Account-Id, and
    that header must be refused for an id sami doesn't own -- not just the
    /api/accounts management endpoints themselves."""
    other = _other_user(session)
    other_account = account_factory(
        session, other, label="Mustafa's live account", mt5_login="777006", is_live=True
    )

    _login(raw_client, test_credentials)
    response = raw_client.get("/api/settings", headers={"X-Account-Id": str(other_account.id)})
    assert response.status_code == 404


def test_archiving_a_live_account_is_refused(raw_client, seed_user, seed_account, test_credentials):
    assert seed_account.is_live is True
    _login(raw_client, test_credentials)

    response = raw_client.patch(f"/api/accounts/{seed_account.id}/archive")
    assert response.status_code == 409


def test_set_live_requires_the_exact_confirmation_phrase(
    raw_client, session, seed_user, account_factory, test_credentials
):
    second = account_factory(session, seed_user, label="Second", mt5_login="555010", is_live=False)
    _login(raw_client, test_credentials)

    wrong = raw_client.post(f"/api/accounts/{second.id}/set-live", json={"confirm_phrase": "nope"})
    assert wrong.status_code == 400

    right = raw_client.post(
        f"/api/accounts/{second.id}/set-live",
        json={"confirm_phrase": LIVE_CONFIRMATION_PHRASE},
    )
    assert right.status_code == 200
    assert right.json()["is_live"] is True


def test_setting_a_new_account_live_demotes_the_previous_one_even_across_users(
    session, seed_user, seed_account, account_factory
):
    """The one genuinely global piece of state (Phase 1's single-broker-
    connection constraint) -- confirms it really does demote across the
    user boundary, not just within one user's own accounts."""
    assert seed_account.is_live is True
    other = _other_user(session)
    other_account = account_factory(
        session, other, label="Mustafa's account", mt5_login="777007", is_live=False
    )

    from app.accounts.service import AccountService

    AccountService(session).set_live(other_account, confirm_phrase=LIVE_CONFIRMATION_PHRASE)

    session.refresh(seed_account)
    session.refresh(other_account)
    assert seed_account.is_live is False
    assert other_account.is_live is True
