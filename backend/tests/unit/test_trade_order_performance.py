"""compute_trade_order_performance -- "does the 1st/2nd/3rd trade of the
day perform differently than later ones?" (requested explicitly: per-day
ordinal win-rate breakdown, live trades only, never touching the archive)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.analytics.calculations import compute_trade_order_performance
from app.core.constants import Side, TradeResult, TradeStatus
from app.database.models.trade import Trade

_SIGNAL_ID = 1


def _trade(
    *,
    opened_at: datetime,
    status: TradeStatus = TradeStatus.CLOSED_TP,
    result: TradeResult | None = TradeResult.WIN,
    profit: Decimal | None = Decimal("10"),
    execution_key: str,
) -> Trade:
    return Trade(
        signal_id=_SIGNAL_ID,
        execution_key=execution_key,
        broker="mt5",
        symbol="XAUUSD",
        side=Side.BUY,
        requested_lot=Decimal("1"),
        target_pips=Decimal("100"),
        opened_at=opened_at,
        status=status,
        result=result,
        profit=profit,
    )


def test_first_trade_of_each_day_grouped_together_across_days():
    day1 = datetime(2026, 1, 1, tzinfo=UTC)
    day2 = datetime(2026, 1, 2, tzinfo=UTC)
    trades = [
        # Day 1: two trades, 1st wins, 2nd loses.
        _trade(opened_at=day1.replace(hour=9), execution_key="1", result=TradeResult.WIN),
        _trade(
            opened_at=day1.replace(hour=14),
            execution_key="2",
            result=TradeResult.LOSS,
            profit=Decimal("-5"),
        ),
        # Day 2: two trades, 1st loses, 2nd wins.
        _trade(
            opened_at=day2.replace(hour=8),
            execution_key="3",
            result=TradeResult.LOSS,
            profit=Decimal("-5"),
        ),
        _trade(opened_at=day2.replace(hour=16), execution_key="4", result=TradeResult.WIN),
    ]

    result = compute_trade_order_performance(trades)

    by_position = {r.position: r for r in result}
    assert by_position[1].trades == 2
    assert by_position[1].wins == 1
    assert by_position[1].losses == 1
    assert by_position[1].win_rate == Decimal("50")

    assert by_position[2].trades == 2
    assert by_position[2].wins == 1
    assert by_position[2].losses == 1
    assert by_position[2].win_rate == Decimal("50")


def test_position_assigned_by_opened_at_order_not_insertion_order():
    day = datetime(2026, 1, 1, tzinfo=UTC)
    # Inserted out of chronological order -- position must still follow
    # opened_at, not list order.
    trades = [
        _trade(opened_at=day.replace(hour=20), execution_key="late", result=TradeResult.LOSS),
        _trade(opened_at=day.replace(hour=5), execution_key="early", result=TradeResult.WIN),
    ]

    result = compute_trade_order_performance(trades)
    by_position = {r.position: r for r in result}

    assert by_position[1].wins == 1  # the 05:00 trade is "1st", and it won
    assert by_position[2].losses == 1  # the 20:00 trade is "2nd", and it lost


def test_days_with_only_one_trade_do_not_pollute_position_2_or_3():
    day1 = datetime(2026, 1, 1, tzinfo=UTC)
    day2 = datetime(2026, 1, 2, tzinfo=UTC)
    trades = [
        _trade(opened_at=day1.replace(hour=9), execution_key="1"),  # only trade that day
        _trade(opened_at=day2.replace(hour=9), execution_key="2"),
        _trade(opened_at=day2.replace(hour=10), execution_key="3"),
        _trade(opened_at=day2.replace(hour=11), execution_key="4"),
    ]

    result = compute_trade_order_performance(trades)
    by_position = {r.position: r for r in result}

    assert by_position[1].trades == 2  # day1's only trade + day2's first
    assert by_position[2].trades == 1  # only day2 had a 2nd trade
    assert by_position[3].trades == 1  # only day2 had a 3rd trade


def test_open_trades_count_toward_the_position_but_not_win_rate():
    day = datetime(2026, 1, 1, tzinfo=UTC)
    trades = [
        _trade(
            opened_at=day.replace(hour=9),
            execution_key="1",
            status=TradeStatus.OPEN,
            result=None,
            profit=None,
        ),
    ]

    result = compute_trade_order_performance(trades)
    assert result[0].trades == 1
    assert result[0].wins == 0
    assert result[0].losses == 0
    assert result[0].win_rate == Decimal("0")


def test_rejected_and_failed_trades_are_excluded_entirely():
    day = datetime(2026, 1, 1, tzinfo=UTC)
    trades = [
        _trade(
            opened_at=day.replace(hour=9),
            execution_key="1",
            status=TradeStatus.REJECTED,
            result=None,
            profit=None,
        ),
        _trade(
            opened_at=day.replace(hour=10),
            execution_key="2",
            status=TradeStatus.FAILED,
            result=None,
            profit=None,
        ),
    ]

    result = compute_trade_order_performance(trades)
    assert result == []


def test_no_trades_returns_empty_list():
    assert compute_trade_order_performance([]) == []
