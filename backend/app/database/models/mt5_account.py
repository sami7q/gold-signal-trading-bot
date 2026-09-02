"""A user's MT5 trading account registration (multi-account feature,
2026-09). `user_id` is the actual isolation boundary -- every account
endpoint filters by it (see app/api/accounts.py).

Phase 1 scope (see the approved plan): multiple accounts can be registered
and each gets its own fully independent TradingSettings row, but the
`MetaTrader5` package only manages one terminal connection per OS process,
so at most one account across ALL users can be `is_live` at a time --
that's the one actually wired to the running MT5Broker connection (which,
in Phase 1, still reads its credentials from `.env`/Settings exactly as
before this feature, NOT from this table -- see app/core/credentials.py's
module docstring for why). Every other account is fully manageable
(settings, history once it *was* live) but inert for real execution until
Phase 2 gives it its own process.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field

from app.database.base import Base
from app.database.types import UTCDateTime


class MT5Account(Base, table=True):
    __tablename__ = "mt5_accounts"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)

    #: Operator-chosen, e.g. "Sami - ICMarkets Main". Never used for lookup.
    label: str

    mt5_login: str
    mt5_server: str
    #: Fernet ciphertext -- see app/core/credentials.py. Never plaintext,
    #: never returned by any API response (only a derived has_password bool).
    mt5_password_encrypted: str
    symbol: str = "XAUUSD"

    #: Global across every user's accounts combined -- see module docstring.
    #: Flipped only via AccountService.set_live(), never a plain field PATCH.
    is_live: bool = False
    #: Soft-archive (matches ArchivedSignal's idiom) -- refuses while is_live.
    is_active: bool = True

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
