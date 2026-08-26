"""Shared SQLModel metadata.

A fixed naming convention is required for Alembic's SQLite ``batch_alter_table``
support (used by the test suite) to be able to drop/rename unnamed
constraints deterministically.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlmodel import SQLModel

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SQLModel.metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(SQLModel):
    """Base class for all ORM table models."""
