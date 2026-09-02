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

from sqlalchemy import Engine, event
from sqlmodel import Session, create_engine

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    is_sqlite = settings.database_url.startswith("sqlite")
    connect_args: dict[str, object] = {}
    if is_sqlite:
        connect_args["check_same_thread"] = False
    engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)

    if is_sqlite:
        # Multi-process feature (2026-09): the hub and each per-account
        # worker process (backend/scripts/worker.py) now write to this same
        # SQLite file concurrently. SQLite's default rollback-journal mode
        # takes an exclusive lock per write and has a *zero* busy timeout,
        # so a second writer fails immediately with "database is locked"
        # the moment two processes ever overlap. WAL lets readers proceed
        # against a writer, and busy_timeout gives a real writer-vs-writer
        # a chance to queue instead of failing outright. Set on every new
        # DBAPI connection (self-installing -- WAL itself is sticky in the
        # file header, but PRAGMAs are per-connection) rather than as a
        # manual "run this once" deployment step, so a fresh DB file (a
        # restore, a new environment) can never be forgotten.
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


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
