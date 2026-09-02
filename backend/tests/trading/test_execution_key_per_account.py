"""Two accounts, one shared signal -> two independent trades (multi-account
feature, 2026-09). `TradingEngine.build_execution_key` went from
`signal_id:strategy_version` to `account_id:signal_id:strategy_version`
specifically so this works instead of the second account's execute() being
silently blocked as "already executed" by the first account's trade."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.audit.service import AuditService
from app.core.constants import CANONICAL_GOLD_SYMBOL, OrderType, Side, SignalStatus, TradeStatus
from app.database.models.signal import Signal
from app.database.models.trading_settings import TradingSettings
from app.integrations.broker.paper import PaperBroker
from app.trading.engine import TradingEngine


def _broker() -> PaperBroker:
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
        starting_price=Decimal("4638.30"),
    )


def _settings(account_id: int, **overrides) -> TradingSettings:
    defaults = dict(
        account_id=account_id,
        auto_trading=True,
        buy_enabled=True,
        lot_size=Decimal("0.10"),
        target_pips=Decimal("100"),
        require_stop_loss=True,
        max_open_trades=10,
        max_channel_stop_pips=None,
        daily_lot_plan_enabled=False,
    )
    defaults.update(overrides)
    return TradingSettings(**defaults)


def _shared_signal(session) -> Signal:
    signal = Signal(
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        signal_type=OrderType.MARKET,
        channel_stop_loss=Decimal("4623"),
        status=SignalStatus.READY,
        source="mock",
        channel_id="chan-1",
        first_message_at=datetime.now(UTC),
    )
    session.add(signal)
    session.commit()
    session.refresh(signal)
    return signal


def test_two_accounts_each_get_their_own_trade_from_the_same_signal(
    session, seed_user, account_factory
):
    account_a = account_factory(session, seed_user, label="A", mt5_login="900001")
    account_b = account_factory(session, seed_user, label="B", mt5_login="900002")

    signal = _shared_signal(session)
    engine = TradingEngine(session, _broker(), AuditService(session))

    trade_a = engine.execute(signal, _settings(account_a.id, lot_size=Decimal("0.10")))
    session.refresh(signal)  # execute() mutates signal.status to EXECUTED
    trade_b = engine.execute(signal, _settings(account_b.id, lot_size=Decimal("0.20")))

    assert trade_a.id != trade_b.id
    assert trade_a.account_id == account_a.id
    assert trade_b.account_id == account_b.id
    assert trade_a.status == TradeStatus.OPEN
    assert trade_b.status == TradeStatus.OPEN
    # Each account's own configured lot, not accidentally shared.
    assert trade_a.requested_lot == Decimal("0.10")
    assert trade_b.requested_lot == Decimal("0.20")
    assert trade_a.execution_key != trade_b.execution_key
    assert trade_a.execution_key == f"{account_a.id}:{signal.id}:v1"
    assert trade_b.execution_key == f"{account_b.id}:{signal.id}:v1"


def test_retrying_the_same_account_against_the_same_signal_is_still_idempotent(
    session, seed_account
):
    """The per-account key must not accidentally weaken the existing
    duplicate-execution guard *within* one account."""
    signal = _shared_signal(session)
    engine = TradingEngine(session, _broker(), AuditService(session))
    settings = _settings(seed_account.id)

    first = engine.execute(signal, settings)
    session.refresh(signal)
    second = engine.execute(signal, settings)

    assert first.id == second.id


def test_one_account_s_open_trade_count_never_includes_another_account_s_trades(
    session, seed_user, account_factory
):
    account_a = account_factory(session, seed_user, label="A", mt5_login="900003")
    account_b = account_factory(session, seed_user, label="B", mt5_login="900004")

    engine = TradingEngine(session, _broker(), AuditService(session))
    engine.execute(_shared_signal(session), _settings(account_a.id, max_open_trades=1))

    # account_b has zero open trades of its own -- must not see account_a's.
    assert engine._count_open_trades(account_a.id) == 1  # noqa: SLF001
    assert engine._count_open_trades(account_b.id) == 0  # noqa: SLF001
