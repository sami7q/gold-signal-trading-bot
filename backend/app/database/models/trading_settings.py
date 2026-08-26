from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlmodel import Field

from app.core.constants import TradingMode
from app.database.base import Base
from app.database.types import UTCDateTime


class TradingSettings(Base, table=True):
    """Singleton-ish settings row.

    The service layer always operates on the most recently updated row; a
    fresh default row is created on first startup if none exists (see
    `app/settings/service.py`).
    """

    __tablename__ = "trading_settings"

    id: int | None = Field(default=None, primary_key=True)

    auto_trading: bool = False
    mode: TradingMode = TradingMode.SIMULATION

    buy_enabled: bool = True
    sell_enabled: bool = False

    lot_size: Decimal = Decimal("0.10")
    target_pips: Decimal = Decimal("100")
    require_stop_loss: bool = True
    max_open_trades: int = 1

    signal_completion_timeout_seconds: int = 120

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
