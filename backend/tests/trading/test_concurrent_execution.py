"""Concurrent multi-account real execution ("Phase 2", 2026-09) -- the
specific bug this update fixed, and the isolation guarantees the design
depends on. See the approved plan for the full architecture."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.accounts.service import AccountService
from app.analytics.service import AnalyticsService
from app.audit.service import AuditService
from app.core.constants import CANONICAL_GOLD_SYMBOL, OrderType, Side, SignalStatus, TradeStatus
from app.database.models.signal import Signal
from app.database.models.trade import Trade
from app.integrations.broker.paper import PaperBroker
from app.settings.schemas import TradingSettingsUpdate
from app.settings.service import SettingsService
from app.trading.reconciliation import ReconciliationService
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
        max_channel_stop_pips=None,
        daily_lot_plan_enabled=False,
    )
    defaults.update(overrides)
    SettingsService(session).update(account_id, TradingSettingsUpdate(**defaults))


def _shared_signal(session, *, status: SignalStatus = SignalStatus.READY) -> Signal:
    signal = Signal(
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        signal_type=OrderType.MARKET,
        channel_stop_loss=Decimal("4623") if status != SignalStatus.WAITING_FOR_DETAILS else None,
        status=status,
        source="mock",
        channel_id="chan-1",
        first_message_at=datetime.now(UTC),
    )
    session.add(signal)
    session.commit()
    session.refresh(signal)
    return signal


def test_two_accounts_each_execute_the_same_signal_via_execute_ready_signals(
    session, seed_user, account_factory
):
    """The critical bug this update fixed: before, a worker polling
    `Signal.status == READY` would never see a signal a *different*
    account already executed, because TradingEngine.execute() flips that
    shared column away from READY the instant *anyone* executes it.
    `execute_ready_signals()` (backed by `find_unexecuted_for_account`,
    which watches per-account `Trade` rows instead) must let every account
    execute its own trade regardless of execution order."""
    account_a = account_factory(session, seed_user, label="A", mt5_login="800001")
    account_b = account_factory(session, seed_user, label="B", mt5_login="800002")
    _configure(session, account_a.id, lot_size=Decimal("0.10"))
    _configure(session, account_b.id, lot_size=Decimal("0.20"))

    signal = _shared_signal(session)
    broker = _broker()

    service_a = TradingService(session, broker, account_id=account_a.id)
    trades_a = service_a.execute_ready_signals()
    assert len(trades_a) == 1
    assert trades_a[0].account_id == account_a.id
    assert trades_a[0].status == TradeStatus.OPEN
    assert trades_a[0].requested_lot == Decimal("0.10")

    # The shared Signal.status has now flipped away from READY -- exactly
    # the state that used to make account B's process blind to it forever.
    session.refresh(signal)
    assert signal.status == SignalStatus.EXECUTED

    service_b = TradingService(session, broker, account_id=account_b.id)
    trades_b = service_b.execute_ready_signals()
    assert len(trades_b) == 1
    assert trades_b[0].account_id == account_b.id
    assert trades_b[0].status == TradeStatus.OPEN
    assert trades_b[0].requested_lot == Decimal("0.20")

    # Idempotent: a second poll tick for either account is a no-op.
    assert service_a.execute_ready_signals() == []
    assert service_b.execute_ready_signals() == []


def test_execute_ready_signals_logs_when_a_telegram_signal_is_observed_only(
    session, seed_account
):
    """The real, silent gap found live 2026-09-03: a worker whose own
    .env is missing TELEGRAM_OBSERVE_ONLY=false inherits the safe-by-
    default True, and every real Telegram signal was skipped with zero
    trace anywhere -- no error, no Trade row, nothing to grep for. This
    is the audit entry that closes that gap, mirroring what
    process_incoming_message already logged for the synchronous path."""
    _configure(session, seed_account.id)
    signal = Signal(
        symbol=CANONICAL_GOLD_SYMBOL,
        side=Side.BUY,
        signal_type=OrderType.MARKET,
        channel_stop_loss=Decimal("4623"),
        status=SignalStatus.READY,
        source="telegram",  # conftest.py forces TELEGRAM_OBSERVE_ONLY=true
        channel_id="chan-1",
        first_message_at=datetime.now(UTC),
    )
    session.add(signal)
    session.commit()

    service = TradingService(session, _broker(), account_id=seed_account.id)
    assert service.execute_ready_signals() == []  # observed only -- never reaches the engine

    events = AuditService(session).recent(limit=10)
    assert any(
        e.event_type == "SIGNAL_OBSERVED_ONLY" and str(signal.id) in e.message for e in events
    )


def test_execute_ready_signals_ignores_signals_still_waiting_for_details(session, seed_account):
    """Never tries to "execute" a signal the state machine hasn't decided
    yet (WAITING_FOR_DETAILS etc.) -- that's not a duplicate-vs-real trade
    question, it simply isn't ready for any account."""
    _shared_signal(session, status=SignalStatus.WAITING_FOR_DETAILS)
    _configure(session, seed_account.id)

    service = TradingService(session, _broker(), account_id=seed_account.id)
    assert service.execute_ready_signals() == []


