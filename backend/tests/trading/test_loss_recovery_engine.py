"""Loss-recovery lot plan wired end-to-end through `TradingEngine` (not
just the pure logic in test_loss_recovery.py) -- confirms the rules engine
integration, the "last trade" DB query, and the actual lot submitted to
the broker all agree. Unlike the daily lot plan, this is not day-scoped
and never rejects a trade for hitting a count cap -- see
app/trading/loss_recovery.py.
"""

from __future__ import annotations

from decimal import Decimal

from app.audit.service import AuditService
from app.core.constants import CANONICAL_GOLD_SYMBOL, OrderType, Side, SignalStatus, TradeStatus
from app.database.models.signal import Signal
from app.database.models.trading_settings import TradingSettings
from app.integrations.broker.paper import PaperBroker
from app.trading.engine import TradingEngine
from app.trading.reconciliation import ReconciliationService


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


def _settings(account_id: int, **overrides) -> TradingSettings:
    defaults = dict(
        account_id=account_id,
        auto_trading=True,
        buy_enabled=True,
        sell_enabled=True,
        lot_size=Decimal("0.02"),
        target_pips=Decimal("100"),
        require_stop_loss=True,
        max_open_trades=10,  # isolate from the concurrency cap
        max_channel_stop_pips=None,
        daily_lot_plan_enabled=False,
        loss_recovery_enabled=True,
        martingale_lot_multiplier=Decimal("2"),
    )
    defaults.update(overrides)
    return TradingSettings(**defaults)


def _signal(session, *, stop_loss: Decimal | None = Decimal("4623")) -> Signal:
    from datetime import UTC, datetime

    signal = Signal(
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        signal_type=OrderType.MARKET,
        channel_stop_loss=stop_loss,
        status=SignalStatus.READY,
        source="mock",
        channel_id="chan-1",
        first_message_at=datetime.now(UTC),
    )
    session.add(signal)
    session.commit()
    session.refresh(signal)
    return signal


def _settle_at_price(session, broker: PaperBroker, price: Decimal, account_id: int) -> None:
    """See test_daily_lot_plan_engine.py -- `PaperBroker.set_price` only
    updates the broker's own in-memory position, never the DB `Trade` row;
    the recovery plan reads `Trade.result` from the DB, so tests must
    reconcile afterward, the same as the real periodic poll loop does."""
    broker.set_price(price)
    ReconciliationService(session, broker, AuditService(session)).reconcile_open_trades(account_id)


def test_first_trade_uses_normal_lot(session, seed_account):
    engine = TradingEngine(session, _broker(), AuditService(session))
    trade = engine.execute(_signal(session), _settings(seed_account.id))
    assert trade.status == TradeStatus.OPEN
    assert trade.requested_lot == Decimal("0.02")


def test_no_cap_on_trade_count_unlike_the_daily_plan(session, seed_account):
    # Repeated wins never get rejected for hitting any daily/count limit --
    # the operator was explicit: every signal must still execute.
    broker = _broker()
    engine = TradingEngine(session, broker, AuditService(session))
    settings = _settings(seed_account.id)

    for _ in range(4):
        trade = engine.execute(_signal(session), settings)
        assert trade.status == TradeStatus.OPEN
        assert trade.requested_lot == Decimal("0.02")
        _settle_at_price(session, broker, trade.take_profit_price, seed_account.id)  # win it


def test_trade_after_a_loss_doubles_the_lot(session, seed_account):
    broker = _broker()
    engine = TradingEngine(session, broker, AuditService(session))
    settings = _settings(seed_account.id)

    first = engine.execute(_signal(session, stop_loss=Decimal("4623")), settings)
    _settle_at_price(session, broker, first.stop_loss, seed_account.id)  # lose it
    session.refresh(first)
    assert first.result.value == "LOSS"

    second = engine.execute(_signal(session, stop_loss=Decimal("4600")), settings)
    assert second.status == TradeStatus.OPEN
    assert second.requested_lot == Decimal("0.04")  # 0.02 * multiplier(2)


