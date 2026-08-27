"""Pure statistics calculations (PROJECT_SPEC.md section 14).

Statistics are always derived from trade rows at query time — nothing here
is a stored, mutable "win rate" value. Open trades never count as a win or
a loss.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.constants import CLOSED_TRADE_STATUSES, TradeResult, TradeStatus
from app.core.decimals import CleanDecimal
from app.database.models.trade import Trade

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class TradeStatistics:
    trades_executed: int
    open_trades: int
    closed_trades: int
    wins: int
    losses: int
    win_rate: CleanDecimal
    net_profit: CleanDecimal
    gross_profit: CleanDecimal
    gross_loss: CleanDecimal
    average_win: CleanDecimal
    average_loss: CleanDecimal
    best_trade: CleanDecimal | None
    worst_trade: CleanDecimal | None
    today_trades: int
    today_pl: CleanDecimal


def compute_trade_statistics(trades: list[Trade], *, today: date) -> TradeStatistics:
    executed = [t for t in trades if t.status not in (TradeStatus.REJECTED, TradeStatus.FAILED)]
    open_trades = [t for t in executed if t.status == TradeStatus.OPEN]
    closed_trades = [t for t in executed if t.status in CLOSED_TRADE_STATUSES]

    wins = [t for t in closed_trades if t.result == TradeResult.WIN]
    losses = [t for t in closed_trades if t.result == TradeResult.LOSS]

    win_rate = (Decimal(len(wins)) / Decimal(len(closed_trades)) * 100) if closed_trades else _ZERO

    profits = [t.profit for t in closed_trades if t.profit is not None]
    net_profit = sum(profits, _ZERO)
    gross_profit = sum((p for p in profits if p > 0), _ZERO)
    gross_loss = sum((p for p in profits if p < 0), _ZERO)

    win_profits = [t.profit for t in wins if t.profit is not None]
    loss_profits = [t.profit for t in losses if t.profit is not None]
    average_win = (sum(win_profits, _ZERO) / len(win_profits)) if win_profits else _ZERO
    average_loss = (sum(loss_profits, _ZERO) / len(loss_profits)) if loss_profits else _ZERO

    best_trade = max(profits) if profits else None
    worst_trade = min(profits) if profits else None

    today_closed = [
        t for t in closed_trades if t.closed_at is not None and t.closed_at.date() == today
    ]
    today_pl = sum((t.profit for t in today_closed if t.profit is not None), _ZERO)

    return TradeStatistics(
        trades_executed=len(executed),
        open_trades=len(open_trades),
        closed_trades=len(closed_trades),
        wins=len(wins),
        losses=len(losses),
        win_rate=win_rate,
        net_profit=net_profit,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        average_win=average_win,
        average_loss=average_loss,
        best_trade=best_trade,
        worst_trade=worst_trade,
        today_trades=len(today_closed),
        today_pl=today_pl,
    )


@dataclass(frozen=True, slots=True)
class ProfitCurvePoint:
    closed_at: str
    trade_id: int
    profit: CleanDecimal
    cumulative_profit: CleanDecimal


def compute_profit_curve(trades: list[Trade]) -> list[ProfitCurvePoint]:
    eligible = [
        t
        for t in trades
        if t.status in CLOSED_TRADE_STATUSES and t.closed_at is not None and t.profit is not None
    ]
    closed = sorted(eligible, key=lambda t: t.closed_at)  # type: ignore[arg-type,return-value]

    curve: list[ProfitCurvePoint] = []
    running = _ZERO
    for trade in closed:
        closed_at, profit, trade_id = trade.closed_at, trade.profit, trade.id
        assert closed_at is not None and profit is not None and trade_id is not None
        running += profit
        curve.append(
            ProfitCurvePoint(
                closed_at=closed_at.isoformat(),
                trade_id=trade_id,
                profit=profit,
                cumulative_profit=running,
            )
        )
    return curve


@dataclass(frozen=True, slots=True)
class DailyPerformance:
    day: str
    trades: int
    wins: int
    losses: int
    profit: CleanDecimal


@dataclass(slots=True)
class _DailyBucket:
    trades: int = 0
    wins: int = 0
    losses: int = 0
    profit: Decimal = _ZERO


@dataclass(frozen=True, slots=True)
class OrderPositionPerformance:
    """Win rate of "the Nth trade opened each day", aggregated across every
    day that had at least N trades. `position=1` is the first trade opened
    on a given day, `position=2` the second, and so on -- answers "does the
    first signal of the day perform differently than later ones?"."""

    position: int
    trades: int
    wins: int
    losses: int
    win_rate: CleanDecimal


def compute_trade_order_performance(trades: list[Trade]) -> list[OrderPositionPerformance]:
    excluded_statuses = (TradeStatus.REJECTED, TradeStatus.FAILED)
    eligible = [t for t in trades if t.status not in excluded_statuses and t.opened_at]

    by_day: dict[date, list[Trade]] = {}
    for trade in eligible:
        assert trade.opened_at is not None
        by_day.setdefault(trade.opened_at.date(), []).append(trade)

    by_position: dict[int, list[Trade]] = {}
    for day_trades in by_day.values():
        day_trades.sort(key=lambda t: t.opened_at)  # type: ignore[arg-type,return-value]
        for position, trade in enumerate(day_trades, start=1):
            by_position.setdefault(position, []).append(trade)

    results: list[OrderPositionPerformance] = []
    for position in sorted(by_position):
        bucket = by_position[position]
        closed = [t for t in bucket if t.status in CLOSED_TRADE_STATUSES]
        wins = [t for t in closed if t.result == TradeResult.WIN]
        losses = [t for t in closed if t.result == TradeResult.LOSS]
        win_rate = (Decimal(len(wins)) / Decimal(len(closed)) * 100) if closed else _ZERO
        results.append(
            OrderPositionPerformance(
                position=position,
                trades=len(bucket),
                wins=len(wins),
                losses=len(losses),
                win_rate=win_rate,
            )
        )
    return results


def compute_daily_performance(trades: list[Trade]) -> list[DailyPerformance]:
    buckets: dict[str, _DailyBucket] = {}
    for trade in trades:
        if trade.status not in CLOSED_TRADE_STATUSES or trade.closed_at is None:
            continue
        key = trade.closed_at.date().isoformat()
        bucket = buckets.setdefault(key, _DailyBucket())
        bucket.trades += 1
        if trade.result == TradeResult.WIN:
            bucket.wins += 1
        elif trade.result == TradeResult.LOSS:
            bucket.losses += 1
        bucket.profit += trade.profit or _ZERO

    return [
        DailyPerformance(day=day, trades=b.trades, wins=b.wins, losses=b.losses, profit=b.profit)
        for day, b in sorted(buckets.items())
    ]
