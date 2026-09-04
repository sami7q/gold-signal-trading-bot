from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.database.models.mt5_account import MT5Account, is_account_connected

__all__ = ["MT5AccountCreate", "MT5AccountRead", "MT5AccountUpdate"]


class MT5AccountRead(BaseModel):
    id: int
    label: str
    mt5_login: str
    mt5_server: str
    symbol: str
    #: Computed from `last_heartbeat_at` recency, NOT the account's own
    #: (dead, as of the concurrent-execution "Phase 2" update) `is_live`
    #: column -- always build this via `from_account()` below, never
    #: `MT5AccountRead.model_validate(account)` directly, which would
    #: silently read that stale column instead.
    is_live: bool
    last_heartbeat_at: datetime | None
    is_active: bool
    #: True = every operator sees and drives this account, not just its
    #: owner (see MT5Account.is_shared). The dashboard shows it as a badge
    #: and lets it be toggled from the same form as the other fields.
    is_shared: bool
    #: Never the ciphertext or plaintext -- just whether one has been set.
    has_password: bool = True

    @classmethod
    def from_account(cls, account: MT5Account) -> MT5AccountRead:
        assert account.id is not None, "account must be persisted before it can be read"
        return cls(
            id=account.id,
            label=account.label,
            mt5_login=account.mt5_login,
            mt5_server=account.mt5_server,
            symbol=account.symbol,
            is_live=is_account_connected(account),
            last_heartbeat_at=account.last_heartbeat_at,
            is_active=account.is_active,
            is_shared=account.is_shared,
        )


class MT5AccountCreate(BaseModel):
    label: str = Field(min_length=1)
    mt5_login: str = Field(min_length=1)
    mt5_server: str = Field(min_length=1)
    mt5_password: str = Field(min_length=1)
    symbol: str = "XAUUSD"
    #: Private to its creator unless deliberately opted in -- see
    #: MT5Account.is_shared.
    is_shared: bool = False


class MT5AccountUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1)
    mt5_login: str | None = Field(default=None, min_length=1)
    mt5_server: str | None = Field(default=None, min_length=1)
    #: Omit to leave the stored password untouched; provide to rotate it.
    mt5_password: str | None = Field(default=None, min_length=1)
    symbol: str | None = None
    is_shared: bool | None = None
