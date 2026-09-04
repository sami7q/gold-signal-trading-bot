"""A user's MT5 trading account registration (multi-account feature,
2026-09). `user_id` is the actual isolation boundary -- every account
endpoint filters by it (see app/api/accounts.py) -- with exactly one
deliberate exception: a row flagged `is_shared` is visible to, and usable
by, every operator (2026-09-04 request, see that field's comment).

Phase 2 (2026-09, see the approved "concurrent execution" plan): real
concurrent execution across accounts is a real OS-process boundary --
`MetaTrader5` only manages one terminal connection per process -- so each
account that trades for real runs its own process (the hub, or a
`backend/scripts/worker.py` instance), each resolving *its own* identity by
matching its own `.env`'s MT5_ACCOUNT/MT5_SERVER against this table's
`mt5_login`/`mt5_server` (see AccountService.resolve_own_process_account),
never by reading a flag here. `is_live` is therefore no longer written or
read by any decision-making code path -- it is inert history, kept only to
avoid a destructive column-drop migration against a table with real rows;
`MT5AccountRead`'s wire-level `is_live` is computed from `last_heartbeat_at`
instead (see app/accounts/schemas.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from app.database.base import Base
from app.database.types import UTCDateTime

#: A process last confirmed `broker.health().healthy` more than this long
#: ago is treated as disconnected -- generous relative to worker.py's
#: ~10s poll tick (a few missed ticks of grace) without letting a genuinely
#: dead process linger as "connected" for long. Lives here (not in
#: app/accounts/service.py) so both that service *and*
#: app/accounts/schemas.py can use it without a circular import (schemas
#: needs it to compute the wire-level `is_live` field; service needs it for
#: the archive-guard and `list_connected`).
HEARTBEAT_STALE_AFTER = timedelta(seconds=60)


class MT5Account(Base, table=True):
    __tablename__ = "mt5_accounts"
    __table_args__ = (
        UniqueConstraint("mt5_login", "mt5_server", name="uq_mt5_account_login_server"),
    )

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)

    #: Operator-chosen, e.g. "Sami - ICMarkets Main". Never used for lookup.
    label: str

    #: Together with `mt5_server`, unique (enforced by both a DB constraint
    #: and AccountService.create/update) -- this pair is how a running
    #: process resolves "which MT5Account am I" from its own `.env`.
    mt5_login: str
    mt5_server: str
    #: Fernet ciphertext -- see app/core/credentials.py. Never plaintext,
    #: never returned by any API response (only a derived has_password bool).
    mt5_password_encrypted: str
    symbol: str = "XAUUSD"

    #: Dead column as of Phase 2 -- see module docstring. Kept only to avoid
    #: a destructive migration; never read or written by application code.
    is_live: bool = False
    #: Written periodically by whichever process (hub or worker) has
    #: resolved this account and confirmed `broker.health().healthy` --
    #: never set speculatively. `MT5AccountRead.is_live` (the wire field
    #: the dashboard actually shows) is `now - last_heartbeat_at < 60s`,
    #: computed in app/accounts/schemas.py -- a self-healing "is a real
    #: process actually connected right now" signal, not a manually-toggled
    #: switch that can silently go stale if a process dies uncleanly.
    last_heartbeat_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    #: This account's own broker balance/equity, as of its own last
    #: heartbeat -- written by whichever process (hub or worker) actually
    #: holds *this* account's MT5 connection. Fixes a real bug (2026-09-03
    #: request): the dashboard's `/api/dashboard/summary` used to call
    #: `broker.get_account()` on the HTTP-serving hub process's own single
    #: broker connection regardless of which account was being viewed --
    #: correct only for whichever account IS that hub's own connection, and
    #: silently showing that same account's numbers under every *other*
    #: account otherwise (a worker's connection lives in a separate OS
    #: process with no HTTP server, so the hub could never reach it live).
    #: Reading these stored fields instead is correct for every account
    #: uniformly, at the cost of being at most one heartbeat cycle old.
    last_balance: Decimal | None = Field(default=None)
    last_equity: Decimal | None = Field(default=None)
    #: Soft-archive (matches ArchivedSignal's idiom) -- refuses while a
    #: process is currently connected to this account (AccountService.archive).
    is_active: bool = True
    #: Deliberate, opt-in hole in the per-user isolation boundary
    #: (2026-09-04 operator request): the two operators run *one* jointly
    #: owned demo account, and both dashboard logins must see and drive it.
    #: True means every user gets exactly the owner's access to this row --
    #: it appears in their account list, it is accepted as their
    #: `X-Account-Id`, and they can edit/archive it (see
    #: AccountService.get_accessible). Default False, so an account stays
    #: private to `user_id` unless someone deliberately shares it; that
    #: private-by-default case is still the isolation boundary every
    #: cross-user test in tests/api/test_accounts.py asserts.
    is_shared: bool = False

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)


def is_account_connected(account: MT5Account, *, now: datetime | None = None) -> bool:
    """Self-healing "is a real process connected to this account right
    now" signal -- see the module docstring for why this replaced the old
    manually-toggled `is_live` flag. A process that dies uncleanly simply
    stops refreshing `last_heartbeat_at`; there's no separate cleanup path
    needed, unlike a boolean someone has to remember to flip back off."""
    if account.last_heartbeat_at is None:
        return False
    now = now or datetime.now(UTC)
    return now - account.last_heartbeat_at < HEARTBEAT_STALE_AFTER
