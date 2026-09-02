"""Auto Stop Loss fallback (TradingSettings.auto_sl_fallback_pips):
`TradingService.sweep_expired_signals` -- a signal that never got a channel
Stop Loss before its completion timeout either gets a computed fallback
Stop Loss and executes, or expires unexecuted exactly as before, depending
on whether the fallback is configured. See app/trading/service.py."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlmodel import select

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
from app.integrations.broker.base import BrokerHealth
from app.integrations.broker.paper import PaperBroker
from app.settings.schemas import TradingSettingsUpdate
from app.settings.service import SettingsService
from app.trading.service import TradingService


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


def _configure(session, account_id: int, **overrides) -> None:
    defaults = dict(
        auto_trading=True,
        buy_enabled=True,
        lot_size=Decimal("0.10"),
        target_pips=Decimal("100"),
        require_stop_loss=True,
        max_open_trades=10,
        max_channel_stop_pips=None,  # not what this test file is about
        signal_completion_timeout_seconds=120,
    )
    defaults.update(overrides)
    SettingsService(session).update(account_id, TradingSettingsUpdate(**defaults))


def _stale_buy_signal(session, *, sent_at: datetime) -> Signal:
    signal = Signal(
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        signal_type=OrderType.MARKET,
        channel_stop_loss=None,
        status=SignalStatus.WAITING_FOR_DETAILS,
        source="mock",
        channel_id="chan-1",
        first_message_at=sent_at,
        expires_at=sent_at + timedelta(seconds=120),
    )
    session.add(signal)
    session.commit()
    session.refresh(signal)
    return signal


def test_fallback_applies_computed_stop_loss_and_executes(session, seed_account):
    _configure(session, seed_account.id, auto_sl_fallback_pips=Decimal("150"))
    long_ago = datetime.now(UTC) - timedelta(seconds=200)
    signal = _stale_buy_signal(session, sent_at=long_ago)

    service = TradingService(session, _broker(), account_id=seed_account.id)
    processed = service.sweep_expired_signals()

    assert len(processed) == 1
    session.refresh(signal)
    assert signal.status == SignalStatus.EXECUTED
    # PaperBroker's ask = bid (4638.30) + spread (20 points * 0.01 = 0.20)
    # = 4638.50; pip_size for 2-digit gold is point*10 = 0.10, so 150 pips
    # = 15.00 behind that: 4638.50 - 15.00 = 4623.50.
    assert signal.channel_stop_loss == Decimal("4623.50")

    trade = session.exec(select(Trade).where(Trade.signal_id == signal.id)).first()
    assert trade is not None
    assert trade.status == TradeStatus.OPEN
    assert trade.stop_loss == Decimal("4623.50")


def test_fallback_disabled_expires_the_signal_instead(session, seed_account):
    _configure(session, seed_account.id, auto_sl_fallback_pips=None)
    long_ago = datetime.now(UTC) - timedelta(seconds=200)
    signal = _stale_buy_signal(session, sent_at=long_ago)

    service = TradingService(session, _broker(), account_id=seed_account.id)
    processed = service.sweep_expired_signals()

    assert len(processed) == 1
    session.refresh(signal)
    assert signal.status == SignalStatus.EXPIRED
    assert signal.channel_stop_loss is None

    trades = list(session.exec(select(Trade).where(Trade.signal_id == signal.id)))
    assert trades == []


def test_signal_not_yet_stale_is_left_alone(session, seed_account):
    _configure(session, seed_account.id, auto_sl_fallback_pips=Decimal("150"))
    just_now = datetime.now(UTC)
    signal = _stale_buy_signal(session, sent_at=just_now)

    service = TradingService(session, _broker(), account_id=seed_account.id)
    processed = service.sweep_expired_signals()

    assert processed == []
    session.refresh(signal)
    assert signal.status == SignalStatus.WAITING_FOR_DETAILS
    assert signal.channel_stop_loss is None


def test_fallback_audit_event_records_the_computed_price(session, seed_account):
    _configure(session, seed_account.id, auto_sl_fallback_pips=Decimal("150"))
    long_ago = datetime.now(UTC) - timedelta(seconds=200)
    signal = _stale_buy_signal(session, sent_at=long_ago)

    service = TradingService(session, _broker(), account_id=seed_account.id)
    service.sweep_expired_signals()

    events = list(
        session.exec(
            select(AuditEvent).where(
                AuditEvent.event_type == AuditEventType.AUTO_STOP_LOSS_APPLIED.value
            )
        )
    )
    assert len(events) == 1
    assert str(signal.id) in events[0].metadata_json
    assert "4623.50" in events[0].metadata_json


def test_fallback_falls_back_to_expiry_when_broker_is_unavailable(session, seed_account):
    _configure(session, seed_account.id, auto_sl_fallback_pips=Decimal("150"))
    long_ago = datetime.now(UTC) - timedelta(seconds=200)
    signal = _stale_buy_signal(session, sent_at=long_ago)

    broker = _broker()
    broker.health = lambda: BrokerHealth(healthy=False, detail="disconnected")  # type: ignore[method-assign]

    service = TradingService(session, broker, account_id=seed_account.id)
    processed = service.sweep_expired_signals()

    assert len(processed) == 1
    session.refresh(signal)
    assert signal.status == SignalStatus.EXPIRED

    events = list(
        session.exec(
            select(AuditEvent).where(AuditEvent.event_type == AuditEventType.BROKER_ERROR.value)
        )
    )
    assert len(events) == 1


def test_observe_only_fallback_reaches_ready_but_never_executes(session, seed_account):
    """A real-source ("telegram") signal must reach READY via the fallback
    exactly like the immediate-SL path does, but never be sent to the
    trading engine while TELEGRAM_OBSERVE_ONLY=true. Force the env var
    explicitly (with cache_clear) rather than relying on conftest.py's
    process-wide default -- `get_settings()` is an `lru_cache` shared across
    the whole test session, and another test clearing/popping it first
    would otherwise make this test's outcome depend on run order."""
    from app.core.config import get_settings

    os.environ["TELEGRAM_OBSERVE_ONLY"] = "true"
    get_settings.cache_clear()
    try:
        _configure(session, seed_account.id, auto_sl_fallback_pips=Decimal("150"))

        long_ago = datetime.now(UTC) - timedelta(seconds=200)
        signal = Signal(
            symbol=CANONICAL_GOLD_SYMBOL,
            side=Side.BUY,
            signal_type=OrderType.MARKET,
            channel_stop_loss=None,
            status=SignalStatus.WAITING_FOR_DETAILS,
            source="telegram",
            channel_id="chan-1",
            first_message_at=long_ago,
            expires_at=long_ago + timedelta(seconds=120),
        )
        session.add(signal)
        session.commit()
        session.refresh(signal)

        service = TradingService(session, _broker(), account_id=seed_account.id)
        service.sweep_expired_signals()

        session.refresh(signal)
        assert signal.status == SignalStatus.READY
        assert signal.channel_stop_loss is not None
        trades = list(session.exec(select(Trade).where(Trade.signal_id == signal.id)))
        assert trades == []

        events = {e.event_type for e in session.exec(select(AuditEvent))}
        assert AuditEventType.SIGNAL_OBSERVED_ONLY.value in events
    finally:
        # Restore conftest.py's documented process-wide default so later
        # tests aren't left depending on this test having run.
        os.environ["TELEGRAM_OBSERVE_ONLY"] = "true"
        get_settings.cache_clear()