def test_execute_ready_signals_is_a_noop_with_no_resolved_account(session, seed_account):
    """`account_id=None` (no MT5Account matches this process's own .env --
    see app/lifecycle.py) must never raise or execute anything."""
    _shared_signal(session)
    _configure(session, seed_account.id)

    service = TradingService(session, _broker(), account_id=None)
    assert service.execute_ready_signals() == []


def _open_trade(session, account_id: int, position_id: str) -> Trade:
    signal = _shared_signal(session, status=SignalStatus.EXECUTED)
    trade = Trade(
        signal_id=signal.id,
        account_id=account_id,
        execution_key=f"{account_id}:{signal.id}:v1",
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


class _NeverFindsAnything:
    """Stands in for one account's real broker connection -- it genuinely
    has no idea about a *different* account's position, on principle (a
    different broker/terminal entirely in the real concurrent-worker
    topology)."""

    name = "stub"

    def get_position(self, position_id: str):  # noqa: ANN001
        return None


def test_reconciliation_is_isolated_per_account_under_concurrent_workers(
    session, seed_user, account_factory
):
    """Two accounts, each with its own OPEN trade -- reconciling one
    account's trades must never touch the other's, and the other account's
    genuinely-open trade must never be misclassified just because it
    wasn't in scope for this call."""
    account_a = account_factory(session, seed_user, label="A", mt5_login="800003")
    account_b = account_factory(session, seed_user, label="B", mt5_login="800004")

    trade_a = _open_trade(session, account_a.id, "pos-a")
    trade_b = _open_trade(session, account_b.id, "pos-b")

    ReconciliationService(
        session, _NeverFindsAnything(), AuditService(session)
    ).reconcile_open_trades(account_a.id)

    session.refresh(trade_a)
    session.refresh(trade_b)
    assert trade_a.status == TradeStatus.UNKNOWN  # genuinely unresolved for A's own broker
    assert trade_b.status == TradeStatus.OPEN  # untouched -- out of scope for this call


def test_record_heartbeat_stores_this_accounts_own_balance_and_equity(
    session, seed_user, account_factory
):
    """The real bug fixed 2026-09-03: `/api/dashboard/summary` used to call
    `broker.get_account()` on the *hub's own* single broker connection
    regardless of which account was being viewed, so every account with a
    fresh heartbeat showed the hub's own numbers. `record_heartbeat` is now
    where each process's own broker numbers land, per account."""
    account = account_factory(session, seed_user, label="A", mt5_login="800005")
    AccountService(session, AuditService(session)).record_heartbeat(
        account.id, balance=Decimal("4321.00"), equity=Decimal("4300.50")
    )
    session.refresh(account)
    assert account.last_balance == Decimal("4321.00")
    assert account.last_equity == Decimal("4300.50")


def test_record_heartbeat_with_no_balance_keeps_the_last_known_value(
    session, seed_user, account_factory
):
    """A transient broker-read failure during one poll tick (the caller
    passes `balance=None` defensively -- see app/lifecycle.py /
    backend/scripts/worker.py) must never zero out or blank a previously
    good number -- only a fresh real read ever overwrites it."""
    account = account_factory(session, seed_user, label="A", mt5_login="800006")
    service = AccountService(session, AuditService(session))
    service.record_heartbeat(account.id, balance=Decimal("100"), equity=Decimal("99"))
    service.record_heartbeat(account.id)  # heartbeat tick, broker read failed this time
    session.refresh(account)
    assert account.last_balance == Decimal("100")
    assert account.last_equity == Decimal("99")


def test_dashboard_summary_never_leaks_one_accounts_broker_numbers_into_anothers(
    session, seed_user, account_factory
):
    """Two accounts, each with its own heartbeat-recorded balance/equity --
    each account's dashboard summary must show *its own* numbers, never
    the other's, regardless of which one happens to be the calling
    process's own physical broker connection."""
    account_a = account_factory(session, seed_user, label="A", mt5_login="800007")
    account_b = account_factory(session, seed_user, label="B", mt5_login="800008")
    audit = AuditService(session)
    AccountService(session, audit).record_heartbeat(
        account_a.id, balance=Decimal("111.11"), equity=Decimal("111.00")
    )
    AccountService(session, audit).record_heartbeat(
        account_b.id, balance=Decimal("222.22"), equity=Decimal("222.00")
    )
    session.refresh(account_a)
    session.refresh(account_b)

    summary_a = AnalyticsService(session).get_dashboard_summary(account_a)
    summary_b = AnalyticsService(session).get_dashboard_summary(account_b)

    assert summary_a.balance == Decimal("111.11")
    assert summary_a.equity == Decimal("111.00")
    assert summary_a.broker_healthy is True
    assert summary_b.balance == Decimal("222.22")
    assert summary_b.equity == Decimal("222.00")
    assert summary_b.broker_healthy is True


def test_dashboard_summary_shows_no_balance_for_an_account_with_no_heartbeat_yet(
    session, seed_user, account_factory
):
    """A genuinely disconnected account (no process has ever resolved to
    it, or its heartbeat has gone stale) must show no balance at all --
    never a stale or borrowed number."""
    account = account_factory(session, seed_user, label="A", mt5_login="800009", connected=False)
    summary = AnalyticsService(session).get_dashboard_summary(account)
    assert summary.balance is None
    assert summary.equity is None
    assert summary.broker_healthy is False
