"""MT5 account CRUD + cross-user isolation (multi-account feature, 2026-09).

The critical case in this file is isolation: sami must never see, edit,
archive, or use as X-Account-Id anything belonging to mustafa, and a
cross-user id must come back 404 (never 403 -- see
app/accounts/service.py::get_accessible's docstring) -- unless that account
is deliberately flagged `is_shared`, the one sanctioned hole in the
boundary (2026-09-04 request: one jointly-operated account both of them
drive from their own logins). The tests at the bottom of this file pin
both halves of that: shared means fully usable by the other operator,
unshared still means invisible.

No "set live" endpoint as of the concurrent-execution "Phase 2" update --
`is_live` is a heartbeat-derived read-only signal now (see
AccountService.is_account_connected / app/accounts/schemas.py), never a
manually-toggled, mutually-exclusive flag."""

from __future__ import annotations

from app.accounts.service import is_account_connected
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
    assert body["is_live"] is False  # never connected -- no heartbeat yet
    assert "mt5_password" not in body
    assert "mt5_password_encrypted" not in body


def test_creating_a_duplicate_login_server_pair_is_refused(
    raw_client, session, seed_user, seed_account, test_credentials
):
    _login(raw_client, test_credentials)
    response = raw_client.post(
        "/api/accounts",
        json={
            "label": "Duplicate",
            "mt5_login": seed_account.mt5_login,
            "mt5_server": seed_account.mt5_server,
            "mt5_password": "whatever",
        },
    )
    assert response.status_code == 409


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
        session, other, label="Mustafa's account", mt5_login="777004", connected=False
    )

    _login(raw_client, test_credentials)
    response = raw_client.patch(f"/api/accounts/{other_account.id}/archive")
    assert response.status_code == 404

    session.refresh(other_account)
    assert other_account.is_active is True


def test_cannot_use_another_user_s_account_id_as_x_account_id_header(
    raw_client, session, seed_user, account_factory, test_credentials
):
    """The real isolation boundary in practice: every account-scoped
    endpoint (settings, trades, dashboard, ...) reads X-Account-Id, and
    that header must be refused for an id sami doesn't own -- not just the
    /api/accounts management endpoints themselves."""
    other = _other_user(session)
    other_account = account_factory(
        session, other, label="Mustafa's connected account", mt5_login="777006"
    )

    _login(raw_client, test_credentials)
    response = raw_client.get("/api/settings", headers={"X-Account-Id": str(other_account.id)})
    assert response.status_code == 404


def test_archiving_a_connected_account_is_refused(
    raw_client, seed_user, seed_account, test_credentials
):
    assert is_account_connected(seed_account)  # sanity: the fixture default is `connected=True`
    _login(raw_client, test_credentials)

    response = raw_client.patch(f"/api/accounts/{seed_account.id}/archive")
    assert response.status_code == 409


def test_archiving_a_disconnected_account_succeeds(
    raw_client, session, seed_user, account_factory, test_credentials
):
    disconnected = account_factory(
        session, seed_user, label="Old account", mt5_login="555099", connected=False
    )
    _login(raw_client, test_credentials)

    response = raw_client.patch(f"/api/accounts/{disconnected.id}/archive")
    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_multiple_accounts_can_be_connected_at_once_even_across_users(
    session, seed_user, seed_account, account_factory
):
    """The point of the concurrent-execution "Phase 2" update: unlike the
    old `is_live` toggle (mutually exclusive, demoted whichever account was
    previously live), heartbeat-derived connectivity has no such limit --
    two accounts, even belonging to different users, can both be genuinely
    connected (each via its own worker process) at the same time."""
    other = _other_user(session)
    other_account = account_factory(session, other, label="Mustafa's account", mt5_login="777007")

    assert is_account_connected(seed_account)
    assert is_account_connected(other_account)


# -- shared accounts (2026-09-04 request) --------------------------------
# The deliberate exception to everything above: `is_shared` makes one
# account read as *every* operator's own. Everything not flagged stays
# private -- that's what all the isolation tests above still cover.


def test_shared_account_of_another_user_appears_in_this_user_s_list(
    raw_client, session, seed_user, seed_account, account_factory, test_credentials
):
    other = _other_user(session)
    shared = account_factory(
        session, other, label="Joint Axi account", mt5_login="777010", is_shared=True
    )

    _login(raw_client, test_credentials)
    body = raw_client.get("/api/accounts").json()

    assert [a["id"] for a in body] == [seed_account.id, shared.id]
    assert [a["is_shared"] for a in body] == [False, True]


def test_shared_account_of_another_user_can_be_read_and_edited(
    raw_client, session, seed_user, account_factory, test_credentials
):
    """A shared account is not read-only for the other operator -- both of
    them run it, so it behaves exactly like their own (see
    AccountService.get_accessible)."""
    other = _other_user(session)
    shared = account_factory(
        session, other, label="Joint Axi account", mt5_login="777011", is_shared=True
    )

    _login(raw_client, test_credentials)
    assert raw_client.get(f"/api/accounts/{shared.id}").status_code == 200

    response = raw_client.patch(f"/api/accounts/{shared.id}", json={"label": "Renamed jointly"})
    assert response.status_code == 200

    session.refresh(shared)
    assert shared.label == "Renamed jointly"
    assert shared.user_id == other.id  # ownership itself never moves


def test_shared_account_is_accepted_as_x_account_id_by_the_other_user(
    raw_client, session, seed_user, account_factory, test_credentials
):
    """The half that actually matters day to day: every account-scoped
    page (الإعدادات، الصفقات، اللوحة...) must work for the shared account
    under the other operator's login, not just the /api/accounts list."""
    other = _other_user(session)
    shared = account_factory(
        session, other, label="Joint Axi account", mt5_login="777012", is_shared=True
    )

    _login(raw_client, test_credentials)
    response = raw_client.get("/api/settings", headers={"X-Account-Id": str(shared.id)})
    assert response.status_code == 200


def test_unsharing_an_account_hides_it_from_the_other_user_again(
    raw_client, session, seed_user, account_factory, test_credentials
):
    other = _other_user(session)
    shared = account_factory(
        session, other, label="Joint Axi account", mt5_login="777013", is_shared=True
    )

    _login(raw_client, test_credentials)
    unshare = raw_client.patch(f"/api/accounts/{shared.id}", json={"is_shared": False})
    assert unshare.status_code == 200

    assert raw_client.get(f"/api/accounts/{shared.id}").status_code == 404
    assert [a["id"] for a in raw_client.get("/api/accounts").json()] == []
    assert (
        raw_client.get("/api/settings", headers={"X-Account-Id": str(shared.id)}).status_code == 404
    )


def test_account_created_as_shared_is_visible_to_the_other_user(
    raw_client, session, seed_user, test_credentials
):
    other = _other_user(session)

    _login(raw_client, test_credentials)
    created = raw_client.post(
        "/api/accounts",
        json={
            "label": "Joint Axi account",
            "mt5_login": "555003",
            "mt5_server": "Axi-Demo",
            "mt5_password": "some-mt5-password",
            "is_shared": True,
        },
    ).json()
    assert created["is_shared"] is True

    from app.accounts.service import AccountService

    visible = AccountService(session).list_for_user(other.id)
    assert [a.id for a in visible] == [created["id"]]
