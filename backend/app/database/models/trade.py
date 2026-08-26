from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from app.core.constants import Side, TradeResult, TradeStatus
from app.database.base import Base
from app.database.types import UTCDateTime


class Trade(Base, table=True):
    __tablename__ = "trades"
    __table_args__ = (UniqueConstraint("execution_key", name="uq_trade_execution_key"),)

    id: int | None = Field(default=None, primary_key=True)
    signal_id: int = Field(foreign_key="signals.id", index=True)

    #: `signal_id + strategy_version` — the idempotency guard (see PROJECT_SPEC.md section 8).
    execution_key: str = Field(index=True)

    broker: str
    broker_order_id: str | None = None
    broker_position_id: str | None = None

    symbol: str
    side: Side

    requested_lot: Decimal
    executed_lot: Decimal | None = None

    entry_price: Decimal | None = None
    #: Optional only because `require_stop_loss` can be turned off on the
    #: dashboard (PROJECT_SPEC.md section 2.2/2.3) — when it's on (the
    #: default), the rules engine rejects any signal missing one before a
    #: Trade row with a null stop_loss is ever created.
    stop_loss: Decimal | None = None
    target_pips: Decimal
    take_profit_price: Decimal | None = None

    opened_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    closed_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    exit_price: Decimal | None = None

    profit: Decimal | None = None
    profit_currency: str = "USD"

    status: TradeStatus = Field(default=TradeStatus.CREATED, index=True)
    result: TradeResult | None = Field(default=None, index=True)
    close_reason: str | None = None
    failure_reason: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
