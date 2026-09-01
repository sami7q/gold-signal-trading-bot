"""Daily/weekly lot plan wired end-to-end through `TradingEngine` (not just
the pure logic in test_daily_lot_plan.py) -- confirms the rules engine
integration, the "today's trades" DB query, and the actual lot submitted to
the broker all agree.

`datetime.now(UTC)` inside app.trading.engine is monkeypatched to a fixed
clock so these tests are deterministic regardless of which real calendar
day/weekday they happen to run on -- without it, a test asserting "first
trade of the day uses the base lot" would spuriously fail if actually run
on a real Monday.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlmodel import select

from app.audit.service import AuditService
from app.core.constants import CANONICAL_GOLD_SYMBOL, OrderType, Side, SignalStatus, TradeStatus
from app.database.models.signal import Signal
from app.database.models.trade import Trade
from app.database.models.trading_settings import TradingSettings
from app.integrations.broker.paper import PaperBroker
from app.trading.engine import TradingEngine
from app.trading.reconciliation import ReconciliationService

#: A fixed, known Tuesday at noon Baghdad time (09:00 UTC) -- see
#: test_daily_lot_plan.py, which confirms this date's weekday directly.
_FIXED_NOW = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return _FIXED_NOW.astimezone(tz) if tz is not None else _FIXED_NOW


@pytest.fixture(autouse=True)
def _freeze_engine_clock(monkeypatch):
    monkeypatch.setattr("app.trading.engine.datetime", _FixedDatetime)
    # Trade.created_at's default_factory reads `datetime` from its own
    # module -- a separate reference the patch above doesn't reach. Without
    # this, every Trade row gets stamped with the real wall-clock time,
    # which falls outside _todays_trades()'s window built around
    # _FIXED_NOW, silently making every trade look like "no trades today".
    monkeypatch.setattr("app.database.models.trade.datetime", _FixedDatetime)


def _broker(starting_price: Decimal = Decimal("4638.30")) -> PaperBroker:
    return PaperBroker(
        starting_balance=Decimal("100000"),
        symbol=CANONICAL_GOLD_SYMBOL,
        point=Decimal("0.01"),
        digits=2,
        contract_size=Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("50"),
        volume_step=Decimal("0.01"),
        spread_points=20,
        starting_price=starting_price,
    )


def _settings(**overrides) -> TradingSettings:
    defaults = dict(
        auto_trading=True,
        buy_enabled=True,
        sell_enabled=True,
        lot_size=Decimal("1"),
        target_pips=Decimal("100"),
        require_stop_loss=True,
        max_open_trades=10,  # isolate the daily-plan cap from the concurrency cap
        # Disabled here (not what these tests are about) -- the price moves
        # across trades within a test (each loss/win resets it), and a
        # fixed default Stop Loss would otherwise drift in and out of range
        # for reasons unrelated to the daily lot plan. Re-enabled explicitly
        # in the one test that's actually about this rule.
        max_channel_stop_pips=None,
        daily_lot_plan_enabled=True,
        martingale_lot_multiplier=Decimal("2"),
        monday_first_trade_lot=Decimal("6"),
        monday_second_trade_lot=Decimal("2"),
        max_trades_per_day=2,
    )
    defaults.update(overrides)
    return TradingSettings(**defaults)


def _signal(session, *, stop_loss: Decimal | None = Decimal("4623")) -> Signal:
    signal = Signal(
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        signal_type=OrderType.MARKET,
        channel_stop_loss=stop_loss,
        status=SignalStatus.READY,
        source="mock",
        channel_id="chan-1",
        first_message_at=_FIXED_NOW,
    )
    session.add(signal)
    session.commit()
    session.refresh(signal)
    return signal


def _settle_at_price(session, broker: PaperBroker, price: Decimal) -> None:
    """`PaperBroker.set_price` only updates the broker's own in-memory
    position -- never the DB `Trade` row, exactly like a real MT5 position
    closing server-side. The daily lot plan reads `Trade.result` from the
    DB, so tests must reconcile afterward, the same as the real periodic
    poll loop (app/lifecycle.py::_poll_open_trades) does live."""
    broker.set_price(price)
    ReconciliationService(session, broker, AuditService(session)).reconcile_open_trades()


def test_first_trade_of_the_day_uses_base_lot(session):
    signal = _signal(session)
    engine = TradingEngine(session, _broker(), AuditService(session))

    trade = engine.execute(signal, _settings())

    assert trade.status == TradeStatus.OPEN
    assert trade.requested_lot == Decimal("1")


def test_second_trade_rejected_after_first_wins(session):
    broker = _broker()
    engine = TradingEngine(session, broker, AuditService(session))

    first_signal = _signal(session)
    first_trade = engine.execute(first_signal, _settings())
    _settle_at_price(session, broker, first_trade.take_profit_price)  # win it
    session.refresh(first_trade)
    assert first_trade.status == TradeStatus.CLOSED_TP
    assert first_trade.result.value == "WIN"

    second_signal = _signal(session)
    second_trade = engine.execute(second_signal, _settings())

    assert second_trade.status == TradeStatus.REJECTED
    assert second_trade.failure_reason == "DAILY_TRADE_LIMIT_REACHED"


def test_second_trade_after_a_loss_uses_doubled_lot(session):
    broker = _broker()
    engine = TradingEngine(session, broker, AuditService(session))

    first_signal = _signal(session, stop_loss=Decimal("4623"))
    first_trade = engine.execute(first_signal, _settings())
    _settle_at_price(session, broker, first_trade.stop_loss)  # lose it
    session.refresh(first_trade)
    assert first_trade.status == TradeStatus.CLOSED_SL
    assert first_trade.result.value == "LOSS"

    second_signal = _signal(session, stop_loss=Decimal("4600"))
    second_trade = engine.execute(second_signal, _settings())

    assert second_trade.status == TradeStatus.OPEN
    assert second_trade.requested_lot == Decimal("2")  # 1 * martingale_lot_multiplier(2)


def test_third_trade_rejected_even_after_second_trade_loses(session):
    broker = _broker()
    engine = TradingEngine(session, broker, AuditService(session))

    first_trade = engine.execute(_signal(session), _settings())
    _settle_at_price(session, broker, first_trade.stop_loss)
    session.refresh(first_trade)

    second_trade = engine.execute(_signal(session), _settings())
    _settle_at_price(session, broker, second_trade.stop_loss)
    session.refresh(second_trade)
    assert second_trade.status == TradeStatus.CLOSED_SL

    third_trade = engine.execute(_signal(session), _settings())
    assert third_trade.status == TradeStatus.REJECTED
    assert third_trade.failure_reason == "DAILY_TRADE_LIMIT_REACHED"


def test_daily_lot_plan_disabled_falls_back_to_plain_lot_size(session):
    broker = _broker()
    engine = TradingEngine(session, broker, AuditService(session))
    settings = _settings(daily_lot_plan_enabled=False, lot_size=Decimal("0.5"))

    first_trade = engine.execute(_signal(session), settings)
    broker.set_price(first_trade.take_profit_price)
    session.refresh(first_trade)

    # Disabled -- a second trade the same day is allowed, still at the
    # plain configured lot_size, no daily-plan rejection at all.
    second_trade = engine.execute(_signal(session), settings)
    assert second_trade.status == TradeStatus.OPEN
    assert second_trade.requested_lot == Decimal("0.5")


def test_max_channel_stop_pips_still_enforced_alongside_the_daily_plan(session):
    # A wide Stop Loss must still be rejected on its own terms (150 pip cap
    # from this same request), independent of the daily lot plan.
    signal = _signal(session, stop_loss=Decimal("4400"))  # ~2300 pips away
    engine = TradingEngine(session, _broker(), AuditService(session))

    trade = engine.execute(signal, _settings(max_channel_stop_pips=Decimal("150")))

    assert trade.status == TradeStatus.REJECTED
    assert trade.failure_reason == "STOP_LOSS_TOO_WIDE"


def test_todays_trades_query_ignores_rejected_and_failed(session):
    # A REJECTED trade (no Stop Loss at all, require_stop_loss=True) must
    # never count as "today's first trade" for the daily plan -- it never
    # reached the broker.
    engine = TradingEngine(session, _broker(), AuditService(session))
    rejected = engine.execute(_signal(session, stop_loss=None), _settings())
    assert rejected.status == TradeStatus.REJECTED
    assert rejected.failure_reason == "STOP_LOSS_REQUIRED"

    real_first = engine.execute(_signal(session), _settings())
    assert real_first.status == TradeStatus.OPEN
    assert real_first.requested_lot == Decimal("1")  # still treated as trade #1, not #2

    todays = list(session.exec(select(Trade)))
    assert len(todays) == 2  # both rows exist...
    # ...but only the real one should ever feed the lot-plan decision,
    # which the assertion above already confirms indirectly (lot == 1, not 2).
