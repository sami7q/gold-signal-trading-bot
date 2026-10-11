"""Hyperliquid coin -> exchange symbol, checked against the exchange's real instrument list.

Hyperliquid quotes some small coins per 1000 units with a `k` prefix (`kPEPE` = 1000 PEPE); Bybit lists the
same contract as `1000PEPEUSDT` (or, for a few coins, `SHIB1000USDT`). `price_multiplier` converts a leader
price into the exchange's units: exchange_px = leader_px * price_multiplier. A coin with no listed candidate
is not listed -- the caller rejects it with SYMBOL_NOT_LISTED, never silently.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SymbolMap:
    coin: str
    symbol: str
    price_multiplier: Decimal


def candidates(coin: str) -> list[tuple[str, Decimal]]:
    one, thousand = Decimal(1), Decimal(1000)
    if coin.startswith("k") and len(coin) > 1 and coin[1:].isupper():
        base = coin[1:]
        return [(f"1000{base}USDT", one), (f"{base}1000USDT", one), (f"{base}USDT", one / thousand)]
    return [(f"{coin}USDT", one), (f"1000{coin}USDT", thousand), (f"{coin}1000USDT", thousand)]


class SymbolMapper:
    """`identity=True`: symbols are the Hyperliquid coin names themselves (replay / Hyperliquid-priced paper)."""

    def __init__(self, listed_symbols: Iterable[str], *, identity: bool = False) -> None:
        self._listed = frozenset(listed_symbols)
        self._identity = identity
        self._cache: dict[str, SymbolMap | None] = {}

    def map(self, coin: str) -> SymbolMap | None:
        if coin not in self._cache:
            options = [(coin, Decimal(1))] if self._identity else candidates(coin)
            self._cache[coin] = next(
                (SymbolMap(coin, sym, mult) for sym, mult in options if sym in self._listed), None
            )
        return self._cache[coin]

    def coin_for_symbol(self, symbol: str) -> str | None:
        return next((c for c, m in self._cache.items() if m is not None and m.symbol == symbol), None)