def test_trade_after_the_recovery_trade_resets_regardless_of_its_outcome(session, seed_account):
    broker = _broker()
    engine = TradingEngine(session, broker, AuditService(session))
    settings = _settings(seed_account.id)

    first = engine.execute(_signal(session, stop_loss=Decimal("4623")), settings)
    _settle_at_price(session, broker, first.stop_loss, seed_account.id)  # trade 1 loses

    second = engine.execute(_signal(session, stop_loss=Decimal("4600")), settings)
    assert second.requested_lot == Decimal("0.04")
    # recovery trade ALSO loses
    _settle_at_price(session, broker, second.stop_loss, seed_account.id)

    third = engine.execute(_signal(session, stop_loss=Decimal("4600")), settings)
    assert third.status == TradeStatus.OPEN
    # Back to normal -- not a second doubling, no unlimited martingale.
    assert third.requested_lot == Decimal("0.02")


def test_cycle_repeats_on_a_later_loss(session, seed_account):
    broker = _broker()
    engine = TradingEngine(session, broker, AuditService(session))
    settings = _settings(seed_account.id)

    # Trade 1 wins -> trade 2 stays normal.
    t1 = engine.execute(_signal(session), settings)
    _settle_at_price(session, broker, t1.take_profit_price, seed_account.id)

    t2 = engine.execute(_signal(session, stop_loss=Decimal("4600")), settings)
    assert t2.requested_lot == Decimal("0.02")
    _settle_at_price(session, broker, t2.stop_loss, seed_account.id)  # trade 2 loses

    # Trade 3 must now double, exactly as if trade 2 had been "trade 1".
    t3 = engine.execute(_signal(session, stop_loss=Decimal("4600")), settings)
    assert t3.requested_lot == Decimal("0.04")


def test_loss_recovery_disabled_falls_back_to_plain_lot_size(session, seed_account):
    broker = _broker()
    engine = TradingEngine(session, broker, AuditService(session))
    settings = _settings(seed_account.id, loss_recovery_enabled=False, lot_size=Decimal("0.5"))

    first = engine.execute(_signal(session, stop_loss=Decimal("4623")), settings)
    _settle_at_price(session, broker, first.stop_loss, seed_account.id)  # lose it

    second = engine.execute(_signal(session, stop_loss=Decimal("4600")), settings)
    assert second.requested_lot == Decimal("0.5")  # no doubling -- plan is off


def test_daily_lot_plan_takes_priority_when_both_flags_are_on(session, seed_account, monkeypatch):
    # TradingEngine.execute checks daily_lot_plan_enabled first (see the
    # module docstring in app/trading/loss_recovery.py). On a first trade
    # alone the two plans would coincidentally agree (both use `lot_size`),
    # so this freezes the clock to a known Monday and gives the daily
    # plan's Monday lot a value that diverges from `lot_size` -- only the
    # daily plan branch running produces it; the recovery plan has no
    # concept of Monday at all and would return plain `lot_size` instead.
    from datetime import UTC, datetime

    frozen_monday = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)  # see test_daily_lot_plan.py

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return frozen_monday.astimezone(tz) if tz is not None else frozen_monday

    monkeypatch.setattr("app.trading.engine.datetime", _FixedDatetime)
    monkeypatch.setattr("app.database.models.trade.datetime", _FixedDatetime)

    broker = _broker()
    engine = TradingEngine(session, broker, AuditService(session))
    settings = _settings(
        seed_account.id,
        daily_lot_plan_enabled=True,
        loss_recovery_enabled=True,
        lot_size=Decimal("0.02"),
        monday_first_trade_lot=Decimal("6"),
    )

    trade = engine.execute(_signal(session), settings)
    assert trade.status == TradeStatus.OPEN
    assert trade.requested_lot == Decimal("6")  # the daily plan's Monday lot, not lot_size
