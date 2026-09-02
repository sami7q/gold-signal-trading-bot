from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.core.constants import TradingMode
from app.core.decimals import CleanDecimal

LIVE_CONFIRMATION_PHRASE = "ENABLE LIVE TRADING"


class TradingSettingsRead(BaseModel):
    id: int
    auto_trading: bool
    mode: TradingMode
    buy_enabled: bool
    sell_enabled: bool
    lot_size: CleanDecimal
    target_pips: CleanDecimal
    require_stop_loss: bool
    max_open_trades: int
    max_channel_stop_pips: CleanDecimal | None
    signal_completion_timeout_seconds: int
    auto_sl_fallback_pips: CleanDecimal | None
    daily_lot_plan_enabled: bool
    martingale_lot_multiplier: CleanDecimal
    monday_first_trade_lot: CleanDecimal
    monday_second_trade_lot: CleanDecimal
    max_trades_per_day: int
    loss_recovery_enabled: bool

    model_config = {"from_attributes": True}


class TradingSettingsUpdate(BaseModel):
    auto_trading: bool | None = None
    mode: TradingMode | None = None
    buy_enabled: bool | None = None
    sell_enabled: bool | None = None
    lot_size: Decimal | None = Field(default=None, gt=0)
    target_pips: Decimal | None = Field(default=None, gt=0)
    require_stop_loss: bool | None = None
    max_open_trades: int | None = Field(default=None, ge=1)
    #: `None` here is meaningful (disables the filter), unlike the other
    #: fields above where `None` only means "not provided" -- distinguished
    #: via `model_dump(exclude_unset=True)` in the service layer, so an
    #: explicit `null` in the request body is applied, while an omitted key
    #: leaves the current value untouched.
    max_channel_stop_pips: Decimal | None = None
    signal_completion_timeout_seconds: int | None = Field(default=None, ge=1)
    #: Same `None`-is-meaningful semantics as `max_channel_stop_pips` above.
    auto_sl_fallback_pips: Decimal | None = None

    #: Daily/weekly lot plan (see app/trading/daily_lot_plan.py).
    daily_lot_plan_enabled: bool | None = None
    martingale_lot_multiplier: Decimal | None = Field(default=None, gt=0)
    monday_first_trade_lot: Decimal | None = Field(default=None, gt=0)
    monday_second_trade_lot: Decimal | None = Field(default=None, gt=0)
    max_trades_per_day: int | None = Field(default=None, ge=1)

    #: Loss-recovery lot plan (see app/trading/loss_recovery.py).
    loss_recovery_enabled: bool | None = None

    #: Required to equal `LIVE_CONFIRMATION_PHRASE` when `mode` is being set
    #: to LIVE (PROJECT_SPEC.md section 28).
    confirm_live_phrase: str | None = None

    @field_validator(
        "lot_size",
        "target_pips",
        "max_channel_stop_pips",
        "auto_sl_fallback_pips",
        "martingale_lot_multiplier",
        "monday_first_trade_lot",
        "monday_second_trade_lot",
    )
    @classmethod
    def _must_be_positive(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("must be greater than zero")
        return value
