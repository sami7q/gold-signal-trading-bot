"""HIGH-3 fix: startup reconciliation between the database and the broker.

Covers PaperBroker's real failure mode (positions are in-memory and vanish
on restart) and the general contract (`ReconciliationService` only ever
calls `broker.get_position()`, so it works unchanged for MT5Broker too)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlmodel import select

from app.audit.service import AuditService
from app.core.constants import (
    CANONICAL_GOLD_SYMBOL,
    AuditEventType,
    OrderType,
    Side,
    SignalStatus,
    TradeResult,
    TradeStatus,
)
from app.database.models.audit_event import AuditEvent
from app.database.models.signal import Signal
from app.database.models.trade import Trade
from app.integrations.broker.base import BrokerPosition
from app.integrations.broker.paper import PaperBroker
from app.trading.engine import TradingEngine
from app.trading.reconciliation import (
    RECONCILIATION_REQUIRED_REASON,
    ReconciliationService,
)


def _signal(session) -> Signal:
    signal = Signal(
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        signal_type=OrderType.MARKET,
        channel_stop_loss=Decimal("4623"),
        status=SignalStatus.EXECUTED,
        source="mock",
        channel_id="chan-1",
        first_message_at=datetime.now(UTC),
    )
    session.add(signal)
    session.commit()
    session.refresh(signal)
    return signal


def _open_trade(session, *, position_id: str = "1") -> Trade:
    trade = Trade(
        signal_id=_signal(session).id,
        execution_key=f"reconcile-{position_id}",
        broker="paper",
        broker_position_id=position_id,
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        requested_lot=Decimal("0.10"),
        executed_lot=Decimal("0.10"),
        entry_price=Decimal("4638.50"),
        stop_loss=Decimal("4623"),
        target_pips=Decimal("100"),
        take_profit_price=Decimal("4639.50"),
        opened_at=datetime.now(UTC),
        status=TradeStatus.OPEN,
    )
    session.add(trade)
    session.commit()
    session.refresh(trade)
    return trade


class _StubBroker:
    name = "stub"

    def __init__(
        self, position: BrokerPosition | None = None, *, raise_on_get: Exception | None = None
    ):
        self._position = position
        self._raise_on_get = raise_on_get

    def get_position(self, position_id: str):  # noqa: ANN001
        if self._raise_on_get is not None:
            raise self._raise_on_get
        return self._position

    def health(self):  # noqa: ANN001
        raise NotImplementedError

    def get_account(self):  # noqa: ANN001
        raise NotImplementedError

    def get_symbol(self, symbol):  # noqa: ANN001
        raise NotImplementedError

    def get_quote(self, symbol):  # noqa: ANN001
        raise NotImplementedError

    def open_buy(self, request):  # noqa: ANN001
        raise NotImplementedError

    def close_position(self, position_id):  # noqa: ANN001
        raise NotImplementedError


def test_position_confirmed_open_stays_open(session):
    trade = _open_trade(session)
    position = BrokerPosition(
        position_id="1",
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        lot=Decimal("0.10"),
        entry_price=Decimal("4638.50"),
        stop_loss=Decimal("4623"),
        take_profit=Decimal("4639.50"),
        is_open=True,
    )
    summary = ReconciliationService(
        session, _StubBroker(position), AuditService(session)
    ).reconcile_open_trades()

    session.refresh(trade)
    assert trade.status == TradeStatus.OPEN
    assert summary.confirmed_open == 1
    assert summary.marked_reconciliation_required == 0

    events = {e.event_type for e in session.exec(select(AuditEvent))}
    assert AuditEventType.POSITION_CONFIRMED.value in events
    assert AuditEventType.RECONCILIATION_STARTED.value in events
    assert AuditEventType.RECONCILIATION_COMPLETED.value in events


def test_position_missing_marks_unknown_requires_reconciliation(session):
    trade = _open_trade(session)
    summary = ReconciliationService(
        session, _StubBroker(None), AuditService(session)
    ).reconcile_open_trades()

    session.refresh(trade)
    assert trade.status == TradeStatus.UNKNOWN
    assert trade.close_reason == RECONCILIATION_REQUIRED_REASON
    assert trade.result is None  # never guess WIN/LOSS
    assert summary.marked_reconciliation_required == 1
    assert trade.id in summary.trade_ids_requiring_attention

    events = {e.event_type for e in session.exec(select(AuditEvent))}
    assert AuditEventType.POSITION_MISSING.value in events
    assert AuditEventType.RECONCILIATION_REQUIRED.value in events


def test_broker_error_during_reconciliation_is_treated_conservatively(session):
    trade = _open_trade(session)
    broker = _StubBroker(raise_on_get=ConnectionError("terminal unreachable"))
    summary = ReconciliationService(session, broker, AuditService(session)).reconcile_open_trades()

    session.refresh(trade)
    assert trade.status == TradeStatus.UNKNOWN
    assert trade.close_reason == RECONCILIATION_REQUIRED_REASON
    assert summary.marked_reconciliation_required == 1


def test_position_closed_with_broker_data_resolves_properly_not_as_unknown(session):
    """When the broker DOES know how it closed, use that real data instead
    of parking the trade as UNKNOWN."""
    trade = _open_trade(session)
    position = BrokerPosition(
        position_id="1",
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        lot=Decimal("0.10"),
        entry_price=Decimal("4638.50"),
        stop_loss=Decimal("4623"),
        take_profit=Decimal("4639.50"),
        is_open=False,
        close_price=Decimal("4639.50"),
        closed_at=datetime.now(UTC),
        profit=Decimal("10"),
        close_reason="TP",
    )
    summary = ReconciliationService(
        session, _StubBroker(position), AuditService(session)
    ).reconcile_open_trades()

    session.refresh(trade)
    assert trade.status == TradeStatus.CLOSED_TP
    assert trade.result == TradeResult.WIN
    assert trade.profit == Decimal("10")
    assert trade.close_reason == "TP"
    assert summary.resolved_from_broker == 1
    assert summary.marked_reconciliation_required == 0


def test_trade_with_no_broker_position_id_is_defensively_marked_unknown(session):
    trade = _open_trade(session, position_id="1")
    trade.broker_position_id = None
    session.add(trade)
    session.commit()

    summary = ReconciliationService(
        session, _StubBroker(None), AuditService(session)
    ).reconcile_open_trades()

    session.refresh(trade)
    assert trade.status == TradeStatus.UNKNOWN
    assert summary.marked_reconciliation_required == 1


def test_no_open_trades_reconciliation_is_a_clean_noop(session):
    summary = ReconciliationService(
        session, _StubBroker(None), AuditService(session)
    ).reconcile_open_trades()
    assert summary.checked == 0
    assert summary.marked_reconciliation_required == 0
    events = {e.event_type for e in session.exec(select(AuditEvent))}
    assert AuditEventType.RECONCILIATION_STARTED.value in events
    assert AuditEventType.RECONCILIATION_COMPLETED.value in events


def test_paper_broker_restart_orphan_is_caught_by_reconciliation_and_still_blocks_new_trades(
    session,
):
    """Reproduces the exact HIGH-3 scenario end to end: a real PaperBroker
    opens a position, a *fresh* PaperBroker instance (simulating the
    in-memory state loss on process restart) is reconciled against the
    existing OPEN trade, and the result must (a) never claim the trade is
    still healthy and (b) still block a new trade from opening."""
    old_broker = PaperBroker(
        starting_balance=Decimal("10000"),
        symbol="XAUUSD",
        point=Decimal("0.01"),
        digits=2,
        contract_size=Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("50"),
        volume_step=Decimal("0.01"),
        spread_points=20,
        starting_price=Decimal("4638.30"),
    )
    from app.integrations.broker.base import OpenTradeRequest

    execution = old_broker.open_buy(
        OpenTradeRequest(
            symbol="XAUUSD",
            side=Side.BUY,
            lot=Decimal("0.10"),
            stop_loss=Decimal("4623"),
            take_profit=Decimal("4639.50"),
            client_reference="restart-test",
        )
    )
    assert execution.success is True

    trade = _open_trade(session, position_id=execution.broker_position_id)

    # Simulate the restart: a brand-new, empty-memory PaperBroker.
    new_broker = PaperBroker(
        starting_balance=Decimal("10000"),
        symbol="XAUUSD",
        point=Decimal("0.01"),
        digits=2,
        contract_size=Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("50"),
        volume_step=Decimal("0.01"),
        spread_points=20,
        starting_price=Decimal("2000.00"),
    )

    summary = ReconciliationService(
        session, new_broker, AuditService(session)
    ).reconcile_open_trades()

    session.refresh(trade)
    assert trade.status == TradeStatus.UNKNOWN
    assert trade.close_reason == RECONCILIATION_REQUIRED_REASON
    assert summary.marked_reconciliation_required == 1

    # And it must still consume the max_open_trades capacity defensively.
    engine = TradingEngine(session, new_broker, AuditService(session))
    assert engine._count_open_trades() == 1  # noqa: SLF001 - intentional internal check


def test_reconciliation_runs_automatically_on_real_app_startup(session):
    """End-to-end: an OPEN trade sitting in the database before the app
    even starts must come out the other side of a real FastAPI startup
    (app/lifecycle.py's lifespan) as UNKNOWN, not silently left OPEN."""
    trade = _open_trade(session, position_id="startup-orphan-1")

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        response = client.get(f"/api/trades/{trade.id}")
        assert response.status_code == 200
        assert response.json()["status"] == "UNKNOWN"

        logs = client.get("/api/logs?limit=50").json()
        event_types = {e["event_type"] for e in logs}
        assert AuditEventType.RECONCILIATION_STARTED.value in event_types
        assert AuditEventType.RECONCILIATION_COMPLETED.value in event_types
        assert AuditEventType.POSITION_MISSING.value in event_types
