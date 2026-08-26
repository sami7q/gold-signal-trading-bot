"""Database engine and session management.

Synchronous SQLAlchemy is used throughout the backend (see docs/01-architecture.md
for the rationale — the MetaTrader5 package is itself synchronous, so keeping
the whole persistence + trading path sync avoids a mixed async/sync engine).

Production/dev target PostgreSQL (`DATABASE_URL`); the test suite points this
at SQLite, which is why no Postgres-only column types are used anywhere in
`database/models/`.
"""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    connect_args: dict[str, object] = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(settings.database_url, echo=False, connect_args=connect_args)


def create_db_and_tables() -> None:
    """Create all tables directly from models.

    Used by the test suite (SQLite, ephemeral) and as a dev convenience.
    Real deployments must use Alembic migrations instead (`alembic upgrade head`).
    """
    from sqlmodel import SQLModel

    import app.database.models  # noqa: F401  (registers all table models)

    SQLModel.metadata.create_all(get_engine())


def get_session() -> Generator[Session, None, None]:
    with Session(get_engine()) as session:
        yield session
