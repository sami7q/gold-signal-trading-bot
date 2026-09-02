"""MT5 account registration + the single global "which account is live"
switch (multi-account feature, 2026-09 -- see the approved plan for the
full rationale). `user_id` scoping is the actual isolation boundary
between operators; every lookup here that returns an existing row must be
told which user is asking and silently treat another user's row as if it
doesn't exist (see `get_owned`) -- callers must never leak *whether* an id
belongs to someone else.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, select

from app.accounts.schemas import LIVE_CONFIRMATION_PHRASE, MT5AccountCreate, MT5AccountUpdate
from app.audit.service import AuditService
from app.core.constants import AuditEventType, AuditSeverity
from app.core.credentials import decrypt_mt5_password, encrypt_mt5_password
from app.database.models.mt5_account import MT5Account


class SetLiveConfirmationError(ValueError):
    pass


class AccountArchiveError(ValueError):
    """Refused because the account is currently is_live."""


class AccountService:
    def __init__(self, session: Session, audit: AuditService | None = None) -> None:
        self._session = session
        self._audit = audit or AuditService(session)

    # -- lookups -------------------------------------------------------

    def list_for_user(self, user_id: int) -> list[MT5Account]:
        statement = (
            select(MT5Account)
            .where(MT5Account.user_id == user_id)
            .order_by(MT5Account.created_at.asc())  # type: ignore[attr-defined]
        )
        return list(self._session.exec(statement))

    def get_owned(self, user_id: int, account_id: int) -> MT5Account | None:
        """`None` for both "doesn't exist" and "exists but belongs to
        someone else" -- deliberately indistinguishable to every caller,
        which is what turns this into the actual isolation boundary rather
        than just a filter."""
        account = self._session.get(MT5Account, account_id)
        if account is None or account.user_id != user_id:
            return None
        return account

    def get_live_account(self) -> MT5Account | None:
        """Global, not user-scoped -- there is at most one across every
        user's accounts combined (Phase 1's one-MT5-connection constraint).
        Used internally by app/lifecycle.py, never exposed to a specific
        user's request."""
        statement = select(MT5Account).where(MT5Account.is_live == True)  # noqa: E712
        return self._session.exec(statement).first()

    # -- mutations -------------------------------------------------------

    def create(self, user_id: int, data: MT5AccountCreate) -> MT5Account:
        account = MT5Account(
            user_id=user_id,
            label=data.label,
            mt5_login=data.mt5_login,
            mt5_server=data.mt5_server,
            mt5_password_encrypted=encrypt_mt5_password(data.mt5_password),
            symbol=data.symbol,
        )
        self._session.add(account)
        self._session.commit()
        self._session.refresh(account)
        self._audit.log(
            AuditEventType.ACCOUNT_CREATED,
            f"MT5 account '{account.label}' created (login={account.mt5_login}).",
            metadata={"account_id": account.id, "user_id": user_id},
        )
        return account

    def update(self, account: MT5Account, data: MT5AccountUpdate) -> MT5Account:
        changes = data.model_dump(exclude_unset=True, exclude={"mt5_password"})
        for field, value in changes.items():
            setattr(account, field, value)
        if data.mt5_password is not None:
            account.mt5_password_encrypted = encrypt_mt5_password(data.mt5_password)
            changes["mt5_password"] = "***rotated***"
        if not changes:
            return account
        account.updated_at = datetime.now(UTC)
        self._session.add(account)
        self._session.commit()
        self._session.refresh(account)
        self._audit.log(
            AuditEventType.ACCOUNT_UPDATED,
            f"MT5 account '{account.label}' updated: {', '.join(changes)}.",
            metadata={"account_id": account.id, "user_id": account.user_id},
        )
        return account

    def archive(self, account: MT5Account) -> MT5Account:
        if account.is_live:
            raise AccountArchiveError(
                "This account is currently live -- switch another account to "
                "live first, then archive this one."
            )
        account.is_active = False
        account.updated_at = datetime.now(UTC)
        self._session.add(account)
        self._session.commit()
        self._session.refresh(account)
        self._audit.log(
            AuditEventType.ACCOUNT_ARCHIVED,
            f"MT5 account '{account.label}' archived.",
            severity=AuditSeverity.WARNING,
            metadata={"account_id": account.id, "user_id": account.user_id},
        )
        return account

    def set_live(self, account: MT5Account, *, confirm_phrase: str) -> MT5Account:
        """The global swap, in one transaction: demote whichever account is
        currently live (if any -- possibly a *different user's*), then
        promote this one. Gated by the same confirmation-phrase pattern as
        switching TradingSettings.mode to LIVE (app/settings/service.py) --
        this is exactly as consequential: it pauses real execution on
        whatever account was live before.
        """
        if confirm_phrase != LIVE_CONFIRMATION_PHRASE:
            raise SetLiveConfirmationError(
                f'Setting an account live requires confirm_phrase="{LIVE_CONFIRMATION_PHRASE}".'
            )
        if account.is_live:
            return account

        previous = self.get_live_account()
        if previous is not None and previous.id != account.id:
            previous.is_live = False
            previous.updated_at = datetime.now(UTC)
            self._session.add(previous)

        account.is_live = True
        account.updated_at = datetime.now(UTC)
        self._session.add(account)
        self._session.commit()
        self._session.refresh(account)

        previous_suffix = (
            f" (was: '{previous.label}')."
            if previous is not None
            else " (no previous live account)."
        )
        self._audit.log(
            AuditEventType.ACCOUNT_SET_LIVE,
            f"MT5 account '{account.label}' set live" + previous_suffix,
            severity=AuditSeverity.WARNING,
            metadata={
                "account_id": account.id,
                "user_id": account.user_id,
                "previous_account_id": previous.id if previous is not None else None,
            },
        )
        return account

    # -- Phase 2 groundwork, unused by the live connection today ---------

    def decrypt_password(self, account: MT5Account) -> str:
        """Round-trip helper for tests / a future "reveal password" admin
        action -- not called anywhere in the live trading path in Phase 1
        (see app/core/credentials.py's module docstring)."""
        return decrypt_mt5_password(account.mt5_password_encrypted)
