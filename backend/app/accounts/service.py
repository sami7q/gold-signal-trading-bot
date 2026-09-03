"""MT5 account registration + per-process identity resolution
(multi-account feature, 2026-09; concurrent-execution "Phase 2" update,
also 2026-09 -- see the approved plans for the full rationale). `user_id`
scoping is the actual isolation boundary between operators; every lookup
here that returns an existing row must be told which user is asking and
silently treat another user's row as if it doesn't exist (see `get_owned`)
-- callers must never leak *whether* an id belongs to someone else.

Phase 2 replaced the old single global "which account is live" flag
(`AccountService.set_live()`/`get_live_account()`, both removed) with
per-process identity resolution: each process (the hub, or a
`backend/scripts/worker.py` instance) matches its own `.env`'s
MT5_ACCOUNT/MT5_SERVER against this table once at startup
(`resolve_own_process_account`), *after* a real `MT5Broker.connect()` has
already proven that identity -- there is no more manual toggle to drift
out of sync with which process is physically connected to what. "Is this
account live" is now a read-only signal computed from `last_heartbeat_at`
recency (`is_account_connected`), written only when a resolved process has
just confirmed `broker.health().healthy`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlmodel import Session, select

from app.accounts.schemas import MT5AccountCreate, MT5AccountUpdate
from app.audit.service import AuditService
from app.core.constants import AuditEventType, AuditSeverity
from app.core.credentials import decrypt_mt5_password, encrypt_mt5_password
from app.database.models.mt5_account import HEARTBEAT_STALE_AFTER, MT5Account, is_account_connected

__all__ = [
    "AccountArchiveError",
    "AccountService",
    "DuplicateAccountError",
    "HEARTBEAT_STALE_AFTER",
    "is_account_connected",
]


class AccountArchiveError(ValueError):
    """Refused because a process is currently connected to this account."""


class DuplicateAccountError(ValueError):
    """A (mt5_login, mt5_server) pair that's already registered -- friendly
    app-level guard in front of the `uq_mt5_account_login_server` DB
    constraint (same "app-level check + DB constraint as last resort"
    pattern as Trade.execution_key / TradingEngine._find_trade)."""


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

    def resolve_own_process_account(self, mt5_login: str, mt5_server: str) -> MT5Account | None:
        """Called once at process startup (the hub, or a worker), *after*
        `MT5Broker.connect()` has already succeeded against exactly these
        credentials -- the physical connection is what proves identity,
        this just finds the matching row. Matched on both fields: an MT5
        login number alone isn't guaranteed unique across different
        brokers/servers (confirmed real by the two live accounts this
        feature was built for -- different numbers, but the pair is the
        only thing actually guaranteed unique, see
        `uq_mt5_account_login_server`)."""
        statement = select(MT5Account).where(
            MT5Account.mt5_login == mt5_login, MT5Account.mt5_server == mt5_server
        )
        return self._session.exec(statement).first()

    def list_connected(self) -> list[MT5Account]:
        """Every account with a real process connected right now, across
        every user -- global by nature (connection is a VPS-process fact,
        not a per-user concept). Used by the simulation reset guard so a
        cleanup run as one account can never reach into a different,
        currently-connected account's shared signal history."""
        threshold = datetime.now(UTC) - HEARTBEAT_STALE_AFTER
        statement = select(MT5Account).where(MT5Account.last_heartbeat_at >= threshold)  # type: ignore[operator]
        return list(self._session.exec(statement))

    # -- mutations -------------------------------------------------------

    def _assert_login_server_free(
        self, mt5_login: str, mt5_server: str, *, exclude_account_id: int | None = None
    ) -> None:
        statement = select(MT5Account).where(
            MT5Account.mt5_login == mt5_login, MT5Account.mt5_server == mt5_server
        )
        existing = self._session.exec(statement).first()
        if existing is not None and existing.id != exclude_account_id:
            raise DuplicateAccountError(
                f"An account for login '{mt5_login}' on server '{mt5_server}' is already "
                "registered -- each (login, server) pair can only be added once."
            )

    def create(self, user_id: int, data: MT5AccountCreate) -> MT5Account:
        self._assert_login_server_free(data.mt5_login, data.mt5_server)
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
        new_login = changes.get("mt5_login", account.mt5_login)
        new_server = changes.get("mt5_server", account.mt5_server)
        if new_login != account.mt5_login or new_server != account.mt5_server:
            self._assert_login_server_free(new_login, new_server, exclude_account_id=account.id)
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
        if is_account_connected(account):
            raise AccountArchiveError(
                "A process is currently connected to this account -- stop it first, "
                "then archive this account."
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

    def record_heartbeat(
        self,
        account_id: int,
        *,
        balance: Decimal | None = None,
        equity: Decimal | None = None,
    ) -> None:
        """Called only after the caller (the hub or a worker, on its own
        poll tick -- see app/lifecycle.py / backend/scripts/worker.py) has
        just confirmed `broker.health().healthy` for its own resolved
        account -- never speculatively. No audit log entry: at a ~10s
        cadence this would drown out everything else in the trail for zero
        diagnostic value beyond what the column's own recency already
        gives for free.

        `balance`/`equity` (2026-09-03 request) are this account's own
        broker numbers, from *this same* caller's own broker connection --
        the dashboard reads them back from here instead of ever asking a
        broker connection that might belong to a different account (see
        MT5Account's docstring). Omitted (left `None`) rather than zeroed
        when the caller couldn't fetch them for some reason, so a
        transient read failure never overwrites the last known-good value.
        """
        account = self._session.get(MT5Account, account_id)
        if account is None:
            return
        account.last_heartbeat_at = datetime.now(UTC)
        if balance is not None:
            account.last_balance = balance
        if equity is not None:
            account.last_equity = equity
        self._session.add(account)
        self._session.commit()

    # -- Phase 1 groundwork, unused by the live connection today ---------

    def decrypt_password(self, account: MT5Account) -> str:
        """Round-trip helper for tests / a future "reveal password" admin
        action -- not called anywhere in the live trading path (see
        app/core/credentials.py's module docstring)."""
        return decrypt_mt5_password(account.mt5_password_encrypted)
