"""Rules engine cases from PROJECT_SPEC.md section 17.3."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.core.constants import CANONICAL_GOLD_SYMBOL, OrderType, Side, SignalStatus
from app.database.models.signal import Signal
from app.database.models.trading_settings import TradingSettings
from app.integrations.broker.base import BrokerAccount, BrokerHealth, SymbolInfo
from app.trading.rules import TradingRulesEngine


def _signal(**overrides) -> Signal:
    defaults = dict(
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        signal_type=OrderType.MARKET,
        channel_stop_loss=Decimal("4623"),
        status=SignalStatus.READY,
        source="mock",
        channel_id="chan-1",
        first_message_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Signal(**defaults)


def _settings(**overrides) -> TradingSettings:
    defaults = dict(
        auto_trading=True,
        buy_enabled=True,
        lot_size=Decimal("0.10"),
        target_pips=Decimal("100"),
        require_stop_loss=True,
        max_open_trades=1,
    )
    defaults.update(overrides)
    return TradingSettings(**defaults)


def _symbol_info() -> SymbolInfo:
    return SymbolInfo(
        symbol=CANONICAL_GOLD_SYMBOL,
        digits=2,
        point=Decimal("0.01"),
        contract_size=Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("50"),
        volume_step=Decimal("0.01"),
    )


_ACCOUNT = BrokerAccount(balance=Decimal("10000"), equity=Decimal("10000"))
_HEALTHY = BrokerHealth(healthy=True)
_UNHEALTHY = BrokerHealth(healthy=False, detail="disconnected")


def _evaluate(**overrides):
    kwargs = dict(
        signal=_signal(),
        settings=_settings(),
        account=_ACCOUNT,
        open_trades_count=0,
        broker_health=_HEALTHY,
        symbol_info=_symbol_info(),
        already_executed=False,
    )
    kwargs.update(overrides)
    return TradingRulesEngine.evaluate(**kwargs)


def test_auto_trading_off_is_rejected():
    decision = _evaluate(settings=_settings(auto_trading=False))
    assert decision.allowed is False
    assert decision.reason_code == "AUTO_TRADING_DISABLED"


def test_buy_disabled_is_rejected():
    decision = _evaluate(settings=_settings(buy_enabled=False))
    assert decision.allowed is False
    assert decision.reason_code == "BUY_DISABLED"


def test_missing_stop_loss_is_rejected():
    decision = _evaluate(signal=_signal(channel_stop_loss=None))
    assert decision.allowed is False
    assert decision.reason_code == "STOP_LOSS_REQUIRED"


def test_invalid_lot_is_rejected():
    decision = _evaluate(settings=_settings(lot_size=Decimal("0.017")))
    assert decision.allowed is False
    assert decision.reason_code == "INVALID_LOT_SIZE"


def test_max_open_trades_reached_is_rejected():
    decision = _evaluate(open_trades_count=1, settings=_settings(max_open_trades=1))
    assert decision.allowed is False
    assert decision.reason_code == "MAX_OPEN_TRADES_REACHED"


def test_broker_unhealthy_is_rejected():
    decision = _evaluate(broker_health=_UNHEALTHY)
    assert decision.allowed is False
    assert decision.reason_code == "BROKER_UNAVAILABLE"


def test_valid_buy_with_sl_is_allowed():
    decision = _evaluate()
    assert decision.allowed is True
    assert decision.reason_code is None


def test_non_buy_kind_is_unsupported_in_v1():
    decision = _evaluate(signal=_signal(side=Side.SELL))
    assert decision.allowed is False
    assert decision.reason_code == "UNSUPPORTED_SIGNAL_TYPE"


def test_expired_signal_is_rejected():
    decision = _evaluate(signal=_signal(status=SignalStatus.EXPIRED))
    assert decision.allowed is False
    assert decision.reason_code == "SIGNAL_EXPIRED"


def test_missing_stop_loss_is_allowed_when_not_required():
    decision = _evaluate(
        signal=_signal(channel_stop_loss=None), settings=_settings(require_stop_loss=False)
    )
    assert decision.allowed is True
