from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.constants import Side, TradeResult, TradeStatus
from app.core.decimals import CleanDecimal
from app.database.models.trade import Trade
from app.database.session import get_session

router = APIRouter(prefix="/api/trades", tags=["trades"])


class TradeRead(BaseModel):
    id: int
    signal_id: int
    execution_key: str
    broker: str
    broker_order_id: str | None
    broker_position_id: str | None
    symbol: str
    side: Side
    requested_lot: CleanDecimal
    executed_lot: CleanDecimal | None
    entry_price: CleanDecimal | None
    stop_loss: CleanDecimal | None
    target_pips: CleanDecimal
    take_profit_price: CleanDecimal | None
    opened_at: datetime | None
    closed_at: datetime | None
    exit_price: CleanDecimal | None
    profit: CleanDecimal | None
    profit_currency: str
    status: TradeStatus
    result: TradeResult | None
    close_reason: str | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[TradeRead])
def list_trades(
    session: Annotated[Session, Depends(get_session)],
    status_filter: Annotated[TradeStatus | None, Query(alias="status")] = None,
    result_filter: Annotated[TradeResult | None, Query(alias="result")] = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = 200,
) -> list[Trade]:
    statement = select(Trade)
    if status_filter is not None:
        statement = statement.where(Trade.status == status_filter)
    if result_filter is not None:
        statement = statement.where(Trade.result == result_filter)
    if date_from is not None:
        statement = statement.where(Trade.created_at >= date_from)
    if date_to is not None:
        statement = statement.where(Trade.created_at <= date_to)
    statement = statement.order_by(Trade.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
    return list(session.exec(statement))


@router.get("/{trade_id}", response_model=TradeRead)
def get_trade(trade_id: int, session: Annotated[Session, Depends(get_session)]) -> Trade:
    trade = session.get(Trade, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade
