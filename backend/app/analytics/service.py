from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlmodel import Session, select

from app.analytics.calculations import (
    DailyPerformance,
    ProfitCurvePoint,
    TradeStatistics,
    compute_daily_performance,
    compute_profit_curve,
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

    def _all_trades(self) -> list[Trade]:
        return list(self._session.exec(select(Trade)))

    def get_dashboard_summary(self, broker: Broker) -> DashboardSummary:
        trades = self._all_trades()
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

        try:
            health = broker.health()
            account = broker.get_account() if health.healthy else None
        except Exception:  # noqa: BLE001
            health = None
            account = None

        return DashboardSummary(
            total_signals_received=total_signals,
            executable_signals=executable_signals,
            ignored_messages=ignored_messages,
            rejected_signals=rejected_signals,
            statistics=stats,
            balance=account.balance if account else None,
            equity=account.equity if account else None,
            broker_healthy=bool(health and health.healthy),
        )

    def get_profit_curve(self) -> list[ProfitCurvePoint]:
        return compute_profit_curve(self._all_trades())

    def get_daily_performance(self) -> list[DailyPerformance]:
        return compute_daily_performance(self._all_trades())
