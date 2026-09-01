"""Daily/weekly lot plan (operator-requested recovery schedule,
2026-09-01): app/trading/daily_lot_plan.py. Pure logic, DB-free -- `Trade`
objects here are constructed in memory and never persisted."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.core.constants import CANONICAL_GOLD_SYMBOL, Side, TradeResult, TradeStatus
from app.database.models.trade import Trade
from app.database.models.trading_settings import TradingSettings
from app.trading.daily_lot_plan import is_monday, next_trade_lot, trading_day


def _settings(**overrides) -> TradingSettings:
    defaults = dict(
        lot_size=Decimal("1"),
        martingale_lot_multiplier=Decimal("2"),
        monday_first_trade_lot=Decimal("6"),
        monday_second_trade_lot=Decimal("2"),
        max_trades_per_day=2,
    )
    defaults.update(overrides)
    return TradingSettings(**defaults)


def _trade(*, result: TradeResult | None, status: TradeStatus = TradeStatus.CLOSED_TP) -> Trade:
    return Trade(
        signal_id=1,
        execution_key="1:v1",
        broker="mt5",
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        requested_lot=Decimal("1"),
        target_pips=Decimal("100"),
        status=status,
        result=result,
    )


# 2026-08-31 is a Monday; 2026-09-01 a Tuesday -- both timestamps sit at
# noon Baghdad time, comfortably inside their calendar day either way.
_MONDAY_NOON_UTC = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
_TUESDAY_NOON_UTC = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def test_is_monday_true_for_a_known_monday():
    assert is_monday(_MONDAY_NOON_UTC) is True


def test_is_monday_false_for_a_known_tuesday():
    assert is_monday(_TUESDAY_NOON_UTC) is False


def test_trading_day_uses_baghdad_offset_not_utc():
    # 23:30 UTC on Sunday 2026-08-30 is already 02:30 Monday in Baghdad
    # (UTC+3) -- the day boundary must follow Baghdad, not UTC midnight.
    sunday_late_utc = datetime(2026, 8, 30, 23, 30, tzinfo=UTC)
    assert trading_day(sunday_late_utc) == trading_day(_MONDAY_NOON_UTC)
    assert is_monday(sunday_late_utc) is True


def test_first_trade_of_a_normal_day_uses_base_lot():
    decision = next_trade_lot([], now=_TUESDAY_NOON_UTC, settings=_settings())
    assert decision.allowed is True
    assert decision.lot == Decimal("1")


def test_first_trade_of_monday_uses_monday_lot():
    decision = next_trade_lot([], now=_MONDAY_NOON_UTC, settings=_settings())
    assert decision.allowed is True
    assert decision.lot == Decimal("6")


def test_no_second_trade_after_a_win_on_a_normal_day():
    first = _trade(result=TradeResult.WIN)
    decision = next_trade_lot([first], now=_TUESDAY_NOON_UTC, settings=_settings())
    assert decision.allowed is False
    assert decision.lot is None


def test_no_second_trade_after_breakeven():
    # Nothing was actually lost -- treated the same as a win, not a loss.
    first = _trade(result=TradeResult.BREAKEVEN)
    decision = next_trade_lot([first], now=_TUESDAY_NOON_UTC, settings=_settings())
    assert decision.allowed is False


def test_second_trade_after_a_loss_on_a_normal_day_doubles_the_lot():
    first = _trade(result=TradeResult.LOSS)
    decision = next_trade_lot([first], now=_TUESDAY_NOON_UTC, settings=_settings())
    assert decision.allowed is True
    assert decision.lot == Decimal("2")  # lot_size(1) * martingale_lot_multiplier(2)


def test_second_trade_after_monday_loss_uses_monday_second_lot_not_a_multiplier():
    first = _trade(result=TradeResult.LOSS)
    decision = next_trade_lot([first], now=_MONDAY_NOON_UTC, settings=_settings())
    assert decision.allowed is True
    assert decision.lot == Decimal("2")  # monday_second_trade_lot, explicitly NOT 6*2=12


def test_no_second_trade_after_monday_win():
    first = _trade(result=TradeResult.WIN)
    decision = next_trade_lot([first], now=_MONDAY_NOON_UTC, settings=_settings())
    assert decision.allowed is False


def test_no_third_trade_after_second_trade_wins():
    trades = [_trade(result=TradeResult.LOSS), _trade(result=TradeResult.WIN)]
    decision = next_trade_lot(trades, now=_TUESDAY_NOON_UTC, settings=_settings())
    assert decision.allowed is False


def test_no_third_trade_after_second_trade_loses():
    trades = [_trade(result=TradeResult.LOSS), _trade(result=TradeResult.LOSS)]
    decision = next_trade_lot(trades, now=_TUESDAY_NOON_UTC, settings=_settings())
    assert decision.allowed is False


def test_still_open_first_trade_blocks_a_second_defensively():
    # max_open_trades=1 is the primary gate against this in practice, but
    # the lot plan must never guess a win/loss for an unresolved trade.
    first = _trade(result=None, status=TradeStatus.OPEN)
    decision = next_trade_lot([first], now=_TUESDAY_NOON_UTC, settings=_settings())
    assert decision.allowed is False


def test_max_trades_per_day_is_configurable():
    settings = _settings(max_trades_per_day=1)
    first_decision = next_trade_lot([], now=_TUESDAY_NOON_UTC, settings=settings)
    assert first_decision.allowed is True

    first = _trade(result=TradeResult.LOSS)
    second_decision = next_trade_lot([first], now=_TUESDAY_NOON_UTC, settings=settings)
    assert second_decision.allowed is False  # cap=1 blocks even a loss-recovery attempt
