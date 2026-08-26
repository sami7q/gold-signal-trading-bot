"""Pip <-> price conversion (PROJECT_SPEC.md section 10).

The pip size is derived from the broker's actual symbol metadata
(`digits`/`point`), never hard-coded — a "100 pip" target must mean
something different on a 2-digit-Gold broker than on a 3-digit-Gold broker,
and this module is the only place that conversion happens.

Convention (standard across MT5 brokers): a symbol quoted with an extra
"fractional" digit (3 or 5 digits) has 1 pip = 10 points; otherwise
1 pip = 1 point.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.core.constants import Side
from app.integrations.broker.base import SymbolInfo

_FRACTIONAL_DIGITS = {3, 5}


def pip_size(symbol_info: SymbolInfo) -> Decimal:
    if symbol_info.digits in _FRACTIONAL_DIGITS:
        return symbol_info.point * 10
    return symbol_info.point


def pip_distance_to_price(symbol_info: SymbolInfo, pips: Decimal) -> Decimal:
    return pip_size(symbol_info) * Decimal(pips)


def round_to_symbol(price: Decimal, symbol_info: SymbolInfo) -> Decimal:
    quantum = Decimal(1).scaleb(-symbol_info.digits)
    return price.quantize(quantum, rounding=ROUND_HALF_UP)


def compute_take_profit(
    *, entry_price: Decimal, pips: Decimal, symbol_info: SymbolInfo, side: Side = Side.BUY
) -> Decimal:
    distance = pip_distance_to_price(symbol_info, pips)
    raw = entry_price + distance if side == Side.BUY else entry_price - distance
    return round_to_symbol(raw, symbol_info)
