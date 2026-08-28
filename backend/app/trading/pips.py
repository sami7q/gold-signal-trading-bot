"""Pip <-> price conversion (PROJECT_SPEC.md section 10).

The pip size is derived from the broker's actual symbol metadata
(`digits`/`point`), never hard-coded — a "100 pip" target must mean
something different on a 2-digit-Gold broker than on a 3-digit-Gold broker,
and this module is the only place that conversion happens.

Convention (standard across MT5 brokers): a symbol quoted with an extra
"fractional" digit (3 or 5 digits) has 1 pip = 10 points; otherwise
1 pip = 1 point.

Gold (XAUUSD) is the exception this bot actually trades: brokers commonly
quote it with only 2 digits (cents) but still define 1 pip = 10 points
($0.10), the same "fractional" convention as 3/5-digit FX pairs — confirmed
against a real MT5 Demo XAUUSD symbol (digits=2, point=0.01) rather than
assumed (this was PROJECT_SPEC.md's MEDIUM-2, explicitly left unresolved
until real broker metadata was available).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.core.constants import CANONICAL_GOLD_SYMBOL, Side
from app.integrations.broker.base import SymbolInfo

_FRACTIONAL_DIGITS = {3, 5}


def pip_size(symbol_info: SymbolInfo) -> Decimal:
    is_gold = symbol_info.symbol.upper() == CANONICAL_GOLD_SYMBOL
    if symbol_info.digits in _FRACTIONAL_DIGITS or (is_gold and symbol_info.digits == 2):
        return symbol_info.point * 10
    return symbol_info.point


def pip_distance_to_price(symbol_info: SymbolInfo, pips: Decimal) -> Decimal:
    return pip_size(symbol_info) * Decimal(pips)


def price_distance_to_pips(symbol_info: SymbolInfo, price_distance: Decimal) -> Decimal:
    """Inverse of `pip_distance_to_price` -- how many pips a raw price
    distance represents. `price_distance` may be negative (the caller
    decides what a negative result means); this never guesses a sign."""
    return price_distance / pip_size(symbol_info)


def round_to_symbol(price: Decimal, symbol_info: SymbolInfo) -> Decimal:
    quantum = Decimal(1).scaleb(-symbol_info.digits)
    return price.quantize(quantum, rounding=ROUND_HALF_UP)


def compute_take_profit(
    *, entry_price: Decimal, pips: Decimal, symbol_info: SymbolInfo, side: Side = Side.BUY
) -> Decimal:
    distance = pip_distance_to_price(symbol_info, pips)
    raw = entry_price + distance if side == Side.BUY else entry_price - distance
    return round_to_symbol(raw, symbol_info)


def compute_stop_loss(
    *, entry_price: Decimal, pips: Decimal, symbol_info: SymbolInfo, side: Side = Side.BUY
) -> Decimal:
    """Stop Loss `pips` behind entry, in the direction that protects the
    trade (below entry for BUY, above for SELL) -- the mirror of
    `compute_take_profit`. Used for the auto-applied fallback Stop Loss
    (TradingSettings.auto_sl_fallback_pips) when a channel never sends its
    own SL before the signal's completion timeout."""
    distance = pip_distance_to_price(symbol_info, pips)
    raw = entry_price - distance if side == Side.BUY else entry_price + distance
    return round_to_symbol(raw, symbol_info)
