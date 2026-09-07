"""Trading engine (PROJECT_SPEC.md section 12): Signal -> Rules -> Broker -> DB.

`TradingEngine.execute` is the only place a `Trade` row is created. It is
idempotent by construction: the `execution_key` (`signal_id:strategy_version`)
is checked before anything else, backed by a unique DB constraint as a
last-resort guard against a race.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlmodel import Session, select

from app.audit.service import AuditService
from app.core.constants import (
    OPEN_TRADE_STATUSES,
    STRATEGY_VERSION,
    AuditEventType,
    AuditSeverity,
    Side,
    SignalStatus,
    TradeStatus,
)
from app.core.decimals import fmt
from app.core.logging import get_logger
from app.database.models.mt5_account import MT5Account
from app.database.models.signal import Signal
from app.database.models.trade import Trade
from app.database.models.trading_settings import TradingSettings
from app.integrations.broker.base import Broker, BrokerHealth, OpenTradeRequest
from app.trading import pips
from app.trading.daily_lot_plan import next_trade_lot, trading_day
from app.trading.loss_recovery import next_trade_lot as next_recovery_lot
from app.trading.rules import TradingRulesEngine

logger = get_logger(__name__)

#: Trade statuses that represent a real, broker-confirmed attempt -- used
#: to decide what counts as "today's Nth trade" for the daily lot plan.
#: Excludes CREATED/SUBMITTING (transient) and REJECTED/FAILED (never
#: actually reached the market).
_COUNTS_TOWARD_DAILY_PLAN: frozenset[TradeStatus] = frozenset(
    {
        TradeStatus.OPEN,
        TradeStatus.UNKNOWN,
        TradeStatus.CLOSED_TP,
        TradeStatus.CLOSED_SL,
        TradeStatus.CLOSED_MANUAL,
    }
)


def broker_symbol_for(session: Session, account_id: int, fallback: str) -> str:
    """The name *this account's broker* knows the instrument by.

    A signal's `symbol` is the canonical `XAUUSD` the parser normalises
    every channel message to -- an internal identity, not something a
    broker is obliged to recognise. XM lists Gold as `GOLD`, and asking it
    for `XAUUSD` fails, which the rules engine can only report as
    BROKER_UNAVAILABLE (2026-09-07: every signal was rejected on the XM
    account for exactly that, while the same signal filled on Axi seconds
    earlier). `MT5Account.symbol` has always existed to carry the
    per-broker name; this is what finally reads it.

    Trades keep storing the canonical symbol -- it identifies the
    instrument, while the broker's name for it is a property of the account
    and is shown there.
    """
    account = session.get(MT5Account, account_id)
    if account is not None and account.symbol:
        return account.symbol
    return fallback


def todays_trades(session: Session, now: datetime, account_id: int) -> list[Trade]:
    """Every real (broker-confirmed) trade opened on `now`'s Asia/Baghdad
    calendar day for this account, oldest first -- see
    app/trading/daily_lot_plan.py. SQLite can't compare timezone-aware
    dates portably, so this pulls a generous UTC window (guaranteed to
    fully contain the Baghdad day) and filters precisely in Python.

    Module-level rather than a TradingEngine method so the operator audit
    script (backend/scripts/check_settings.py) can report the lot the
    engine would really pick, through this exact query, without
    constructing an engine (and therefore a broker) just to ask."""
    today = trading_day(now)
    window_start = datetime.combine(today, datetime.min.time(), tzinfo=UTC) - timedelta(hours=6)
    window_end = window_start + timedelta(hours=36)
    statement = (
        select(Trade)
        .where(Trade.status.in_(_COUNTS_TOWARD_DAILY_PLAN))  # type: ignore[attr-defined]
        .where(Trade.account_id == account_id)
        .where(Trade.created_at >= window_start)
        .where(Trade.created_at <= window_end)
        .order_by(Trade.created_at.asc())  # type: ignore[attr-defined]
    )
    candidates = list(session.exec(statement))
    return [t for t in candidates if trading_day(t.created_at) == today]


def last_trade(session: Session, account_id: int) -> Trade | None:
    """This account's single most recent real (broker-confirmed) trade --
    see app/trading/loss_recovery.py. Not calendar-day scoped, unlike
    `todays_trades` above. Ordered by `id`, not `created_at`: two trades
    can land on the exact same wall-clock microsecond (observed in tests),
    which would make a `created_at` ordering ambiguous -- the
    auto-increment primary key is guaranteed to reflect actual
    insertion/execution order instead."""
    statement = (
        select(Trade)
        .where(Trade.status.in_(_COUNTS_TOWARD_DAILY_PLAN))  # type: ignore[attr-defined]
        .where(Trade.account_id == account_id)
        .order_by(Trade.id.desc())  # type: ignore[union-attr]
    )
    return session.exec(statement).first()


class TradingEngine:
    def __init__(self, session: Session, broker: Broker, audit: AuditService | None = None) -> None:
        self._session = session
        self._broker = broker
        self._audit = audit or AuditService(session)

    @staticmethod
    def build_execution_key(account_id: int, signal_id: int) -> str:
        # Account-scoped (multi-account feature, 2026-09): each account
        # gets its own independent trade for the same shared-channel
        # signal, instead of the old signal-only key blocking every
        # account but the first from ever executing it.
        return f"{account_id}:{signal_id}:{STRATEGY_VERSION}"

    def _find_trade(self, execution_key: str) -> Trade | None:
        statement = select(Trade).where(Trade.execution_key == execution_key)
        return self._session.exec(statement).first()

    def _count_open_trades(self, account_id: int) -> int:
        # UNKNOWN (unreconciled) trades are counted too — a trade we can't
        # confirm is actually closed must still be treated as consuming risk
        # capacity. See app/trading/reconciliation.py and
        # PROJECT_SPEC.md section 32, rule 13.
        statement = select(Trade).where(
            Trade.status.in_(OPEN_TRADE_STATUSES),  # type: ignore[attr-defined]
            Trade.account_id == account_id,
        )
        return len(list(self._session.exec(statement)))

    def execute(self, signal: Signal, settings: TradingSettings) -> Trade:
        assert signal.id is not None, "signal must be persisted before execution"
        execution_key = self.build_execution_key(settings.account_id, signal.id)
        broker_symbol = broker_symbol_for(self._session, settings.account_id, signal.symbol)

        existing = self._find_trade(execution_key)
        if existing is not None:
            self._audit.log(
                AuditEventType.SIGNAL_DUPLICATE,
                f"Execution blocked: trade already exists for signal {signal.id}",
                severity=AuditSeverity.WARNING,
                metadata={"signal_id": signal.id, "trade_id": existing.id},
            )
            return existing

        try:
            broker_health: BrokerHealth | None = self._broker.health()
        except Exception:  # noqa: BLE001 - broker unavailable must never crash the pipeline
            broker_health = None

        account = None
        symbol_info = None
        quote = None
        if broker_health is not None and broker_health.healthy:
            try:
                account = self._broker.get_account()
                symbol_info = self._broker.get_symbol(broker_symbol)
                # Fetched here (not just later, right before order placement)
                # so the rules engine can screen the channel's Stop Loss
                # against a current price (rule 7b). A fresh quote is still
                # re-fetched right before order submission below -- this
                # one is only for that coarse quality check.
                quote = self._broker.get_quote(broker_symbol)
            except Exception:  # noqa: BLE001
                broker_health = BrokerHealth(healthy=False, detail="broker query failed")

        now = datetime.now(UTC)
        if settings.daily_lot_plan_enabled:
            plan = next_trade_lot(
                todays_trades(self._session, now, settings.account_id), now=now, settings=settings
            )
            effective_lot: Decimal = (
                plan.lot if plan.allowed and plan.lot is not None else settings.lot_size
            )
            daily_plan_rejection = None if plan.allowed else plan.explanation
        elif settings.loss_recovery_enabled:
            recovery = next_recovery_lot(
                last_trade(self._session, settings.account_id), settings=settings
            )
            effective_lot = recovery.lot
            daily_plan_rejection = None
        else:
            effective_lot = settings.lot_size
            daily_plan_rejection = None

        decision = TradingRulesEngine.evaluate(
            signal=signal,
            settings=settings,
            account=account,
            open_trades_count=self._count_open_trades(settings.account_id),
            broker_health=broker_health,
            symbol_info=symbol_info,
            quote=quote,
            already_executed=False,
            effective_lot=effective_lot,
            daily_plan_rejection=daily_plan_rejection,
        )

        if not decision.allowed:
            trade = Trade(
                signal_id=signal.id,
                account_id=settings.account_id,
                execution_key=execution_key,
                broker=self._broker.name,
                symbol=signal.symbol,
                side=signal.side,
                requested_lot=effective_lot,
                stop_loss=signal.channel_stop_loss or 0,
                target_pips=settings.target_pips,
                status=TradeStatus.REJECTED,
                failure_reason=decision.reason_code,
            )
            self._session.add(trade)
            signal.status = SignalStatus.REJECTED
            signal.rejection_reason = decision.reason_code
            signal.updated_at = datetime.now(UTC)
            self._session.add(signal)
            self._session.commit()
            self._session.refresh(trade)

            self._audit.log(
                AuditEventType.SIGNAL_REJECTED,
                f"Signal {signal.id} rejected: {decision.explanation}",
                severity=AuditSeverity.WARNING,
                metadata={"signal_id": signal.id, "reason": decision.reason_code},
            )
            return trade

        assert symbol_info is not None
        signal.status = SignalStatus.VALIDATING
        self._session.add(signal)
        self._session.commit()

        pending_trade: Trade | None = None
        try:
            quote = self._broker.get_quote(broker_symbol)
            # BUY fills/targets off the ask, SELL off the bid -- same mirror
            # as everywhere else this distinction shows up (broker adapters,
            # rules.py rule 7b).
            entry_reference_price = quote.ask if signal.side == Side.BUY else quote.bid
            take_profit_price = pips.compute_take_profit(
                entry_price=entry_reference_price,
                pips=settings.target_pips,
                symbol_info=symbol_info,
                side=signal.side,
            )

            pending_trade = Trade(
                signal_id=signal.id,
                account_id=settings.account_id,
                execution_key=execution_key,
                broker=self._broker.name,
                symbol=signal.symbol,
                side=signal.side,
                requested_lot=effective_lot,
                stop_loss=signal.channel_stop_loss,
                target_pips=settings.target_pips,
                take_profit_price=take_profit_price,
                status=TradeStatus.SUBMITTING,
            )
            self._session.add(pending_trade)
            signal.status = SignalStatus.EXECUTION_PENDING
            self._session.add(signal)
            self._session.commit()
            self._session.refresh(pending_trade)

            self._audit.log(
                AuditEventType.TRADE_SUBMISSION_STARTED,
                f"Submitting {signal.side.value} {signal.symbol} lot={fmt(effective_lot)} "
                f"to {self._broker.name}",
                metadata={"signal_id": signal.id, "trade_id": pending_trade.id},
            )

            request = OpenTradeRequest(
                symbol=broker_symbol,
                side=signal.side,
                lot=effective_lot,
                stop_loss=signal.channel_stop_loss,
                take_profit=take_profit_price,
                client_reference=execution_key,
            )
            execution = (
                self._broker.open_buy(request)
                if signal.side == Side.BUY
                else self._broker.open_sell(request)
            )
        except Exception as exc:  # noqa: BLE001 - a broker fault must never leave a phantom OPEN trade
            return self._fail_from_exception(
                signal, execution_key, effective_lot, settings, pending_trade, exc
            )

        assert pending_trade is not None
        trade = pending_trade

        if execution.success:
            trade.status = TradeStatus.OPEN
            trade.broker_order_id = execution.broker_order_id
            trade.broker_position_id = execution.broker_position_id
            trade.entry_price = execution.entry_price
            trade.executed_lot = execution.executed_lot
            trade.opened_at = execution.opened_at or datetime.now(UTC)
            trade.updated_at = datetime.now(UTC)
            self._session.add(trade)

            signal.status = SignalStatus.EXECUTED
            signal.updated_at = datetime.now(UTC)
            self._session.add(signal)
            self._session.commit()
            self._session.refresh(trade)

            self._audit.log(
                AuditEventType.TRADE_OPENED,
                f"Trade {trade.id} opened: {signal.side.value} {trade.symbol} "
                f"{fmt(trade.executed_lot)} @ {fmt(trade.entry_price)}",
                metadata={"signal_id": signal.id, "trade_id": trade.id},
            )
        else:
            trade.status = TradeStatus.FAILED
            trade.failure_reason = execution.error
            trade.updated_at = datetime.now(UTC)
            self._session.add(trade)

            signal.status = SignalStatus.FAILED
            signal.updated_at = datetime.now(UTC)
            self._session.add(signal)
            self._session.commit()
            self._session.refresh(trade)

            self._audit.log(
                AuditEventType.TRADE_FAILED,
                f"Broker rejected order for signal {signal.id}: {execution.error}",
                severity=AuditSeverity.ERROR,
                metadata={"signal_id": signal.id, "trade_id": trade.id, "error": execution.error},
            )

        return trade

    def _fail_from_exception(
        self,
        signal: Signal,
        execution_key: str,
        effective_lot: Decimal,
        settings: TradingSettings,
        trade: Trade | None,
        exc: Exception,
    ) -> Trade:
        """A broker call raised instead of returning a graceful rejection —
        e.g. a network timeout mid-submission. `trade` may or may not exist
        yet depending on exactly where the failure happened (quote fetch vs.
        order submission); either way this must end in a definitive FAILED
        record, never a trade left stuck in SUBMITTING/EXECUTION_PENDING
        forever, and never a fake OPEN.
        """
        logger.exception("Broker raised during order submission for signal %s", signal.id)
        # Exception text can be arbitrary (library-dependent) and is never
        # guaranteed safe to show verbatim to a dashboard user — keep the
        # user-facing reason to the exception type, and put the full text
        # only in the audit event's metadata (server-side), not the primary
        # human-readable message.
        safe_reason = f"Broker error: {type(exc).__name__}"

        if trade is None:
            trade = Trade(
                signal_id=signal.id,
                account_id=settings.account_id,
                execution_key=execution_key,
                broker=self._broker.name,
                symbol=signal.symbol,
                side=signal.side,
                requested_lot=effective_lot,
                stop_loss=signal.channel_stop_loss,
                target_pips=settings.target_pips,
            )

        trade.status = TradeStatus.FAILED
        trade.failure_reason = safe_reason
        trade.updated_at = datetime.now(UTC)
        self._session.add(trade)

        signal.status = SignalStatus.FAILED
        signal.updated_at = datetime.now(UTC)
        self._session.add(signal)
        self._session.commit()
        self._session.refresh(trade)

        self._audit.log(
            AuditEventType.BROKER_ERROR,
            f"Broker raised an exception submitting the order for signal {signal.id}: "
            f"{type(exc).__name__}",
            severity=AuditSeverity.ERROR,
            metadata={
                "signal_id": signal.id,
                "trade_id": trade.id,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc)[:500],
            },
        )
        self._audit.log(
            AuditEventType.TRADE_FAILED,
            f"Trade {trade.id} failed: {safe_reason}",
            severity=AuditSeverity.ERROR,
            metadata={"signal_id": signal.id, "trade_id": trade.id},
        )
        return trade
