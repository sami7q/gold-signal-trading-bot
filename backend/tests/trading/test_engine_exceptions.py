"""HIGH-2 fix: a broker adapter that raises instead of returning a graceful
`BrokerExecution(success=False, ...)` must never leave a trade stuck in
SUBMITTING/EXECUTION_PENDING, and must never produce a fake OPEN trade."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlmodel import select

from app.audit.service import AuditService
from app.core.constants import (
    CANONICAL_GOLD_SYMBOL,
    AuditEventType,
    OrderType,
    Side,
    SignalStatus,
    TradeStatus,
)
from app.database.models.audit_event import AuditEvent
from app.database.models.signal import Signal
from app.database.models.trade import Trade
from app.database.models.trading_settings import TradingSettings
from app.integrations.broker.base import BrokerAccount, BrokerHealth, Quote, SymbolInfo
from app.trading.engine import TradingEngine


class _RaisingBroker:
    """Healthy in every other respect; only open_buy() blows up."""

    name = "raising"

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def health(self) -> BrokerHealth:
        return BrokerHealth(healthy=True)

    def get_account(self) -> BrokerAccount:
        return BrokerAccount(balance=Decimal("10000"), equity=Decimal("10000"))

    def get_symbol(self, symbol: str) -> SymbolInfo:
        return SymbolInfo(
            symbol=symbol,
            digits=2,
            point=Decimal("0.01"),
            contract_size=Decimal("100"),
            volume_min=Decimal("0.01"),
            volume_max=Decimal("50"),
            volume_step=Decimal("0.01"),
        )

    def get_quote(self, symbol: str) -> Quote:
        return Quote(bid=Decimal("4638.30"), ask=Decimal("4638.50"))

    def open_buy(self, request):  # noqa: ANN001
        raise self._exc

    def open_sell(self, request):  # noqa: ANN001
        raise self._exc

    def get_position(self, position_id: str):  # noqa: ANN001
        return None

    def close_position(self, position_id: str):  # noqa: ANN001
        raise NotImplementedError


def _settings(account_id: int, **overrides) -> TradingSettings:
    defaults = dict(
        account_id=account_id,
        auto_trading=True,
        buy_enabled=True,
        lot_size=Decimal("0.10"),
        target_pips=Decimal("100"),
        require_stop_loss=True,
        max_open_trades=1,
    )
    defaults.update(overrides)
    return TradingSettings(**defaults)


def _ready_signal(session, *, side: Side = Side.BUY) -> Signal:
    signal = Signal(
        symbol=CANONICAL_GOLD_SYMBOL,
        side=side,
        signal_type=OrderType.MARKET,
        # Valid Stop Loss on the correct side of the raising broker's quote
        # (bid=4638.30, ask=4638.50) either way: below for BUY, above for SELL.
        channel_stop_loss=Decimal("4623") if side == Side.BUY else Decimal("4650"),
        status=SignalStatus.READY,
        source="mock",
        channel_id="chan-1",
        first_message_at=datetime.now(UTC),
    )
    session.add(signal)
    session.commit()
    session.refresh(signal)
    return signal


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionError("timed out talking to broker"),
        TimeoutError("gateway timeout"),
        RuntimeError("unexpected broker failure"),
    ],
)
def test_broker_exception_never_leaves_a_trade_open_or_stuck(session, seed_account, exc):
    signal = _ready_signal(session)
    settings = _settings(seed_account.id)
    broker = _RaisingBroker(exc)
    engine = TradingEngine(session, broker, AuditService(session))

    trade = engine.execute(signal, settings)

    assert trade.status == TradeStatus.FAILED
    assert trade.failure_reason is not None
    assert "Broker error" in trade.failure_reason

    session.refresh(signal)
    assert signal.status == SignalStatus.FAILED

    # No OPEN trade anywhere for this signal.
    all_trades = list(session.exec(select(Trade).where(Trade.signal_id == signal.id)))
    assert len(all_trades) == 1
    assert all(t.status != TradeStatus.OPEN for t in all_trades)


def test_broker_exception_on_sell_never_leaves_a_trade_open_or_stuck(session, seed_account):
    # Same guarantee as the BUY case above, exercised through open_sell()
    # instead of open_buy() -- added alongside SELL market order support.
    signal = _ready_signal(session, side=Side.SELL)
    settings = _settings(seed_account.id, sell_enabled=True)
    broker = _RaisingBroker(ConnectionError("timed out talking to broker"))
    engine = TradingEngine(session, broker, AuditService(session))

    trade = engine.execute(signal, settings)

    assert trade.status == TradeStatus.FAILED
    assert trade.side == Side.SELL
    assert trade.failure_reason is not None
    assert "Broker error" in trade.failure_reason

    session.refresh(signal)
    assert signal.status == SignalStatus.FAILED

    all_trades = list(session.exec(select(Trade).where(Trade.signal_id == signal.id)))
    assert len(all_trades) == 1
    assert all(t.status != TradeStatus.OPEN for t in all_trades)


def test_broker_exception_writes_audit_events(session, seed_account):
    signal = _ready_signal(session)
    settings = _settings(seed_account.id)
    broker = _RaisingBroker(ConnectionError("boom"))
    engine = TradingEngine(session, broker, AuditService(session))

    engine.execute(signal, settings)

    events = list(session.exec(select(AuditEvent)))
    event_types = {e.event_type for e in events}
    assert AuditEventType.BROKER_ERROR.value in event_types
    assert AuditEventType.TRADE_FAILED.value in event_types


def test_broker_exception_does_not_leak_raw_exception_text_to_failure_reason(
    session, seed_account
):
    signal = _ready_signal(session)
    settings = _settings(seed_account.id)
    secret_looking_text = "connection refused: password=hunter2 host=internal-broker"
    broker = _RaisingBroker(ConnectionError(secret_looking_text))
    engine = TradingEngine(session, broker, AuditService(session))

    trade = engine.execute(signal, settings)

    assert secret_looking_text not in (trade.failure_reason or "")
    assert trade.failure_reason == "Broker error: ConnectionError"


def test_retrying_the_same_signal_after_broker_exception_does_not_duplicate(session, seed_account):
    signal = _ready_signal(session)
    settings = _settings(seed_account.id)
    broker = _RaisingBroker(TimeoutError("still down"))
    engine = TradingEngine(session, broker, AuditService(session))

    first = engine.execute(signal, settings)
    second = engine.execute(signal, settings)

    assert first.id == second.id
    all_trades = list(session.exec(select(Trade).where(Trade.signal_id == signal.id)))
    assert len(all_trades) == 1
    assert all_trades[0].status == TradeStatus.FAILED


def test_exception_during_quote_fetch_before_trade_row_exists_is_rejected_not_stuck(
    session, seed_account,
):
    """`get_quote()` is now called early (rule 7b needs a current price to
    screen the channel's Stop Loss width), alongside get_account()/
    get_symbol() -- so a quote-fetch failure is caught there and classified
    as BROKER_UNAVAILABLE (a REJECTED trade), the same as those other two
    failing. It never reaches the later execution-path quote fetch, so it
    can no longer surface as FAILED. Either way the safety property this
    test guards holds: exactly one definitive terminal-state trade row,
    never zero rows, never a crash, never a phantom OPEN."""

    class _RaisingOnQuote(_RaisingBroker):
        def get_quote(self, symbol: str):  # noqa: ANN001
            raise ConnectionError("no quote available")

        def open_buy(self, request):  # noqa: ANN001
            raise AssertionError("open_buy should never be reached")

    signal = _ready_signal(session)
    settings = _settings(seed_account.id)
    engine = TradingEngine(
        session, _RaisingOnQuote(ConnectionError("unused")), AuditService(session)
    )

    trade = engine.execute(signal, settings)

    assert trade.status == TradeStatus.REJECTED
    assert trade.failure_reason == "BROKER_UNAVAILABLE"
    session.refresh(signal)
    assert signal.status == SignalStatus.REJECTED
    all_trades = list(session.exec(select(Trade).where(Trade.signal_id == signal.id)))
    assert len(all_trades) == 1
