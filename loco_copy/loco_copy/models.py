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
    #: The leader's signed position before / after the fill(s) behind this event (cap20 sizes adds from them).
    pos_before: Decimal | None = None
    pos_after: Decimal | None = None

    @property
    def qty_delta(self) -> Decimal:
        """How much the leader's position grew in absolute size (ADD / OPEN); 0 if unknown or shrinking."""
        if self.pos_before is None or self.pos_after is None:
            return Decimal(0)
        return max(abs(self.pos_after) - abs(self.pos_before), Decimal(0))


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
class Quote:
    """Top of the book: what a market order would pay (ask) or get (bid) right now."""

    bid: Decimal
    ask: Decimal
    mark: Decimal

    def price_for(self, side: int) -> Decimal:
        return self.ask if side > 0 else self.bid


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
    #: Which strategy opened it: "first_fill" (v2/v3) or "cap20_cross" (botup.md v4).
    strategy: str = "first_fill"
    # -- cap20_cross trade state (botup.md v4 §4): fixed for the trade, or evolving with each fill ------------
    ratio_r: Decimal | None = None  # R = K x E0 / W0
    cap_notional: Decimal | None = None  # CAP = cap% x L x E0
    pending_qty: Decimal = Decimal(0)  # adds below the minimum order, carried to the next add
    capped: bool = False  # cost reached CAP: further adds ignored, reduces still followed
    qty_at_cap: Decimal | None = None
    leader_pos_at_cap: Decimal | None = None
    adds_done: int = 0
    cost_notional: Decimal = Decimal(0)  # sum of qty x price of our entries, scaled down by reduces
    leader_pos: Decimal | None = None  # the leader's |position| after his last fill of this trade
