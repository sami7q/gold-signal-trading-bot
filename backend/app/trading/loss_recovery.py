"""Loss-recovery lot plan (operator-requested rule, 2026-09-03 request):
after a LOSS on a normal-lot trade, the very next trade doubles the lot
(`martingale_lot_multiplier`); the trade after *that* always resets back to
the normal lot, regardless of whether the doubled trade won, lost, or broke
even.

Unlike `app/trading/daily_lot_plan.py`, this is deliberately **not**
calendar-day scoped and never caps how many trades execute in a day --
every signal still executes (the operator was explicit: "يدخل كل
الصفقات"), only the lot size of each one varies based on the immediately
preceding trade. The two plans are mutually exclusive per account (see
`TradingEngine.execute`); enabling one is expected to mean the other is off.

Pure logic, DB-free, deliberately separate from `app/trading/engine.py` so
the recovery/reset arithmetic is unit-testable in isolation without a
broker or a database. `TradingEngine` is the only caller: it queries the
account's single most recent real trade and passes it in.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.constants import TradeResult
from app.database.models.trade import Trade
from app.database.models.trading_settings import TradingSettings


@dataclass(frozen=True, slots=True)
class RecoveryLotDecision:
    lot: Decimal
    explanation: str


def next_trade_lot(last_trade: Trade | None, *, settings: TradingSettings) -> RecoveryLotDecision:
    """`last_trade` must be this account's single most recent real (broker-
    confirmed) trade -- status OPEN, UNKNOWN, or any CLOSED_* variant;
    never REJECTED/FAILED, which never entered the market -- or `None` if
    it has no such trade yet. Always returns a lot (this plan never blocks
    a trade, unlike the daily lot plan)."""
    if last_trade is None:
        return RecoveryLotDecision(settings.lot_size, "No prior trade yet -- normal lot.")

    was_recovery_trade = last_trade.requested_lot != settings.lot_size
    if was_recovery_trade:
        return RecoveryLotDecision(
            settings.lot_size,
            "Previous trade was already the doubled recovery trade -- reset to "
            "normal lot regardless of its outcome.",
        )

    if last_trade.result is None:
        # Still open/unresolved (or UNKNOWN, pending reconciliation) -- can't
        # know win/loss yet. `max_open_trades` typically prevents a new
        # signal from executing while a prior trade is still open anyway;
        # this is a defensive fallback, not the primary gate.
        return RecoveryLotDecision(
            settings.lot_size, "Previous trade's result isn't known yet -- normal lot."
        )

    if last_trade.result == TradeResult.LOSS:
        return RecoveryLotDecision(
            settings.lot_size * settings.martingale_lot_multiplier,
            "Previous (normal-lot) trade lost -- doubling this trade.",
        )

    return RecoveryLotDecision(
        settings.lot_size,
        f"Previous trade result was {last_trade.result.value}, not a loss -- normal lot.",
    )
