"""Daily/weekly lot plan -- operator-requested recovery schedule (2026-09-01
request): trade once a day at a fixed lot; only re-enter (once) at a bigger
lot if that first trade lost; never a third trade the same day. Monday gets
its own, larger schedule as the start of the trading week.

Pure logic, DB-free, deliberately separate from `app/trading/rules.py` and
`app/trading/engine.py` so the day/week arithmetic is unit-testable in
isolation without a broker or a database. `TradingEngine` is the only
caller: it queries today's already-executed trades and passes them in.

Rule, exactly as specified:
  - "Today" and "Monday" are Asia/Baghdad calendar days (the operator's
    own timezone) -- not UTC, which would shift the day boundary by three
    hours from how the operator actually thinks about "today".
  - Trade 1 of the day: `monday_first_trade_lot` on Monday, `lot_size`
    every other day.
  - Trade 2 of the day: ONLY reached if trade 1's result was a LOSS (a WIN
    or BREAKEVEN stops the day at one trade -- there is nothing to
    recover). `monday_second_trade_lot` on Monday,
    `lot_size * martingale_lot_multiplier` every other day.
  - Trade 3+: never allowed, regardless of trade 2's outcome.
  - `max_trades_per_day` (default 2) enforces the "never a third trade"
    rule generically, so it also caps any future change to allow more.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.core.constants import TradeResult
from app.database.models.trade import Trade
from app.database.models.trading_settings import TradingSettings

#: The operator's own timezone -- see module docstring. Iraq does not
#: observe DST, but ZoneInfo handles that correctly either way rather than
#: assuming a fixed UTC+3 offset forever.
BAGHDAD_TZ = ZoneInfo("Asia/Baghdad")


def trading_day(now: datetime) -> date:
    """The Asia/Baghdad calendar date `now` falls on."""
    return now.astimezone(BAGHDAD_TZ).date()


def is_monday(now: datetime) -> bool:
    return trading_day(now).weekday() == 0  # date.weekday(): Monday == 0


@dataclass(frozen=True, slots=True)
class LotPlanDecision:
    allowed: bool
    lot: Decimal | None
    explanation: str


def next_trade_lot(
    todays_trades: list[Trade], *, now: datetime, settings: TradingSettings
) -> LotPlanDecision:
    """`todays_trades` must be every trade actually opened today (Asia/
    Baghdad calendar day) that reached the broker -- i.e. status OPEN,
    UNKNOWN, or any CLOSED_* variant; never REJECTED/FAILED, which never
    entered the market and so don't count as a "trade" for this rule.
    Ordered oldest first. Returns the lot for the *next* trade, or
    `allowed=False` with `lot=None` if no more trades are allowed today.
    """
    count = len(todays_trades)

    if count >= settings.max_trades_per_day:
        return LotPlanDecision(
            allowed=False,
            lot=None,
            explanation=f"{count} of {settings.max_trades_per_day} trades already taken today.",
        )

    monday = is_monday(now)

    if count == 0:
        lot = settings.monday_first_trade_lot if monday else settings.lot_size
        return LotPlanDecision(allowed=True, lot=lot, explanation="First trade of the day.")

    # count == 1 here (count >= 2 already returned above with the default
    # max_trades_per_day=2; a larger cap would just never reach this
    # branch a second time, since nothing produces more than one prior
    # trade to inspect under the operator's "never a third" rule).
    first_trade = todays_trades[0]
    if first_trade.result is None:
        # Still open/unresolved (or UNKNOWN, pending reconciliation) --
        # can't know win/loss yet, so no second trade yet either.
        # max_open_trades=1 already prevents a second signal from
        # executing while the first is still open; this is a defensive
        # second guard, not the primary gate.
        return LotPlanDecision(
            allowed=False,
            lot=None,
            explanation="Today's first trade is still open/unresolved.",
        )

    if first_trade.result != TradeResult.LOSS:
        # WIN or BREAKEVEN: nothing to recover from.
        return LotPlanDecision(
            allowed=False,
            lot=None,
            explanation=f"Today's first trade result was {first_trade.result.value}, not a "
            "loss -- no second trade today.",
        )

    lot = (
        settings.monday_second_trade_lot
        if monday
        else settings.lot_size * settings.martingale_lot_multiplier
    )
    return LotPlanDecision(
        allowed=True, lot=lot, explanation="Recovery trade after today's first trade lost."
    )
