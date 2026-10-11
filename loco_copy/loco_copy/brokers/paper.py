"""Simulated exchange: isolated margin, market fills at the current price ± slippage, exchange-side take profit
and liquidation, taker fees. Prices are pushed in from outside -- live Hyperliquid mids in `run`, recorded
candles in `replay`. Funding is not simulated (the research ignores it too).

Liquidation follows the research exactly: an adverse move of (1/leverage - maintenance_margin_rate) closes the
position and the whole isolated margin is lost. Within one candle a liquidation is checked before the take
profit (conservative, same as the research).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from loco_copy.brokers.base import Broker, BrokerError
from loco_copy.models import Account, ClosedTrade, Health, InstrumentInfo, OrderResult, Position
from loco_copy.sizing import liquidation_price


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class _PaperPosition:
    symbol: str
    side: int
    qty: Decimal
    entry: Decimal
    margin: Decimal
    liq_px: Decimal
    opened_ms: int
    tp: Decimal | None = None


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, *, instruments: dict[str, InstrumentInfo], start_equity: Decimal, fee_rate: Decimal,
                 slippage_bps: Decimal, leverage: int, maintenance_margin_rate: Decimal,
                 include_unrealized: bool = True, stale_after_ms: int | None = None,
                 clock: Callable[[], int] = _now_ms) -> None:
        self._instruments = instruments
        self._balance = start_equity
        self._fee = fee_rate
        self._slip = slippage_bps / Decimal(10_000)
        self._leverage = leverage
        self._mmr = maintenance_margin_rate
        self._include_unrealized = include_unrealized
        self._stale_after = stale_after_ms
        self._clock = clock
        self._prices: dict[str, Decimal] = {}
        self._last_price_ms: int | None = None
        self._positions: dict[str, _PaperPosition] = {}
        self._orders: dict[str, OrderResult] = {}
        self._closed: list[ClosedTrade] = []
        self._lock = threading.RLock()

    # -- price input ---------------------------------------------------------------------------------------

    def set_price(self, symbol: str, price: Decimal, ts_ms: int | None = None, *,
                  check: bool = True) -> list[ClosedTrade]:
        """Live mode: a new mark for `symbol`; triggers TP / liquidation if crossed."""
        with self._lock:
            ts = ts_ms if ts_ms is not None else self._clock()
            self._prices[symbol] = price
            self._last_price_ms = ts
            return self._check(symbol, high=price, low=price, ts=ts) if check else []

    def on_candle(self, symbol: str, ts_ms: int, high: Decimal, low: Decimal, close: Decimal) -> list[ClosedTrade]:
        """Replay mode: one recorded candle. Only candles at or after the position's open count."""
        with self._lock:
            self._prices[symbol] = close
            self._last_price_ms = ts_ms
            return self._check(symbol, high=high, low=low, ts=ts_ms)

    def _check(self, symbol: str, *, high: Decimal, low: Decimal, ts: int) -> list[ClosedTrade]:
        pos = self._positions.get(symbol)
        if pos is None or ts < pos.opened_ms:
            return []
        if (low <= pos.liq_px) if pos.side > 0 else (high >= pos.liq_px):
            return [self._close(pos, pos.qty, pos.liq_px, ts, liquidation=True)]
        if pos.tp is not None and ((high >= pos.tp) if pos.side > 0 else (low <= pos.tp)):
            return [self._close(pos, pos.qty, pos.tp, ts, liquidation=False)]
        return []

    # -- Broker interface ------------------------------------------------------------------------------------

    def instruments(self) -> dict[str, InstrumentInfo]:
        return dict(self._instruments)

    def account(self) -> Account:
        with self._lock:
            locked = sum((p.margin for p in self._positions.values()), Decimal(0))
            unrealized = Decimal(0)
            if self._include_unrealized:
                for p in self._positions.values():
                    px = self._prices.get(p.symbol, p.entry)
                    unrealized += p.side * (px - p.entry) * p.qty
            return Account(equity=self._balance + unrealized, available=self._balance - locked)

    def mark_price(self, symbol: str) -> Decimal:
        with self._lock:
            if symbol not in self._prices:
                raise BrokerError(f"no price for {symbol}")
            return self._prices[symbol]

    def ensure_isolated_leverage(self, symbol: str, leverage: int) -> None:
        if symbol not in self._instruments:
            raise BrokerError(f"{symbol} not listed")
        self._leverage = leverage

    def market_open(self, symbol: str, side: int, qty: Decimal, *, client_id: str, max_slippage: Decimal,
                    take_profit: Decimal | None) -> OrderResult:
        with self._lock:
            if client_id in self._orders:
                return self._orders[client_id]
            if symbol in self._positions:
                return self._reject(client_id, "position already open (one-way mode)")
            if qty <= 0 or symbol not in self._prices:
                return self._reject(client_id, "bad qty or no price")
            if self._slip > max_slippage:
                return self._reject(client_id, "slippage above max_slippage")
            ts = self._clock()
            px = self._prices[symbol] * (1 + side * self._slip)
            notional = px * qty
            margin = notional / self._leverage
            fee = notional * self._fee
            if self._balance - self._locked() < margin + fee:
                return self._reject(client_id, "insufficient available balance")
            self._balance -= fee
            self._positions[symbol] = _PaperPosition(
                symbol=symbol, side=side, qty=qty, entry=px, margin=margin,
                liq_px=liquidation_price(px, side, self._leverage, self._mmr), opened_ms=ts, tp=take_profit)
            res = OrderResult(True, client_id, order_id=f"paper-{len(self._orders) + 1}", filled_qty=qty,
                              avg_price=px, fee=fee, filled_at_ms=ts)
            self._orders[client_id] = res
            return res

    def market_reduce(self, symbol: str, position_side: int, qty: Decimal, *, client_id: str) -> OrderResult:
        with self._lock:
            if client_id in self._orders:
                return self._orders[client_id]
            pos = self._positions.get(symbol)
            if pos is None or pos.side != position_side:
                return self._reject(client_id, "no position to reduce")
            qty = min(qty, pos.qty)
            ts = self._clock()
            px = self._prices.get(symbol, pos.entry) * (1 - position_side * self._slip)
            self._close(pos, qty, px, ts, liquidation=False, record=False)
            res = OrderResult(True, client_id, order_id=f"paper-{len(self._orders) + 1}", filled_qty=qty,
                              avg_price=px, fee=qty * px * self._fee, filled_at_ms=ts)
            self._orders[client_id] = res
            return res

    def set_take_profit(self, symbol: str, price: Decimal) -> None:
        with self._lock:
            pos = self._positions.get(symbol)
            if pos is None:
                raise BrokerError(f"no position on {symbol}")
            pos.tp = price

    def cancel_take_profit(self, symbol: str) -> None:
        with self._lock:
            pos = self._positions.get(symbol)
            if pos is not None:
                pos.tp = None

    def position(self, symbol: str) -> Position | None:
        with self._lock:
            p = self._positions.get(symbol)
            return Position(p.symbol, p.side, p.qty, p.entry, p.tp) if p else None

    def positions(self) -> list[Position]:
        with self._lock:
            return [Position(p.symbol, p.side, p.qty, p.entry, p.tp) for p in self._positions.values()]

    def order_by_client_id(self, client_id: str) -> OrderResult | None:
        return self._orders.get(client_id)

    def closed_trades(self, symbol: str, since_ms: int) -> list[ClosedTrade]:
        with self._lock:
            return [c for c in self._closed if c.symbol == symbol and c.closed_at_ms >= since_ms]

    def health(self) -> Health:
        if self._stale_after is not None:
            if self._last_price_ms is None:
                return Health(False, "no prices yet")
            age = self._clock() - self._last_price_ms
            if age > self._stale_after:
                return Health(False, f"prices {age // 1000}s old")
        return Health(True, "paper")

    # -- persistence (the live paper account must survive a restart) ---------------------------------------

    def export_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "balance": str(self._balance),
                "positions": [{"symbol": p.symbol, "side": p.side, "qty": str(p.qty), "entry": str(p.entry),
                               "margin": str(p.margin), "liq_px": str(p.liq_px), "opened_ms": p.opened_ms,
                               "tp": str(p.tp) if p.tp is not None else None} for p in self._positions.values()],
                "orders": {k: {"success": v.success, "order_id": v.order_id, "filled_qty": str(v.filled_qty),
                               "avg_price": str(v.avg_price) if v.avg_price is not None else None,
                               "fee": str(v.fee), "error": v.error, "filled_at_ms": v.filled_at_ms}
                           for k, v in list(self._orders.items())[-500:]},
                "closed": [{"symbol": c.symbol, "side": c.side, "qty": str(c.qty), "px": str(c.avg_exit_price),
                            "pnl": str(c.realized_pnl), "ts": c.closed_at_ms, "liq": c.is_liquidation}
                           for c in self._closed[-500:]],
            }

    def import_state(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._balance = Decimal(str(data["balance"]))
            self._positions = {}
            for p in data.get("positions", []):
                tp = p.get("tp")
                self._positions[p["symbol"]] = _PaperPosition(
                    symbol=p["symbol"], side=int(p["side"]), qty=Decimal(p["qty"]), entry=Decimal(p["entry"]),
                    margin=Decimal(p["margin"]), liq_px=Decimal(p["liq_px"]), opened_ms=int(p["opened_ms"]),
                    tp=Decimal(tp) if tp is not None else None)
            self._orders = {k: OrderResult(v["success"], k, order_id=v["order_id"], filled_qty=Decimal(v["filled_qty"]),
                                           avg_price=Decimal(v["avg_price"]) if v["avg_price"] else None,
                                           fee=Decimal(v["fee"]), error=v["error"], filled_at_ms=v["filled_at_ms"])
                            for k, v in data.get("orders", {}).items()}
            self._closed = [ClosedTrade(c["symbol"], int(c["side"]), Decimal(c["qty"]), Decimal(c["px"]),
                                        Decimal(c["pnl"]), int(c["ts"]), bool(c["liq"]))
                            for c in data.get("closed", [])]

    # -- internals -------------------------------------------------------------------------------------------

    def _locked(self) -> Decimal:
        return sum((p.margin for p in self._positions.values()), Decimal(0))

    def _reject(self, client_id: str, why: str) -> OrderResult:
        res = OrderResult(False, client_id, error=why)
        self._orders[client_id] = res
        return res

    def _close(self, pos: _PaperPosition, qty: Decimal, px: Decimal, ts: int, *, liquidation: bool,
               record: bool = True) -> ClosedTrade:
        share = qty / pos.qty
        margin_part = pos.margin * share
        fee = qty * px * self._fee
        pnl = -margin_part if liquidation else pos.side * (px - pos.entry) * qty
        self._balance += pnl - fee
        ct = ClosedTrade(symbol=pos.symbol, side=pos.side, qty=qty, avg_exit_price=px, realized_pnl=pnl - fee,
                         closed_at_ms=ts, is_liquidation=liquidation)
        if record:
            self._closed.append(ct)
        pos.qty -= qty
        pos.margin -= margin_part
        if pos.qty <= 0:
            del self._positions[pos.symbol]
        return ct
