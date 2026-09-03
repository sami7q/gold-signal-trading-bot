from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.analytics.calculations import DailyPerformance, OrderPositionPerformance, ProfitCurvePoint
from app.analytics.service import AnalyticsService, DashboardSummary
from app.api.logs import AuditEventRead
from app.api.trades import TradeRead
from app.audit.service import AuditService
from app.core.auth_deps import get_current_account
from app.database.models.mt5_account import MT5Account
from app.database.models.trade import Trade
from app.database.session import get_session
from app.settings.schemas import TradingSettingsRead
from app.settings.service import SettingsService

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
def get_summary(
    session: Annotated[Session, Depends(get_session)],
    account: Annotated[MT5Account, Depends(get_current_account)],
) -> dict:
    summary: DashboardSummary = AnalyticsService(session).get_dashboard_summary(account)
    settings: TradingSettingsRead = TradingSettingsRead.model_validate(
        SettingsService(session).get_for_account(account.id)  # type: ignore[arg-type]
    )
    return {"summary": summary, "settings": settings}


@router.get("/recent-trades", response_model=list[TradeRead])
def get_recent_trades(
    session: Annotated[Session, Depends(get_session)],
    account: Annotated[MT5Account, Depends(get_current_account)],
    limit: int = 10,
):
    statement = (
        select(Trade)
        .where(Trade.account_id == account.id)
        .order_by(Trade.created_at.desc())  # type: ignore[attr-defined]
        .limit(limit)
    )
    return list(session.exec(statement))


@router.get("/recent-events", response_model=list[AuditEventRead])
def get_recent_events(
    session: Annotated[Session, Depends(get_session)],
    _account: Annotated[MT5Account, Depends(get_current_account)],
    limit: int = 20,
):
    # Shared audit trail across both trusted operators (deliberate --
    # see the approved plan) -- _account only enforces "must be logged in
    # and specify a real account you own", not a filter on the results.
    return AuditService(session).recent(limit=limit)


@router.get("/analytics/profit-curve")
def get_profit_curve(
    session: Annotated[Session, Depends(get_session)],
    account: Annotated[MT5Account, Depends(get_current_account)],
) -> list[ProfitCurvePoint]:
    return AnalyticsService(session).get_profit_curve(account.id)  # type: ignore[arg-type]


@router.get("/analytics/daily-performance")
def get_daily_performance(
    session: Annotated[Session, Depends(get_session)],
    account: Annotated[MT5Account, Depends(get_current_account)],
) -> list[DailyPerformance]:
    return AnalyticsService(session).get_daily_performance(account.id)  # type: ignore[arg-type]


@router.get("/analytics/trade-order-performance")
def get_trade_order_performance(
    session: Annotated[Session, Depends(get_session)],
    account: Annotated[MT5Account, Depends(get_current_account)],
) -> list[OrderPositionPerformance]:
    return AnalyticsService(session).get_trade_order_performance(account.id)  # type: ignore[arg-type]
