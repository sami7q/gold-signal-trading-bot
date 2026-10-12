"""A virtual cross-margin account filled at live Bybit prices (botup.md v5 §V5.5 "trio" ledger, v4 paper broker).

- Every order fills at once at the best ask (buy) or bid (sell) of that moment, plus a taker fee: what the
  same market order would have cost. No order ever leaves the process.
- One-way mode: one net position per symbol. Several leaders in one account net out exactly as they would on
  Bybit; each leader's own share is tracked by its copies, not here.
- Cross margin: equity = cash + unrealized PnL of every position. Liquidation when equity <= sum of
  notional x the symbol's tier-1 maintenance rate (Bybit's rule, botup.md v4 C-6) -- everything is closed at
  the mark, like the exchange would.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from loco_copy.brokers.base import Broker, BrokerError
from loco_copy.models import Account, ClosedTrade, Health, InstrumentInfo, OrderResult, Position, Quote

DEFAULT_MMR = Decimal("0.005")
QuoteSource = Callable[[str], Quote]


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class _Pos:
    qty: Decimal  # signed
    avg: Decimal


class VirtualAccount(Broker):
    def __init__(self, *, name: str = "virtual", instruments: dict[str, InstrumentInfo], quote: QuoteSource,
                 start_equity: Decimal, fee_rate: Decimal, leverage: int,
                 maintenance_rates: dict[str, Decimal] | None = None, clock: Callable[[], int] = _now_ms,
                 slippage_bps: Decimal = Decimal(0)) -> None:
        self.name = name
        self._instruments = instruments
        self._quote = quote
        self._fee = fee_rate
        #: on top of the bid / ask: 0 for the observe ledger (v5 §V5.5), PAPER_SLIPPAGE_BPS for a paper account
        self._slip = slippage_bps / 10_000
        self._lev = leverage
        self._mmr = maintenance_rates or {}
        self._clock = clock
        self.start_equity = start_equity
        self.cash = start_equity  # deposit + realized PnL - fees
        self.fees_paid = Decimal(0)
        self.liquidations = 0
        self._pos: dict[str, _Pos] = {}
        self._marks: dict[str, Decimal] = {}
        self._orders: dict[str, OrderResult] = {}

    # -- state ------------------------------------------------------------------------------------------------

    def export_state(self) -> dict[str, Any]:
        return {"cash": str(self.cash), "fees_paid": str(self.fees_paid), "liquidations": self.liquidations,
                "start_equity": str(self.start_equity),
                "positions": {s: {"qty": str(p.qty), "avg": str(p.avg)} for s, p in self._pos.items()},
                "marks": {s: str(m) for s, m in self._marks.items()}}

    def import_state(self, data: dict[str, Any]) -> None:
        self.cash = Decimal(data["cash"])
        self.fees_paid = Decimal(data.get("fees_paid", "0"))
        self.liquidations = int(data.get("liquidations", 0))
        self.start_equity = Decimal(data.get("start_equity", str(self.start_equity)))
        self._pos = {s: _Pos(Decimal(p["qty"]), Decimal(p["avg"])) for s, p in data.get("positions", {}).items()}
        self._marks = {s: Decimal(m) for s, m in data.get("marks", {}).items()}

    def set_mark(self, symbol: str, mark: Decimal) -> None:
        self._marks[symbol] = mark

    def held_symbols(self) -> list[str]:
        return list(self._pos)

    def mmr(self, symbol: str) -> Decimal:
        return self._mmr.get(symbol, DEFAULT_MMR)

    # -- account figures --------------------------------------------------------------------------------------

    def _mark(self, symbol: str) -> Decimal:
        return self._marks.get(symbol) or self._pos[symbol].avg

    def unrealized(self) -> Decimal:
        return sum((p.qty * (self._mark(s) - p.avg) for s, p in self._pos.items()), Decimal(0))

    def equity(self) -> Decimal:
        return self.cash + self.unrealized()

    def notional(self) -> Decimal:
        return sum((abs(p.qty) * self._mark(s) for s, p in self._pos.items()), Decimal(0))

    def maintenance(self) -> Decimal:
        return sum((abs(p.qty) * self._mark(s) * self.mmr(s) for s, p in self._pos.items()), Decimal(0))

    def margin_used(self) -> Decimal:
        return sum((abs(p.qty) * p.avg / self._lev for p in self._pos.values()), Decimal(0))

    def liquidation_distance(self) -> Decimal | None:
        """equity / maintenance: 1.0 = liquidated. None with nothing open."""
        mm = self.maintenance()
        return self.equity() / mm if mm > 0 else None

    def summary(self) -> dict[str, Any]:
        """What the dashboard's trio panel and the daily line show."""
        dist = self.liquidation_distance()
        return {"name": self.name, "equity": str(self.equity()), "start_equity": str(self.start_equity),
                "cash": str(self.cash), "unrealized": str(self.unrealized()), "notional": str(self.notional()),
                "margin_used": str(self.margin_used()), "maintenance": str(self.maintenance()),
                "liquidation_distance": str(dist) if dist is not None else None, "fees_paid": str(self.fees_paid),
                "liquidations": self.liquidations,
                "positions": [{"symbol": s, "side": 1 if p.qty > 0 else -1, "qty": str(abs(p.qty)), "entry": str(p.avg),
                               "mark": str(self._mark(s)), "upnl": str(p.qty * (self._mark(s) - p.avg)),
                               "mmr": str(self.mmr(s))} for s, p in self._pos.items()]}

    # -- Broker interface ---------------------------------------------------------------------------------------

    def instruments(self) -> dict[str, InstrumentInfo]:
        return self._instruments

    def account(self) -> Account:
        equity = self.equity()
        return Account(equity=equity, available=max(equity - self.margin_used(), Decimal(0)))

    def total_equity(self) -> Decimal:
        return self.equity()

    def quote(self, symbol: str) -> Quote:
        q = self._quote(symbol)
        self._marks[symbol] = q.mark
        return q

    def mark_price(self, symbol: str) -> Decimal:
        return self.quote(symbol).mark

    def ensure_isolated_leverage(self, symbol: str, leverage: int) -> None:
        return None

    def ensure_cross_leverage(self, symbol: str, leverage: int) -> None:
        return None  # the virtual account is cross by construction

    def market_order(self, symbol: str, side: int, qty: Decimal, *, client_id: str, reduce_only: bool) -> OrderResult:
        if client_id in self._orders:
            return self._orders[client_id]
        if qty <= 0:
            return OrderResult(False, client_id, error="qty must be positive")
        pos = self._pos.get(symbol)
        if reduce_only and (pos is None or pos.qty * side >= 0):
            return OrderResult(False, client_id, error="reduce-only: nothing to reduce")
        if reduce_only and pos is not None:
            qty = min(qty, abs(pos.qty))
        try:
            q = self.quote(symbol)
        except Exception as exc:  # noqa: BLE001 - no price, no fill
            return OrderResult(False, client_id, error=f"no quote: {exc}")
        px = q.price_for(side) * (1 + side * self._slip)
        fee = qty * px * self._fee
        self._apply(symbol, side * qty, px)
        self.cash -= fee
        self.fees_paid += fee
        res = OrderResult(True, client_id, order_id=client_id, filled_qty=qty, avg_price=px, fee=fee,
                          filled_at_ms=self._clock())
        self._orders[client_id] = res
        return res

    def _apply(self, symbol: str, signed_qty: Decimal, px: Decimal) -> None:
        pos = self._pos.get(symbol)
        if pos is None or pos.qty == 0:
            self._pos[symbol] = _Pos(signed_qty, px)
            return
        if pos.qty * signed_qty > 0:  # same direction: average in
            total = abs(pos.qty) + abs(signed_qty)
            pos.avg = (pos.avg * abs(pos.qty) + px * abs(signed_qty)) / total
            pos.qty += signed_qty
            return
        closing = min(abs(signed_qty), abs(pos.qty))
        self.cash += closing * (px - pos.avg) * (1 if pos.qty > 0 else -1)
        rest = pos.qty + signed_qty
        if rest == 0:
            del self._pos[symbol]
        elif rest * pos.qty > 0:
            pos.qty = rest  # partly reduced, same avg
        else:
            self._pos[symbol] = _Pos(rest, px)  # flipped: the remainder opens at this price

    def market_open(self, symbol: str, side: int, qty: Decimal, *, client_id: str, max_slippage: Decimal,
                    take_profit: Decimal | None) -> OrderResult:
        return self.market_order(symbol, side, qty, client_id=client_id, reduce_only=False)

    def market_reduce(self, symbol: str, position_side: int, qty: Decimal, *, client_id: str) -> OrderResult:
        return self.market_order(symbol, -position_side, qty, client_id=client_id, reduce_only=True)

    def set_take_profit(self, symbol: str, price: Decimal) -> None:
        return None

    def cancel_take_profit(self, symbol: str) -> None:
        return None

    def position(self, symbol: str) -> Position | None:
        p = self._pos.get(symbol)
        if p is None or p.qty == 0:
            return None
        return Position(symbol=symbol, side=1 if p.qty > 0 else -1, qty=abs(p.qty), entry_price=p.avg)

    def positions(self) -> list[Position]:
        return [x for x in (self.position(s) for s in list(self._pos)) if x is not None]

    def order_by_client_id(self, client_id: str) -> OrderResult | None:
        return self._orders.get(client_id)

    def closed_trades(self, symbol: str, since_ms: int) -> list[ClosedTrade]:
        return []  # nothing closes on its own here except a liquidation, which the app books itself

    def health(self) -> Health:
        return Health(True, self.name)

    # -- marking / liquidation ----------------------------------------------------------------------------------

    def mark_all(self, quotes: dict[str, Quote]) -> list[tuple[str, Decimal, Decimal]] | None:
        """New marks for what is held, then Bybit's cross rule. Returns the closed positions
        [(symbol, signed qty, price)] if the account was liquidated, else None."""
        for s in self._pos:
            if s in quotes:
                self._marks[s] = quotes[s].mark
        if not self._pos or self.equity() > self.maintenance():
            return None
        closed = []
        for s, p in list(self._pos.items()):
            px = self._mark(s)
            fee = abs(p.qty) * px * self._fee
            closed.append((s, p.qty, px))
            self._apply(s, -p.qty, px)
            self.cash -= fee
            self.fees_paid += fee
        self.cash = max(self.cash, Decimal(0))
        self.liquidations += 1
        return closed


def virtual_quote_source(fetch_one: Callable[[str], dict[str, Quote]]) -> QuoteSource:
    def quote(symbol: str) -> Quote:
        q = fetch_one(symbol).get(symbol)
        if q is None:
            raise BrokerError(f"no quote for {symbol}")
        return q

    return quote
