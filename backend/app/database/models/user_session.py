"""DB-backed login sessions (multi-account feature, 2026-09) -- deliberately
not a stateless JWT: this codebase has no JWT/crypto-token dependency
anywhere, and "remember me" needs real revocation on logout (a stateless
token needs a denylist anyway, which is just this table under another
name). See app/core/auth_deps.py for how a session cookie is validated
against this table.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field

from app.database.base import Base
from app.database.types import UTCDateTime


class UserSession(Base, table=True):
    __tablename__ = "user_sessions"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)

    #: sha256 hex digest of the raw session token (a secrets.token_urlsafe(32)
    #: value, 256 bits of entropy) -- the raw token is sent to the browser
    #: exactly once (Set-Cookie at login) and never stored. Deliberately NOT
    #: hash_password's PBKDF2: that's for low-entropy user-chosen passwords
    #: needing slow, salted hashing; this token is already high-entropy and
    #: looked up by exact match on every authenticated request, where PBKDF2's
    #: 390k iterations would add needless latency.
    token_hash: str = Field(unique=True, index=True)

    remember_me: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
    expires_at: datetime = Field(sa_type=UTCDateTime, index=True)
    #: Soft-revoke on logout (matches this codebase's archive-don't-delete
    #: idiom elsewhere, e.g. ArchivedSignal) -- never hard-deleted, so a
    #: session's full lifecycle stays in the audit trail.
    revoked_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    user_agent: str | None = None
