"""Shared pytest fixtures.

The whole test session runs against one throwaway SQLite file (set before
any `app.*` module is imported, so `app.core.config.get_settings()` and
`app.database.session.get_engine()` resolve to it naturally — no dependency
overrides needed, tests exercise the exact same code path production does).
Each test gets a clean set of tables and fresh in-memory singletons
(Paper Broker, Mock Signal Source) via the autouse `clean_state` fixture.
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

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel  # noqa: E402

import app.database.models  # noqa: E402,F401  (populate SQLModel.metadata)
from app.core.runtime import reset_runtime_singletons  # noqa: E402
from app.database.session import get_engine  # noqa: E402


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
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
