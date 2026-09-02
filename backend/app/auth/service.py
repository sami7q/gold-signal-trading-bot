"""Login/session business logic (multi-account feature, 2026-09). Pre-seeded
users only (backend/scripts/seed_users.py) -- no signup here or anywhere.
See app/core/auth_deps.py for the FastAPI dependency wrapper that reads the
cookie and calls into this.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from app.audit.service import AuditService
from app.core.constants import AuditEventType, AuditSeverity
from app.core.security import verify_password
from app.database.models.user import User
from app.database.models.user_session import UserSession

SESSION_COOKIE_NAME = "goldbot_session"
_SHORT_SESSION = timedelta(hours=12)
_REMEMBER_ME_SESSION = timedelta(days=30)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


class AuthService:
    def __init__(self, session: Session, audit: AuditService | None = None) -> None:
        self._session = session
        self._audit = audit or AuditService(session)

    def login(
        self, username: str, password: str, *, remember_me: bool, user_agent: str | None = None
    ) -> tuple[User, str] | None:
        """Returns `(user, raw_token)` on success, `None` on any failure
        (unknown username, wrong password, inactive user) -- deliberately
        one generic outcome for the API layer to turn into one generic
        error message, so a failed login never confirms which part was
        wrong."""
        statement = select(User).where(User.username == username.strip().lower())
        user = self._session.exec(statement).first()

        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            self._audit.log(
                AuditEventType.USER_LOGIN_FAILED,
                f"Login failed for username '{username}'.",
                severity=AuditSeverity.WARNING,
                metadata={"username": username},
            )
            return None

        raw_token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        expires_at = now + (_REMEMBER_ME_SESSION if remember_me else _SHORT_SESSION)
        db_session = UserSession(
            user_id=user.id,  # type: ignore[arg-type]
            token_hash=_hash_token(raw_token),
            remember_me=remember_me,
            expires_at=expires_at,
            user_agent=user_agent,
        )
        self._session.add(db_session)
        self._session.commit()

        self._audit.log(
            AuditEventType.USER_LOGIN_SUCCEEDED,
            f"User '{user.username}' logged in.",
            metadata={"user_id": user.id, "remember_me": remember_me},
        )
        return user, raw_token

    def logout(self, raw_token: str) -> None:
        db_session = self._find_session(raw_token)
        if db_session is None or db_session.revoked_at is not None:
            return
        db_session.revoked_at = datetime.now(UTC)
        self._session.add(db_session)
        self._session.commit()
        self._audit.log(
            AuditEventType.USER_LOGOUT,
            "User logged out.",
            metadata={"user_id": db_session.user_id},
        )

    def resolve_session(self, raw_token: str) -> User | None:
        """`None` for a missing/expired/revoked session, or a since-
        deactivated user -- the caller (app/core/auth_deps.py) turns this
        into a 401, never distinguishing why."""
        db_session = self._find_session(raw_token)
        if db_session is None:
            return None
        now = datetime.now(UTC)
        if db_session.revoked_at is not None or db_session.expires_at <= now:
            return None

        user = self._session.get(User, db_session.user_id)
        if user is None or not user.is_active:
            return None

        db_session.last_seen_at = now
        self._session.add(db_session)
        self._session.commit()
        return user

    def _find_session(self, raw_token: str) -> UserSession | None:
        statement = select(UserSession).where(UserSession.token_hash == _hash_token(raw_token))
        return self._session.exec(statement).first()
