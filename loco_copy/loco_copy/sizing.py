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
