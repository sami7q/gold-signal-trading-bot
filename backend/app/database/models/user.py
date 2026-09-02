"""Dashboard login (multi-account feature, 2026-09). Pre-seeded only -- see
`backend/scripts/seed_users.py`, no public signup endpoint exists.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field

from app.database.base import Base
from app.database.types import UTCDateTime


class User(Base, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)

    #: Always stored lowercase; login also lowercases the submitted
    #: username before lookup so "Sami"/"sami" are the same account.
    username: str = Field(unique=True, index=True)
    display_name: str

    #: `pbkdf2_sha256$...` -- see app/core/security.py::hash_password.
    #: Never a real password until backend/scripts/seed_users.py sets it.
    password_hash: str

    is_active: bool = True

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
