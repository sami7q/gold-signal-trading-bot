from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlmodel import Field

from app.core.constants import TradingMode
from app.database.base import Base
from app.database.types import UTCDateTime


class TradingSettings(Base, table=True):
    """One settings row per MT5 account (multi-account feature, 2026-09).

    The service layer operates on the most recently updated row *for a
    given `account_id`*; a fresh default row is created for that account on
    first access if none exists yet (see `app/settings/service.py`'s
    `get_for_account`). Every field below is therefore independent per
    account -- two accounts can run completely different strategies against
    the same shared Telegram channel.
    """

    __tablename__ = "trading_settings"

    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="mt5_accounts.id", index=True)

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

    #: Daily/weekly lot plan (operator-requested martingale-style recovery
    #: schedule, Asia/Baghdad calendar day -- see
    #: `app/trading/daily_lot_plan.py` for the full rule). `False` restores
    #: plain behavior: every executed trade uses `lot_size`, no daily cap.
    daily_lot_plan_enabled: bool = True
    #: Non-Monday second trade of the day (only reached if the first trade
    #: that day lost): `lot_size * martingale_lot_multiplier`.
    martingale_lot_multiplier: Decimal = Decimal("2")
    #: Monday's first trade of the week uses this lot instead of `lot_size`.
    monday_first_trade_lot: Decimal = Decimal("6")
    #: Monday's second trade (only reached if the lot-6 first trade lost)
    #: uses this lot instead of the multiplier-based one.
    monday_second_trade_lot: Decimal = Decimal("2")
    #: Hard cap on executed trades per calendar day, regardless of outcome.
    max_trades_per_day: int = 2

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
