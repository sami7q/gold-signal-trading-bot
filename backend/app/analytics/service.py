from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlmodel import Session, select

from app.analytics.calculations import (
    DailyPerformance,
    OrderPositionPerformance,
    ProfitCurvePoint,
    TradeStatistics,
    compute_daily_performance,
    compute_profit_curve,
    compute_trade_order_performance,
    compute_trade_statistics,
)
from app.core.constants import MessageClassification, SignalStatus
from app.core.decimals import CleanDecimal
from app.database.models.incoming_message import IncomingMessage
from app.database.models.mt5_account import MT5Account, is_account_connected
from app.database.models.signal import Signal
from app.database.models.trade import Trade

_EXECUTABLE_SIGNAL_STATUSES = (
    SignalStatus.READY,
    SignalStatus.VALIDATING,
    SignalStatus.EXECUTION_PENDING,
    SignalStatus.EXECUTED,
)


class DashboardSummary(BaseModel):
    total_signals_received: int
    executable_signals: int
    ignored_messages: int
    rejected_signals: int
    statistics: TradeStatistics
    balance: CleanDecimal | None
    equity: CleanDecimal | None
    broker_healthy: bool


class AnalyticsService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _all_trades(self, account_id: int) -> list[Trade]:
        return list(self._session.exec(select(Trade).where(Trade.account_id == account_id)))

    def get_dashboard_summary(self, account: MT5Account) -> DashboardSummary:
        account_id = account.id
        assert account_id is not None, "account must be persisted"
        trades = self._all_trades(account_id)
        stats = compute_trade_statistics(trades, today=datetime.now(UTC).date())

        total_signals = len(list(self._session.exec(select(Signal))))
        rejected_signals = len(
            list(self._session.exec(select(Signal).where(Signal.status == SignalStatus.REJECTED)))
        )
        executable_signals = len(
            list(
                self._session.exec(
                    select(Signal).where(Signal.status.in_(_EXECUTABLE_SIGNAL_STATUSES))  # type: ignore[attr-defined]
                )
            )
        )
        ignored_messages = len(
            list(
                self._session.exec(
                    select(IncomingMessage).where(
                        IncomingMessage.classification == MessageClassification.IGNORED
                    )
                )
            )
        )

        # Read this account's *own* last-known numbers off its row instead
        # of ever querying a live broker connection here (concurrent-
        # execution "Phase 2" fix, 2026-09-03): the HTTP-serving hub
        # process only ever holds one physical broker connection, its own
        # -- calling `broker.get_account()` for whichever account is being
        # *viewed* used to silently return that same connection's numbers
        # regardless of which account they were displayed under. A
        # worker's connection lives in a separate OS process with no HTTP
        # server, so the hub could never reach it live anyway; the stored
        # fields (see MT5Account, written every heartbeat by whichever
        # process actually owns this account) are correct for every
        # account uniformly, at the cost of being at most one heartbeat
        # cycle old.
        return DashboardSummary(
            total_signals_received=total_signals,
            executable_signals=executable_signals,
            ignored_messages=ignored_messages,
            rejected_signals=rejected_signals,
            statistics=stats,
            balance=account.last_balance,
            equity=account.last_equity,
            broker_healthy=is_account_connected(account),
        )

    def get_profit_curve(self, account_id: int) -> list[ProfitCurvePoint]:
        return compute_profit_curve(self._all_trades(account_id))

    def get_daily_performance(self, account_id: int) -> list[DailyPerformance]:
        return compute_daily_performance(self._all_trades(account_id))

    def get_trade_order_performance(self, account_id: int) -> list[OrderPositionPerformance]:
        return compute_trade_order_performance(self._all_trades(account_id))
