"""End-to-end simulation scenarios (PROJECT_SPEC.md sections 17.5 and 31)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlmodel import select

from app.analytics.service import AnalyticsService
from app.core.constants import SignalStatus, TradeResult, TradeStatus
from app.database.models.trade import Trade
from app.integrations.broker.paper import PaperBroker
from app.settings.schemas import TradingSettingsUpdate
from app.settings.service import SettingsService
from app.signals.models import IncomingMessageData
from app.trading.service import TradingService


def _broker() -> PaperBroker:
    return PaperBroker(
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


def _msg(text: str, message_id: str) -> IncomingMessageData:
    now = datetime.now(UTC)
    return IncomingMessageData(
        source="mock",
        channel_id="chan-1",
        message_id=message_id,
        text=text,
        sent_at=now,
        received_at=now,
    )


def _configure(session, account_id: int) -> None:
    SettingsService(session).update(
        account_id,
        TradingSettingsUpdate(
            auto_trading=True,
            buy_enabled=True,
            lot_size=Decimal("0.10"),
            target_pips=Decimal("100"),
            max_open_trades=1,
            require_stop_loss=True,
        ),
    )


def test_full_scenario_buy_gold_then_sl_then_tp_hit_is_a_win(session, seed_account):
    broker = _broker()
    _configure(session, seed_account.id)
    service = TradingService(session, broker, account_id=seed_account.id)

    first = service.process_incoming_message(_msg("BUY GOLD", "m1"))
    assert first.signal.status == SignalStatus.WAITING_FOR_DETAILS
    assert session.exec(select(Trade)).first() is None

    second = service.process_incoming_message(_msg("4640 - 4637\nSL 4623", "m2"))
    assert second.became_ready is True
    assert second.signal.status == SignalStatus.EXECUTED

    trade = session.exec(select(Trade)).one()
    assert trade.status == TradeStatus.OPEN
    assert trade.stop_loss == Decimal("4623")
    assert trade.entry_price == broker.ask
    assert trade.take_profit_price > trade.entry_price

    closed = broker.set_price(trade.take_profit_price)
    service.on_positions_closed(closed)
    session.refresh(trade)
    assert trade.status == TradeStatus.CLOSED_TP
    assert trade.result == TradeResult.WIN

    summary = AnalyticsService(session).get_dashboard_summary(
        broker, seed_account.id, is_live=seed_account.is_live
    )
    assert summary.statistics.trades_executed == 1
    assert summary.statistics.closed_trades == 1
    assert summary.statistics.wins == 1
    assert summary.statistics.losses == 0
    assert summary.statistics.win_rate == Decimal("100")

    # Same Telegram message delivered again (identical source/channel/message id).
    duplicate = service.process_incoming_message(_msg("BUY GOLD", "m1"))
    assert duplicate.duplicate is True
    assert len(list(session.exec(select(Trade)))) == 1


def test_full_scenario_sl_hit_is_a_loss(session, seed_account):
    broker = _broker()
    _configure(session, seed_account.id)
    service = TradingService(session, broker, account_id=seed_account.id)

    service.process_incoming_message(_msg("BUY GOLD", "m1"))
    service.process_incoming_message(_msg("4640 - 4637\nSL 4623", "m2"))

    trade = session.exec(select(Trade)).one()
    closed = broker.set_price(trade.stop_loss)
    service.on_positions_closed(closed)
    session.refresh(trade)

    assert trade.status == TradeStatus.CLOSED_SL
    assert trade.result == TradeResult.LOSS

    summary = AnalyticsService(session).get_dashboard_summary(
        broker, seed_account.id, is_live=seed_account.is_live
    )
    assert summary.statistics.wins == 0
    assert summary.statistics.losses == 1
    assert summary.statistics.win_rate == Decimal("0")


def test_auto_trading_off_never_opens_a_trade(session, seed_account):
    broker = _broker()
    _configure(session, seed_account.id)
    SettingsService(session).update(seed_account.id, TradingSettingsUpdate(auto_trading=False))
    service = TradingService(session, broker, account_id=seed_account.id)

    service.process_incoming_message(_msg("BUY GOLD", "m1"))
    result = service.process_incoming_message(_msg("4640 - 4637\nSL 4623", "m2"))

    assert result.signal.status == SignalStatus.REJECTED
    trade = session.exec(select(Trade)).one()
    assert trade.status == TradeStatus.REJECTED
    assert trade.failure_reason == "AUTO_TRADING_DISABLED"
