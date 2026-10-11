"""Plain data passed between the pipeline stages. Prices and sizes are always `Decimal`.

Sides are ints everywhere inside the bot: +1 = long, -1 = short.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from loco_copy.constants import EventType


@dataclass(frozen=True, slots=True)
class LeaderFill:
    coin: str
    side: Literal["B", "A"]  # B buy, A sell
    px: Decimal
    sz: Decimal
    start_position: Decimal  # signed size before this fill
    time_ms: int
    tid: int  # dedupe key
    oid: int | None
    received_at_ms: int

    @property
    def signed_sz(self) -> Decimal:
        return self.sz if self.side == "B" else -self.sz

    @property
    def new_position(self) -> Decimal:
        return self.start_position + self.signed_sz


@dataclass(frozen=True, slots=True)
class LeaderSnapshot:
    positions: dict[str, Decimal]  # coin -> signed size (main-dex perps only)
    entry_px: dict[str, Decimal]
    account_value: Decimal
    #: When the request was *sent* (local clock) -- fills received after this are not yet reflected.
    taken_at_ms: int


@dataclass(frozen=True, slots=True)
class LeaderEvent:
    type: EventType
    coin: str
    episode_id: int
    side: int  # side of the episode this event belongs to
    leader_px: Decimal
    leader_time_ms: int
    detected_at_ms: int
    tid: int
    fraction: Decimal | None = None  # REDUCE only: share of the leader's position that was closed


@dataclass(frozen=True, slots=True)
class DriftInfo:
    coin: str
    tracker_position: Decimal
    snapshot_position: Decimal


@dataclass(frozen=True, slots=True)
class InstrumentInfo:
    symbol: str
    qty_step: Decimal
    min_qty: Decimal
    tick_size: Decimal
    max_leverage: int
    min_notional: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class Account:
    equity: Decimal
    available: Decimal


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    side: int
    qty: Decimal
    entry_price: Decimal
    take_profit: Decimal | None = None


@dataclass(frozen=True, slots=True)
class OrderResult:
    success: bool
    client_id: str
    order_id: str | None = None
    filled_qty: Decimal = Decimal(0)
    avg_price: Decimal | None = None
    fee: Decimal = Decimal(0)
    error: str | None = None
    filled_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    """One exchange-side close, used by the reconciler to tell TP / liquidation / manual apart."""

    symbol: str
    side: int  # side of the position that was closed
    qty: Decimal
    avg_exit_price: Decimal
    realized_pnl: Decimal
    closed_at_ms: int
    is_liquidation: bool
    #: Opening fee already inside `realized_pnl` (Bybit's closedPnl nets both fees; the paper broker's does not).
    open_fee: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True)
class Health:
    healthy: bool
    detail: str = ""


@dataclass(slots=True)
class Copy:
    """One row of the `copies` table."""

    coin: str
    symbol: str
    episode_id: int
    side: int
    status: str
    qty_initial: Decimal
    qty_open: Decimal
    leader_entry_px: Decimal
    id: int | None = None
    entry_price: Decimal | None = None
    tp_price: Decimal | None = None
    exit_price_avg: Decimal | None = None
    realized_pnl: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    funding: Decimal = Decimal(0)
    margin: Decimal | None = None
    opened_at: int | None = None
    closed_at: int | None = None
    close_reason: str | None = None
    slippage_bps: Decimal | None = None
    latency_detect_ms: int | None = None
    latency_order_ms: int | None = None
    exit_qty_total: Decimal = field(default=Decimal(0))
    #: Lane label of the leader this copies (botup.md v3).
    leader: str = "loco"
