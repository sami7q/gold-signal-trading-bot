"""A historical, retrospective reconstruction of what a channel's past
messages would have produced -- explicitly NOT live trading data.

Built once (or re-built) by `scripts/build_channel_archive.py` from a
channel's real message history plus real historical MT5 price data, using
the exact same deterministic parser the live pipeline uses. Kept in its own
table specifically so that resetting live trades/signals (a routine
operation, see `app/api/simulation.py`) can never touch it -- the archive
is meant to persist across live-data resets."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlmodel import Field

from app.database.base import Base
from app.database.types import UTCDateTime


class ArchivedSignal(Base, table=True):
    __tablename__ = "archived_signals"

    id: int | None = Field(default=None, primary_key=True)

    channel_id: str = Field(index=True)
    #: The entry message's own Telegram message id -- lets a re-run of the
    #: archive builder skip messages it has already recorded.
    message_id: str = Field(index=True)
    reply_to_message_id: str | None = None

    symbol: str
    side: str
    signal_type: str

    entry_min: Decimal | None = None
    entry_max: Decimal | None = None
    channel_stop_loss: Decimal | None = None

    raw_entry_text: str
    raw_sl_text: str | None = None

    signal_posted_at: datetime = Field(sa_type=UTCDateTime)
    sl_posted_at: datetime | None = Field(default=None, sa_type=UTCDateTime)

    #: READY | AMBIGUOUS | NO_SL | NON_GOLD | NON_BUY | IGNORED -- mirrors
    #: the live classification/state-machine outcome for this message, so
    #: the archive can report "how many were even tradeable" honestly
    #: instead of only showing the ones that happened to resolve.
    classification: str = Field(index=True)

    #: Only set when classification == READY.
    simulated_entry_price: Decimal | None = None
    simulated_take_profit_price: Decimal | None = None

    #: WIN | LOSS | UNRESOLVED | NO_PRICE_DATA -- only meaningful when
    #: classification == READY. UNRESOLVED means neither SL nor TP was
    #: crossed anywhere in the historical price data available (e.g. it's
    #: still an open, ongoing move); NO_PRICE_DATA means the broker had no
    #: history at all for that window -- neither is ever guessed as a WIN
    #: or LOSS (PROJECT_SPEC.md section 32, rule 13).
    outcome: str | None = Field(default=None, index=True)
    outcome_price: Decimal | None = None
    outcome_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    profit_estimate: Decimal | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
