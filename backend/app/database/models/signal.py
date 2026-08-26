from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlmodel import Field

from app.core.constants import OrderType, Side, SignalStatus
from app.database.base import Base
from app.database.types import UTCDateTime


class Signal(Base, table=True):
    __tablename__ = "signals"

    id: int | None = Field(default=None, primary_key=True)

    symbol: str = Field(index=True)
    side: Side
    signal_type: OrderType

    entry_min: Decimal | None = None
    entry_max: Decimal | None = None
    channel_stop_loss: Decimal | None = None

    status: SignalStatus = Field(default=SignalStatus.RECEIVED, index=True)

    source: str = Field(index=True)
    channel_id: str = Field(index=True)

    first_message_at: datetime = Field(sa_type=UTCDateTime)
    completed_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    expires_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    rejection_reason: str | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
