"""Centralized trading rules engine (PROJECT_SPEC.md section 11).

Every safety and strategy condition lives here. Checks run in the exact
order listed in PROJECT_SPEC.md section 2.3 so the first failing condition
is always the one reported — nothing is ever silently skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.constants import (
    CANONICAL_GOLD_SYMBOL,
    OrderType,
    RejectionReason,
    Side,
    SignalStatus,
)
from app.database.models.signal import Signal
from app.database.models.trading_settings import TradingSettings
from app.integrations.broker.base import BrokerAccount, BrokerHealth, Quote, SymbolInfo
from app.trading import pips, risk


@dataclass(frozen=True, slots=True)
class TradingDecision:
    allowed: bool
    reason_code: str | None
    explanation: str


def _decision(reason: RejectionReason, explanation: str) -> TradingDecision:
    return TradingDecision(allowed=False, reason_code=reason.value, explanation=explanation)


_ALLOWED = TradingDecision(allowed=True, reason_code=None, explanation="All trading rules passed.")


class TradingRulesEngine:
    @staticmethod
    def evaluate(
        *,
        signal: Signal,
        settings: TradingSettings,
        account: BrokerAccount | None,
        open_trades_count: int,
        broker_health: BrokerHealth | None,
        symbol_info: SymbolInfo | None,
        quote: Quote | None,
        already_executed: bool,
        effective_lot: Decimal,
        daily_plan_rejection: str | None = None,
    ) -> TradingDecision:
        # 1. Auto Trading must be enabled.
        if not settings.auto_trading:
            return _decision(RejectionReason.AUTO_TRADING_DISABLED, "Auto Trading is OFF.")

        # 4. Parser must have recognized a valid, executable GOLD signal.
        # BUY and SELL market orders are both executable (v1.3); LIMIT/STOP
        # (pending/conditional orders) are not -- they need a separate
        # pending-order lifecycle the broker adapters don't have yet.
        if signal.symbol != CANONICAL_GOLD_SYMBOL:
            return _decision(
                RejectionReason.UNSUPPORTED_SYMBOL,
                f"Symbol {signal.symbol} is not supported in v1.",
            )
        if signal.signal_type != OrderType.MARKET:
            return _decision(
                RejectionReason.UNSUPPORTED_SIGNAL_TYPE,
                f"{signal.side}/{signal.signal_type} is classified but not executable "
                "(only market BUY/SELL are, pending orders are not).",
            )

        # 2. BUY/SELL signals must be enabled on the dashboard, independently.
        if signal.side == Side.BUY and not settings.buy_enabled:
            return _decision(
                RejectionReason.BUY_DISABLED, "BUY signals are disabled on the dashboard."
            )
        if signal.side == Side.SELL and not settings.sell_enabled:
            return _decision(
                RejectionReason.SELL_DISABLED, "SELL signals are disabled on the dashboard."
            )

        # Signal must still be actionable (not already expired by the state machine).
        if signal.status == SignalStatus.EXPIRED:
            return _decision(
                RejectionReason.SIGNAL_EXPIRED, "Signal expired before a Stop Loss arrived."
            )

        # 5. A valid Stop Loss must be available.
        if settings.require_stop_loss and signal.channel_stop_loss is None:
            return _decision(
                RejectionReason.STOP_LOSS_REQUIRED, "No Stop Loss was provided by the channel."
            )
        if signal.channel_stop_loss is not None and signal.channel_stop_loss <= 0:
            return _decision(
                RejectionReason.STOP_LOSS_INVALID, "Stop Loss must be a positive price."
            )

        # 6. The signal must not already have been executed.
        if already_executed:
            return _decision(RejectionReason.DUPLICATE_SIGNAL, "This signal already has a trade.")

        # 7. The broker adapter must be available.
        if (
            broker_health is None
            or not broker_health.healthy
            or account is None
            or symbol_info is None
            or quote is None
        ):
            return _decision(RejectionReason.BROKER_UNAVAILABLE, "Broker is not available.")

        # 7b. (v1.1) The channel's own Stop Loss must not be unreasonably
        # far from the current market price. This is a signal-quality
        # filter, not a risk cap -- the SL actually sent to the broker
        # stays the channel's own number either way (see engine.py).
        # Validated via historical backtesting: signals with an unusually
        # wide channel Stop Loss are both lower win-rate AND carry
        # outsized losses when wrong (VIP_FOREX_ANALYSIS.md section 4).
        if settings.max_channel_stop_pips is not None and signal.channel_stop_loss is not None:
            # BUY: SL sits below the ask, so ask - SL is a positive distance.
            # SELL: SL sits above the bid, so SL - bid is the positive
            # distance (the mirror image).
            raw_distance = (
                quote.ask - signal.channel_stop_loss
                if signal.side == Side.BUY
                else signal.channel_stop_loss - quote.bid
            )
            distance_pips = pips.price_distance_to_pips(symbol_info, raw_distance)
            if distance_pips > settings.max_channel_stop_pips:
                return _decision(
                    RejectionReason.STOP_LOSS_TOO_WIDE,
                    f"Channel Stop Loss is {distance_pips:.0f} pips away "
                    f"(max allowed: {settings.max_channel_stop_pips:.0f}).",
                )

        # 8. Maximum open trade rules must allow another position.
        if open_trades_count >= settings.max_open_trades:
            return _decision(
                RejectionReason.MAX_OPEN_TRADES_REACHED,
                f"{open_trades_count} of {settings.max_open_trades} max open trades already open.",
            )

        # 8b. Daily/weekly lot plan (if TradingSettings.daily_lot_plan_enabled)
        # must allow another trade today -- e.g. today's trade cap already
        # reached, or today's first trade already won and there's nothing
        # to recover. See app/trading/daily_lot_plan.py. Distinct from rule
        # 8 above: that's about concurrency, this is a per-day count.
        if daily_plan_rejection is not None:
            return _decision(RejectionReason.DAILY_TRADE_LIMIT_REACHED, daily_plan_rejection)

        # 9. The lot size (the daily plan's, or the configured one if that
        # plan is disabled) must be valid for this broker/symbol.
        if not risk.is_valid_lot(effective_lot, symbol_info):
            return _decision(
                RejectionReason.INVALID_LOT_SIZE, "Configured lot size is invalid for this broker."
            )

        # 10. The configured profit target must be valid.
        if not risk.is_valid_target_pips(settings.target_pips):
            return _decision(
                RejectionReason.INVALID_TARGET, "Configured target pips must be positive."
            )

        return _ALLOWED
