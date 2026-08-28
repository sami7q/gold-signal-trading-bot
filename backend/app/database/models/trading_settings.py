from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlmodel import Field

from app.core.constants import TradingMode
from app.database.base import Base
from app.database.types import UTCDateTime


class TradingSettings(Base, table=True):
    """Singleton-ish settings row.

    The service layer always operates on the most recently updated row; a
    fresh default row is created on first startup if none exists (see
    `app/settings/service.py`).
    """

    __tablename__ = "trading_settings"

    id: int | None = Field(default=None, primary_key=True)

    auto_trading: bool = False
    mode: TradingMode = TradingMode.SIMULATION

    buy_enabled: bool = True
    sell_enabled: bool = False

    lot_size: Decimal = Decimal("0.10")
    target_pips: Decimal = Decimal("100")
    require_stop_loss: bool = True
    max_open_trades: int = 1

    #: Reject a signal whose channel-stated Stop Loss is farther from the
    #: current market price than this many pips. `None` disables the
    #: filter (every signal with a positive Stop Loss is accepted, the
    #: pre-v1.1 behavior). Historical backtesting supports anywhere from
    #: 170-200 pips -- see VIP_FOREX_ANALYSIS.md section 4; 180 is the
    #: operator-chosen value in that range.
    max_channel_stop_pips: Decimal | None = Decimal("180")

    signal_completion_timeout_seconds: int = 120

    #: If the channel never sends a Stop Loss before
    #: `signal_completion_timeout_seconds` elapses, apply this many pips as
    #: a fallback Stop Loss (measured from the live quote at the moment the
    #: timeout fires) and proceed to execution instead of expiring the
    #: signal unexecuted. `None` restores the pre-v1.2 behavior: no SL in
    #: time means the signal simply expires. See
    #: `app/trading/service.py::TradingService._apply_auto_stop_loss_fallback`.
    auto_sl_fallback_pips: Decimal | None = Decimal("150")

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
