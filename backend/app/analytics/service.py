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
from app.database.models.signal import Signal
from app.database.models.trade import Trade
from app.integrations.broker.base import Broker

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

    def get_dashboard_summary(
        self, broker: Broker, account_id: int, *, is_live: bool
    ) -> DashboardSummary:
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

        # Phase 1 has exactly one physical broker connection, bound to
        # whichever MT5Account is currently is_live -- showing that
        # connection's real balance/equity while viewing a *different*
        # (not-live) account would misleadingly imply it has its own live
        # connection, which it doesn't yet (Phase 2).
        broker_account = None
        health = None
        if is_live:
            try:
                health = broker.health()
                broker_account = broker.get_account() if health.healthy else None
            except Exception:  # noqa: BLE001
                health = None
                broker_account = None

        return DashboardSummary(
            total_signals_received=total_signals,
            executable_signals=executable_signals,
            ignored_messages=ignored_messages,
            rejected_signals=rejected_signals,
            statistics=stats,
            balance=broker_account.balance if broker_account else None,
            equity=broker_account.equity if broker_account else None,
            broker_healthy=bool(health and health.healthy),
        )

    def get_profit_curve(self, account_id: int) -> list[ProfitCurvePoint]:
        return compute_profit_curve(self._all_trades(account_id))

    def get_daily_performance(self, account_id: int) -> list[DailyPerformance]:
        return compute_daily_performance(self._all_trades(account_id))

    def get_trade_order_performance(self, account_id: int) -> list[OrderPositionPerformance]:
        return compute_trade_order_performance(self._all_trades(account_id))
