from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.analytics.calculations import DailyPerformance, OrderPositionPerformance, ProfitCurvePoint
from app.analytics.service import AnalyticsService, DashboardSummary
from app.api.logs import AuditEventRead
from app.api.trades import TradeRead
from app.audit.service import AuditService
from app.core.runtime import get_broker
from app.database.models.trade import Trade
from app.database.session import get_session
from app.integrations.broker.base import Broker
from app.settings.schemas import TradingSettingsRead
from app.settings.service import SettingsService

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def get_summary(
    session: Annotated[Session, Depends(get_session)],
    broker: Annotated[Broker, Depends(get_broker)],
) -> dict:
    summary: DashboardSummary = AnalyticsService(session).get_dashboard_summary(broker)
    settings: TradingSettingsRead = TradingSettingsRead.model_validate(
        SettingsService(session).get_active()
    )
    return {"summary": summary, "settings": settings}


@router.get("/recent-trades", response_model=list[TradeRead])
def get_recent_trades(session: Annotated[Session, Depends(get_session)], limit: int = 10):
    statement = (
        select(Trade).order_by(Trade.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
    )
    return list(session.exec(statement))


@router.get("/recent-events", response_model=list[AuditEventRead])
def get_recent_events(session: Annotated[Session, Depends(get_session)], limit: int = 20):
    return AuditService(session).recent(limit=limit)


@router.get("/analytics/profit-curve")
def get_profit_curve(session: Annotated[Session, Depends(get_session)]) -> list[ProfitCurvePoint]:
    return AnalyticsService(session).get_profit_curve()


@router.get("/analytics/daily-performance")
def get_daily_performance(
    session: Annotated[Session, Depends(get_session)],
) -> list[DailyPerformance]:
    return AnalyticsService(session).get_daily_performance()


@router.get("/analytics/trade-order-performance")
def get_trade_order_performance(
    session: Annotated[Session, Depends(get_session)],
) -> list[OrderPositionPerformance]:
    return AnalyticsService(session).get_trade_order_performance()
