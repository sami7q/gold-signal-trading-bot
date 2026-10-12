"""Margin / quantity / take-profit math (botup.md §7). Pure Decimal arithmetic, no floats anywhere.

    margin   = equity x RISK_PER_TRADE
    notional = min(margin x LEVERAGE, MAX_NOTIONAL_PER_COIN)
    qty      = floor((notional / mark) / qty_step) x qty_step
    tp       = entry x (1 + TP_ROI/LEVERAGE)  long   |  entry x (1 - TP_ROI/LEVERAGE)  short   -> rounded to tick
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from loco_copy.models import InstrumentInfo


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise ValueError("step must be positive")
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def round_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    if tick <= 0:
        raise ValueError("tick must be positive")
    return (price / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick


def take_profit_price(entry: Decimal, side: int, tp_roi: Decimal, leverage: int, tick: Decimal) -> Decimal:
    move = tp_roi / leverage
    raw = entry * (1 + move) if side > 0 else entry * (1 - move)
    return round_to_tick(raw, tick)


def liquidation_price(entry: Decimal, side: int, leverage: int, maintenance_margin_rate: Decimal) -> Decimal:
    """Isolated-margin approximation used by the research and the paper broker: an adverse move of
    (1/leverage - maintenance) wipes the margin out (~4.5 % at 20x)."""
    move = Decimal(1) / leverage - maintenance_margin_rate
    return entry * (1 - move) if side > 0 else entry * (1 + move)


@dataclass(frozen=True, slots=True)
class Sizing:
    margin: Decimal
    notional: Decimal
    qty: Decimal
    tp_price: Decimal
    capped: bool


def size_order(*, equity: Decimal, mark: Decimal, side: int, instrument: InstrumentInfo, risk_per_trade: Decimal,
               leverage: int, tp_roi: Decimal, max_notional: Decimal | None) -> Sizing:
    if mark <= 0:
        raise ValueError("mark price must be positive")
    margin = equity * risk_per_trade
    raw_notional = margin * leverage
    capped = max_notional is not None and raw_notional > max_notional
    notional = max_notional if capped and max_notional is not None else raw_notional
    qty = floor_to_step(notional / mark, instrument.qty_step)
    tp = take_profit_price(mark, side, tp_roi, leverage, instrument.tick_size)
    return Sizing(margin=margin, notional=qty * mark, qty=qty, tp_price=tp, capped=capped)


def reduce_qty(qty_open: Decimal, fraction: Decimal, instrument: InstrumentInfo) -> Decimal:
    """Same fraction as the leader, floored to the step. If what would remain is below min_qty, close it all."""
    qty = floor_to_step(qty_open * fraction, instrument.qty_step)
    if qty_open - qty < instrument.min_qty:
        return qty_open
    return qty


# -- cap20_cross (botup.md v4 §4) ------------------------------------------------------------------------------


def copy_ratio(k: Decimal, e0: Decimal, w0: Decimal) -> Decimal:
    """R = K x E0 / W0: our size per unit of the leader's size, fixed for the whole trade."""
    if w0 <= 0:
        raise ValueError("leader equity must be positive")
    return k * e0 / w0


def cap_notional(cap_pct: Decimal, leverage: int, e0: Decimal) -> Decimal:
    """CAP = cap% x L x E0: the trade's notional limit (= margin of cap% of E0). 1.0 x E0 at 5x, 0.6 x E0 at 3x."""
    return cap_pct / 100 * leverage * e0


@dataclass(frozen=True, slots=True)
class AddOrder:
    """What one add should do. `qty` > 0: send it. Else keep `pending` (below minimum).
    `capped`: the cap bound this add -- after `qty` (if any) the trade is capped for good (spec §4.2)."""

    qty: Decimal
    pending: Decimal
    capped: bool
    limited_by: str | None  # "cap" | "margin" | "exposure" when that limit cut the order


def add_order_qty(*, pending: Decimal, room_notional: Decimal, price: Decimal, instrument: InstrumentInfo,
                  available: Decimal, leverage: int, exposure_left: Decimal, min_notional: Decimal) -> AddOrder:
    """One add of a cap20 trade, research-faithful (analysis365/copysim.py): `pending` already holds
    dq x R plus what earlier small adds left over.

      q = min(pending, room / px, available x L / px, exposure_left / px)
      the cap binds and the room left is below the minimum   -> capped, nothing sent
      q x px below the minimum (and the cap not reached)       -> keep it pending
      q <= 0                                                   -> capped (no room left)
      else                                                     -> order floor(q) to the qty step; capped if the cap
                                                                  bound it (the research orders the whole room
                                                                  and caps; the qty step leaves dust, never room)"""
    if price <= 0:
        raise ValueError("price must be positive")
    limits = {"cap": max(room_notional, Decimal(0)) / price, "margin": max(available, Decimal(0)) * leverage / price,
              "exposure": max(exposure_left, Decimal(0)) / price}
    q = min(pending, *limits.values())
    limited_by = next((name for name, lim in limits.items() if lim < pending and q == lim), None)
    if limited_by == "cap" and room_notional < min_notional:
        return AddOrder(Decimal(0), Decimal(0), True, "cap")
    if q * price < min_notional and q * price < room_notional:
        if limited_by == "exposure" and q <= 0:
            return AddOrder(Decimal(0), Decimal(0), False, "exposure")
        return AddOrder(Decimal(0), q, False, limited_by)
    if q <= 0:
        return AddOrder(Decimal(0), Decimal(0), True, limited_by)
    qty = floor_to_step(q, instrument.qty_step)
    if qty < instrument.min_qty or qty <= 0 or qty * price < min_notional:
        return AddOrder(Decimal(0), q, False, limited_by)
    return AddOrder(qty, Decimal(0), limited_by == "cap", limited_by)
