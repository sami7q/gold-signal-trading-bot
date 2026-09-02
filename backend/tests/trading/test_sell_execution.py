"""End-to-end SELL market order execution through `TradingEngine` (v1.3:
SELL was previously always rejected as UNSUPPORTED_SIGNAL_TYPE regardless of
`sell_enabled` -- the broker adapters had no `open_sell` at all). Mirrors the
BUY coverage already in test_rules.py / test_paper_broker.py, exercised here
through the full engine instead of the rules engine or broker in isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.audit.service import AuditService
from app.core.constants import (
    CANONICAL_GOLD_SYMBOL,
    OrderType,
    RejectionReason,
    Side,
    SignalStatus,
    TradeStatus,
)
from app.database.models.signal import Signal
from app.database.models.trading_settings import TradingSettings
from app.integrations.broker.base import BrokerAccount, BrokerHealth, Quote, SymbolInfo
from app.integrations.broker.paper import PaperBroker
from app.trading.engine import TradingEngine
from app.trading.rules import TradingRulesEngine


def _broker(starting_price: Decimal = Decimal("4638.30")) -> PaperBroker:
    return PaperBroker(
        starting_balance=Decimal("10000"),
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
        lot_size=Decimal("1"),
        target_pips=Decimal("100"),
        require_stop_loss=True,
        max_open_trades=1,
        max_channel_stop_pips=Decimal("180"),
    )
    defaults.update(overrides)
    return TradingSettings(**defaults)


def _sell_signal(session, *, stop_loss: Decimal = Decimal("4650")) -> Signal:
    signal = Signal(
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.SELL,
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


def test_sell_signal_executes_and_opens_a_real_position(session, seed_account):
    signal = _sell_signal(session)
    engine = TradingEngine(session, _broker(), AuditService(session))

    trade = engine.execute(signal, _settings(seed_account.id))

    assert trade.status == TradeStatus.OPEN
    assert trade.side == Side.SELL
    assert trade.stop_loss == Decimal("4650")
    session.refresh(signal)
    assert signal.status == SignalStatus.EXECUTED


def test_sell_take_profit_is_below_entry_by_target_pips(session, seed_account):
    # PaperBroker's bid = 4638.30, spread 20 points (0.20) -> SELL fills at
    # bid = 4638.30. pip_size for 2-digit gold = 0.1; 100 pips = 10.00 below.
    signal = _sell_signal(session)
    engine = TradingEngine(session, _broker(), AuditService(session))

    trade = engine.execute(signal, _settings(seed_account.id, target_pips=Decimal("100")))

    assert trade.entry_price == Decimal("4638.30")
    assert trade.take_profit_price == Decimal("4628.30")


def test_sell_disabled_rejects_without_touching_the_broker(session, seed_account):
    signal = _sell_signal(session)
    engine = TradingEngine(session, _broker(), AuditService(session))

    trade = engine.execute(signal, _settings(seed_account.id, sell_enabled=False))

    assert trade.status == TradeStatus.REJECTED
    assert trade.failure_reason == "SELL_DISABLED"


def test_sell_position_closes_at_take_profit_when_price_falls(session, seed_account):
    signal = _sell_signal(session)
    broker = _broker()
    engine = TradingEngine(session, broker, AuditService(session))

    trade = engine.execute(signal, _settings(seed_account.id, target_pips=Decimal("100")))
    assert trade.broker_position_id is not None

    closed = broker.set_price(trade.take_profit_price)
    assert len(closed) == 1
    position = broker.get_position(trade.broker_position_id)
    assert position.close_reason == "TP"
    assert position.profit > 0


def test_sell_position_closes_at_stop_loss_when_price_rises(session, seed_account):
    signal = _sell_signal(session, stop_loss=Decimal("4650"))
    broker = _broker()
    engine = TradingEngine(session, broker, AuditService(session))

    trade = engine.execute(signal, _settings(seed_account.id))
    closed = broker.set_price(Decimal("4650"))
    assert len(closed) == 1
    position = broker.get_position(trade.broker_position_id)
    assert position.close_reason == "SL"
    assert position.profit < 0


def test_buy_and_sell_are_independent_toggles(session, seed_account):
    # buy_enabled=True, sell_enabled=False must still let BUY through --
    # confirms the two checks are genuinely independent, not accidentally
    # coupled (e.g. one flag gating both sides).
    symbol_info = SymbolInfo(
        symbol=CANONICAL_GOLD_SYMBOL,
        digits=2,
        point=Decimal("0.01"),
        contract_size=Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("50"),
        volume_step=Decimal("0.01"),
    )
    quote = Quote(bid=Decimal("4638.30"), ask=Decimal("4638.50"))
    buy_signal = Signal(
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        signal_type=OrderType.MARKET,
        channel_stop_loss=Decimal("4623"),
        status=SignalStatus.READY,
        source="mock",
        channel_id="chan-1",
        first_message_at=datetime.now(UTC),
    )
    settings = _settings(seed_account.id, sell_enabled=False)
    decision = TradingRulesEngine.evaluate(
        signal=buy_signal,
        settings=settings,
        account=BrokerAccount(balance=Decimal("10000"), equity=Decimal("10000")),
        open_trades_count=0,
        broker_health=BrokerHealth(healthy=True),
        symbol_info=symbol_info,
        quote=quote,
        already_executed=False,
        effective_lot=settings.lot_size,
    )
    assert decision.allowed is True
    assert decision.reason_code != RejectionReason.SELL_DISABLED.value
