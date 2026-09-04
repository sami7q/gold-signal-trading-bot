"""Shared pytest fixtures.

The whole test session runs against one throwaway SQLite file (set before
any `app.*` module is imported, so `app.core.config.get_settings()` and
`app.database.session.get_engine()` resolve to it naturally — no dependency
overrides needed, tests exercise the exact same code path production does).
Each test gets a clean set of tables and fresh in-memory singletons
(Paper Broker, Mock Signal Source) via the autouse `clean_state` fixture.

`Settings` reads `../.env` (the real project env file) by default, so every
safety-relevant flag a test implicitly relies on the documented default for
must be force-overridden here too -- otherwise running the suite on a
machine whose `.env` has been switched to real trading (as happens during
manual live testing) silently changes what the tests exercise. This bit
once: TELEGRAM_OBSERVE_ONLY=false in the real `.env` leaked into a test
that assumed the true default, making it exercise the AUTO_TRADING_DISABLED
path instead of the one it was named for.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_DB_PATH = Path(tempfile.gettempdir()) / "goldbot_test.db"
_TEST_DB_PATH.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"
os.environ["TELEGRAM_ENABLED"] = "false"
os.environ["MT5_ENABLED"] = "false"
#: Must match `make_account`'s defaults below (mt5_login/mt5_server) --
#: concurrent-execution "Phase 2" update: a process resolves "which account
#: am I" by matching these against MT5Account rows (see
#: AccountService.resolve_own_process_account), so `app/lifecycle.py`'s
#: real `lifespan()` (exercised by the `client`/`raw_client` fixtures'
#: `TestClient(app)`) needs a match here or every test would silently
#: resolve to "no account" (account_id=None) regardless of `seed_account`.
os.environ["MT5_ACCOUNT"] = "999001"
os.environ["MT5_SERVER"] = "Test-Server"
os.environ["TELEGRAM_OBSERVE_ONLY"] = "true"
os.environ["AUTO_TRADING"] = "false"
os.environ["TRADING_MODE"] = "SIMULATION"
#: A real Fernet key so MT5Account fixtures can encrypt a placeholder
#: password (app/core/credentials.py) -- one generated per test *process*
#: is fine, nothing here needs it to be stable across runs.
os.environ.setdefault(
    "ACCOUNT_CREDENTIALS_ENCRYPTION_KEY",
    __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode(),
)
#: The real deployment needs Secure+SameSite=None because Vercel and the
#: Cloudflare Tunnel are different HTTPS origins (see Settings.cookie_*'s
#: docstring) -- TestClient talks plain http://testserver, where a
#: Secure-flagged cookie set at login would silently never be resent on
#: later requests, breaking every "authenticated" fixture below in a way
#: that looks like an auth bug rather than a test-transport mismatch.
os.environ["COOKIE_SECURE"] = "false"
os.environ["COOKIE_SAMESITE"] = "lax"

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

import app.database.models  # noqa: E402,F401  (populate SQLModel.metadata)
from app.core.runtime import reset_runtime_singletons  # noqa: E402
from app.database.session import get_engine  # noqa: E402

#: Shared by every fixture/test that needs a real login -- not a secret,
#: just fixture data (see backend/scripts/seed_users.py for the real thing).
TEST_USERNAME = "testuser"
TEST_PASSWORD = "testpassword123"


@pytest.fixture(autouse=True)
def clean_state():
    engine = get_engine()
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    reset_runtime_singletons()
    yield
    reset_runtime_singletons()


@pytest.fixture()
def session():
    with Session(get_engine()) as s:
        yield s


@pytest.fixture()
def seed_user(session):
    """A real, loggable-in `User` row -- see TEST_USERNAME/TEST_PASSWORD."""
    from app.core.security import hash_password
    from app.database.models.user import User

    user = User(
        username=TEST_USERNAME,
        display_name="Test User",
        password_hash=hash_password(TEST_PASSWORD),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def make_account(
    session,
    user,
    *,
    label="Test Account",
    mt5_login="999001",
    mt5_server="Test-Server",
    connected=True,
    is_shared=False,
):
    """Not a fixture itself -- call directly from a test that needs more
    than one account (e.g. cross-user isolation tests), where a single
    fixture instance wouldn't do. `seed_account` below is the one-account
    convenience wrapper around this for everything else.

    `connected` writes (or omits) a fresh `last_heartbeat_at` --
    concurrent-execution "Phase 2" update: the account's wire-level
    `is_live` is computed from that recency (app/accounts/schemas.py), not
    from the model's own `is_live` column, which is dead as of that update
    (see MT5Account's docstring).

    `is_shared` is the deliberate opt-in hole in the per-user isolation
    boundary (2026-09-04 request) -- default False, so every existing
    isolation test keeps exercising a genuinely private account."""
    from datetime import UTC, datetime

    from app.core.credentials import encrypt_mt5_password
    from app.database.models.mt5_account import MT5Account

    account = MT5Account(
        user_id=user.id,
        label=label,
        mt5_login=mt5_login,
        mt5_server=mt5_server,
        mt5_password_encrypted=encrypt_mt5_password("placeholder"),
        last_heartbeat_at=datetime.now(UTC) if connected else None,
        is_shared=is_shared,
    )
    session.add(account)
    session.commit()
    session.refresh(account)
    return account


@pytest.fixture()
def seed_account(session, seed_user):
    """The one MT5 account almost every test needs -- `connected=True`
    (the default) so its wire-level `is_live` reads True, matching the
    real deployment's account (dashboard balance/equity, engine execution,
    etc. all gate on that). `mt5_login`/`mt5_server` default to the same
    values conftest.py sets MT5_ACCOUNT/MT5_SERVER to, so this account is
    also what `app/lifecycle.py`'s real startup resolves as "this
    process's own account" in the `client`/`raw_client` fixtures."""
    return make_account(session, seed_user)


@pytest.fixture()
def account_factory():
    """The `make_account` callable, as a fixture -- for a test that needs
    more than one account (e.g. cross-user isolation tests) where the
    single `seed_account` instance isn't enough. Exists so such a test
    never has to `from tests.conftest import make_account` at module level
    -- same double-import hazard `test_credentials` avoids (see its
    docstring), except here it would fire at collection time instead of
    mid-test, which happens to be less likely to explode but is just as
    fragile to depend on."""
    return make_account


@pytest.fixture()
def test_credentials():
    """(username, password) for `seed_user` -- for a test that builds its
    own raw `TestClient` (instead of using `client`/`raw_client`) and needs
    to log in manually. Exists so such a test never has to
    `from tests.conftest import TEST_USERNAME` mid-test -- that re-imports
    this module as a second, separate module object, re-running the
    module-level env var / test-db setup above against an already-open DB
    file."""
    return TEST_USERNAME, TEST_PASSWORD


@pytest.fixture()
def raw_client():
    """An unauthenticated TestClient -- use this (not `client`) for
    login/auth/account-isolation tests that need to control the cookie and
    X-Account-Id header themselves instead of getting them for free."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def client(raw_client, seed_account):
    """A TestClient already logged in as `seed_user` with `X-Account-Id`
    defaulted to `seed_account` -- every endpoint added by the multi-account
    feature requires both, and almost every existing test only ever cared
    about "a working session against the one account it set up", not about
    exercising auth itself (that's what `raw_client` + test_auth.py /
    test_accounts.py are for)."""
    response = raw_client.post(
        "/api/auth/login", json={"username": TEST_USERNAME, "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    raw_client.headers["X-Account-Id"] = str(seed_account.id)
    yield raw_client
