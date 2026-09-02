"""Loss-recovery lot plan (operator-requested rule, 2026-09-03):
app/trading/loss_recovery.py. Pure logic, DB-free -- `Trade` objects here
are constructed in memory and never persisted."""

from __future__ import annotations

from decimal import Decimal

from app.core.constants import CANONICAL_GOLD_SYMBOL, Side, TradeResult, TradeStatus
from app.database.models.trade import Trade
from app.database.models.trading_settings import TradingSettings
from app.trading.loss_recovery import next_trade_lot


def _settings(**overrides) -> TradingSettings:
    defaults = dict(
        lot_size=Decimal("0.025"),
        martingale_lot_multiplier=Decimal("2"),
    )
    defaults.update(overrides)
    return TradingSettings(**defaults)


def _trade(
    *,
    requested_lot: Decimal,
    result: TradeResult | None,
    status: TradeStatus = TradeStatus.CLOSED_TP,
) -> Trade:
    return Trade(
        signal_id=1,
        execution_key="1:v1",
        broker="mt5",
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        requested_lot=requested_lot,
        target_pips=Decimal("100"),
        status=status,
        result=result,
    )


def test_no_prior_trade_uses_normal_lot():
    decision = next_trade_lot(None, settings=_settings())
    assert decision.lot == Decimal("0.025")


def test_normal_trade_that_won_keeps_normal_lot_next():
    last = _trade(requested_lot=Decimal("0.025"), result=TradeResult.WIN)
    decision = next_trade_lot(last, settings=_settings())
    assert decision.lot == Decimal("0.025")


def test_normal_trade_that_broke_even_keeps_normal_lot_next():
    last = _trade(requested_lot=Decimal("0.025"), result=TradeResult.BREAKEVEN)
    decision = next_trade_lot(last, settings=_settings())
    assert decision.lot == Decimal("0.025")


def test_normal_trade_that_lost_doubles_the_next_lot():
    last = _trade(requested_lot=Decimal("0.025"), result=TradeResult.LOSS)
    decision = next_trade_lot(last, settings=_settings())
    assert decision.lot == Decimal("0.050")  # lot_size(0.025) * multiplier(2)


def test_recovery_trade_that_won_resets_to_normal_lot_regardless():
    # The doubled trade itself won -- still resets, per the operator's
    # explicit rule ("سواء خسر او فاز الثانيه يدخل الثالثه ب لوت عادي").
    last = _trade(requested_lot=Decimal("0.050"), result=TradeResult.WIN)
    decision = next_trade_lot(last, settings=_settings())
    assert decision.lot == Decimal("0.025")


def test_recovery_trade_that_lost_also_resets_to_normal_lot_not_a_second_double():
    # No unlimited martingale: exactly one recovery attempt, then reset --
    # even if that recovery attempt itself lost.
    last = _trade(requested_lot=Decimal("0.050"), result=TradeResult.LOSS)
    decision = next_trade_lot(last, settings=_settings())
    assert decision.lot == Decimal("0.025")


def test_still_open_prior_trade_defaults_to_normal_lot():
    # max_open_trades typically prevents this from being reachable in
    # practice, but the plan must never guess a win/loss for an unresolved
    # trade -- normal lot is the safe fallback.
    last = _trade(requested_lot=Decimal("0.025"), result=None, status=TradeStatus.OPEN)
    decision = next_trade_lot(last, settings=_settings())
    assert decision.lot == Decimal("0.025")


def test_three_trade_cycle_normal_double_reset():
    settings = _settings()

    # Trade 1: no history yet.
    d1 = next_trade_lot(None, settings=settings)
    assert d1.lot == Decimal("0.025")
    t1 = _trade(requested_lot=d1.lot, result=TradeResult.LOSS)

    # Trade 2: recovers after trade 1's loss.
    d2 = next_trade_lot(t1, settings=settings)
    assert d2.lot == Decimal("0.050")
    t2 = _trade(requested_lot=d2.lot, result=TradeResult.LOSS)  # recovery trade also loses

    # Trade 3: resets to normal regardless of trade 2's outcome.
    d3 = next_trade_lot(t2, settings=settings)
    assert d3.lot == Decimal("0.025")
