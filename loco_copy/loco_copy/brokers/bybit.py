"""Bybit v5 USDT perpetuals, demo or live (botup.md §5.4-5.5). Synchronous; the app calls it via to_thread.

Checked against the official v5 docs (2026-10):
- Demo REST is https://api-demo.bybit.com (pybit `demo=True`); demo WS has no public streams.
- Margin mode is account-wide on unified accounts: /v5/account/set-margin-mode setMarginMode=ISOLATED_MARGIN.
- Opens are sent as a marketable LIMIT + IOC at mark x (1 ± max_slippage): fills like a market order but can
  never fill worse than that cap, and TP can be attached with tpslMode=Full (Full mode = market TP).
- Exits are Market + reduceOnly. TP is set / moved / cancelled with /v5/position/trading-stop (takeProfit="0"
  cancels).
- orderLinkId allows only [A-Za-z0-9_-] up to 36 chars, so our client_id is hashed into one deterministically
  (`link_id`) -- the same client_id always maps to the same orderLinkId, which is what crash recovery needs.
- closed-pnl execType "BustTrade" = liquidation.

Not yet done: the private WebSocket. The reconciler's 30 s REST polling covers TP / liquidation detection;
exchange-side TP itself needs no bot at all once it's placed.
"""

from __future__ import annotations

import hashlib
import time
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any

import httpx

from loco_copy.brokers.base import Broker, BrokerError
from loco_copy.models import Account, ClosedTrade, Health, InstrumentInfo, OrderResult, Position

MAINNET_REST = "https://api.bybit.com"
DEMO_REST = "https://api-demo.bybit.com"
TERMINAL = {"Filled", "PartiallyFilledCanceled", "Cancelled", "Rejected", "Deactivated"}
LEVERAGE_NOT_MODIFIED = 110043
TPSL_NOT_MODIFIED = 34040
SEVEN_DAYS_MS = 7 * 86_400_000


def link_id(client_id: str) -> str:
    return "lc-" + hashlib.sha256(client_id.encode()).hexdigest()[:32]


def parse_instruments(rows: list[dict[str, Any]]) -> dict[str, InstrumentInfo]:
    out: dict[str, InstrumentInfo] = {}
    for r in rows:
        if r.get("status") != "Trading" or r.get("quoteCoin") != "USDT" or r.get("contractType") != "LinearPerpetual":
            continue
        lot, price, lev = r.get("lotSizeFilter", {}), r.get("priceFilter", {}), r.get("leverageFilter", {})
        out[r["symbol"]] = InstrumentInfo(
            symbol=r["symbol"], qty_step=Decimal(lot["qtyStep"]), min_qty=Decimal(lot["minOrderQty"]),
            tick_size=Decimal(price["tickSize"]), max_leverage=int(Decimal(lev.get("maxLeverage", "1"))),
            min_notional=Decimal(lot.get("minNotionalValue") or "0"))
    return out


def fetch_public_instruments(base_url: str = MAINNET_REST) -> dict[str, InstrumentInfo]:
    """No keys needed -- also used by the paper broker so paper trading only uses what Bybit really lists."""
    rows: list[dict[str, Any]] = []
    cursor = ""
    with httpx.Client(timeout=20) as http:
        for _ in range(20):
            params = {"category": "linear", "limit": "1000"} | ({"cursor": cursor} if cursor else {})
            data = http.get(f"{base_url}/v5/market/instruments-info", params=params).json()
            if data.get("retCode") != 0:
                raise BrokerError(f"instruments-info: {data.get('retMsg')}")
            rows += data["result"]["list"]
            cursor = data["result"].get("nextPageCursor") or ""
            if not cursor:
                break
    return parse_instruments(rows)


def _dec(v: Any) -> Decimal:
    return Decimal(str(v)) if v not in (None, "") else Decimal(0)


class BybitBroker(Broker):
    name = "bybit"

    def __init__(self, *, api_key: str, api_secret: str, env: str, session: Any | None = None,
                 poll_seconds: float = 0.25, fill_timeout_seconds: float = 8.0) -> None:
        if env not in ("demo", "live"):
            raise ValueError("env must be demo or live")
        self.env = env
        if session is None:
            from pybit.unified_trading import HTTP  # noqa: PLC0415 - only needed with real keys

            session = HTTP(testnet=False, demo=env == "demo", api_key=api_key, api_secret=api_secret,
                           recv_window=10_000)
        self._s = session
        self._poll = poll_seconds
        self._timeout = fill_timeout_seconds
        self._instruments: dict[str, InstrumentInfo] | None = None
        self._isolated_checked = False
        self._leverage_set: dict[str, int] = {}

    # -- helpers ---------------------------------------------------------------------------------------------

    def _call(self, fn: str, **kw: Any) -> dict[str, Any]:
        try:
            res: dict[str, Any] = getattr(self._s, fn)(**kw)
        except Exception as exc:  # pybit raises on non-zero retCode and on network errors
            code = getattr(exc, "status_code", None)
            if code is None:
                raise BrokerError(f"{fn}: {exc}") from exc
            raise _CodedError(fn, int(code), str(exc)) from exc
        if res.get("retCode") not in (0, None):
            raise _CodedError(fn, int(res["retCode"]), str(res.get("retMsg")))
        return res.get("result") or {}

    def _inst(self, symbol: str) -> InstrumentInfo:
        inst = self.instruments().get(symbol)
        if inst is None:
            raise BrokerError(f"{symbol} not listed")
        return inst

    # -- Broker interface ------------------------------------------------------------------------------------

    def instruments(self) -> dict[str, InstrumentInfo]:
        if self._instruments is None:
            rows: list[dict[str, Any]] = []
            cursor = ""
            for _ in range(20):
                kw: dict[str, Any] = {"category": "linear", "limit": 1000}
                if cursor:
                    kw["cursor"] = cursor
                res = self._call("get_instruments_info", **kw)
                rows += res.get("list", [])
                cursor = res.get("nextPageCursor") or ""
                if not cursor:
                    break
            self._instruments = parse_instruments(rows)
        return self._instruments

    def account(self) -> Account:
        res = self._call("get_wallet_balance", accountType="UNIFIED", coin="USDT")
        acct = (res.get("list") or [{}])[0]
        coin = next((c for c in acct.get("coin", []) if c.get("coin") == "USDT"), None)
        if coin is None:
            return Account(equity=Decimal(0), available=Decimal(0))
        equity = _dec(coin.get("equity"))
        available = _dec(coin.get("walletBalance")) - _dec(coin.get("totalPositionIM")) - _dec(coin.get("totalOrderIM"))
        return Account(equity=equity, available=max(available, Decimal(0)))

    def mark_price(self, symbol: str) -> Decimal:
        res = self._call("get_tickers", category="linear", symbol=symbol)
        rows = res.get("list") or []
        if not rows:
            raise BrokerError(f"no ticker for {symbol}")
        return _dec(rows[0].get("markPrice"))

    def ensure_isolated_leverage(self, symbol: str, leverage: int) -> None:
        if not self._isolated_checked:
            info = self._call("get_account_info")
            if info.get("marginMode") != "ISOLATED_MARGIN":
                self._call("set_margin_mode", setMarginMode="ISOLATED_MARGIN")
            self._isolated_checked = True
        if self._leverage_set.get(symbol) == leverage:
            return
        try:
            self._call("set_leverage", category="linear", symbol=symbol, buyLeverage=str(leverage),
                       sellLeverage=str(leverage))
        except _CodedError as exc:
            if exc.code != LEVERAGE_NOT_MODIFIED:
                raise
        self._leverage_set[symbol] = leverage

    def market_open(self, symbol: str, side: int, qty: Decimal, *, client_id: str, max_slippage: Decimal,
                    take_profit: Decimal | None) -> OrderResult:
        inst = self._inst(symbol)
        mark = self.mark_price(symbol)
        cap = mark * (1 + side * max_slippage)
        price = (cap / inst.tick_size).to_integral_value(rounding=ROUND_DOWN if side > 0 else ROUND_UP) * inst.tick_size
        kw: dict[str, Any] = dict(category="linear", symbol=symbol, side="Buy" if side > 0 else "Sell",
                                  orderType="Limit", timeInForce="IOC", price=str(price), qty=str(qty),
                                  orderLinkId=link_id(client_id), positionIdx=0)
        if take_profit is not None:
            kw |= dict(takeProfit=str(take_profit), tpslMode="Full", tpTriggerBy="LastPrice")
        try:
            self._call("place_order", **kw)
        except _CodedError as exc:
            return OrderResult(False, client_id, error=f"{exc.code}: {exc.msg}")
        return self._await_fill(client_id)

    def market_reduce(self, symbol: str, position_side: int, qty: Decimal, *, client_id: str) -> OrderResult:
        try:
            self._call("place_order", category="linear", symbol=symbol, side="Sell" if position_side > 0 else "Buy",
                       orderType="Market", qty=str(qty), reduceOnly=True, orderLinkId=link_id(client_id),
                       positionIdx=0)
        except _CodedError as exc:
            return OrderResult(False, client_id, error=f"{exc.code}: {exc.msg}")
        return self._await_fill(client_id)

    def set_take_profit(self, symbol: str, price: Decimal) -> None:
        self._trading_stop(symbol, str(price))

    def cancel_take_profit(self, symbol: str) -> None:
        self._trading_stop(symbol, "0")

    def _trading_stop(self, symbol: str, tp: str) -> None:
        try:
            self._call("set_trading_stop", category="linear", symbol=symbol, takeProfit=tp, tpslMode="Full",
                       tpTriggerBy="LastPrice", positionIdx=0)
        except _CodedError as exc:
            if exc.code != TPSL_NOT_MODIFIED:
                raise

    def position(self, symbol: str) -> Position | None:
        rows = self._call("get_positions", category="linear", symbol=symbol).get("list") or []
        return next((p for p in (self._position(r) for r in rows) if p is not None), None)

    def positions(self) -> list[Position]:
        out: list[Position] = []
        cursor = ""
        for _ in range(20):
            kw: dict[str, Any] = {"category": "linear", "settleCoin": "USDT", "limit": 200}
            if cursor:
                kw["cursor"] = cursor
            res = self._call("get_positions", **kw)
            out += [p for p in (self._position(r) for r in res.get("list") or []) if p is not None]
            cursor = res.get("nextPageCursor") or ""
            if not cursor:
                break
        return out

    @staticmethod
    def _position(r: dict[str, Any]) -> Position | None:
        size = _dec(r.get("size"))
        if size <= 0 or r.get("side") not in ("Buy", "Sell"):
            return None
        tp = _dec(r.get("takeProfit"))
        return Position(symbol=r["symbol"], side=1 if r["side"] == "Buy" else -1, qty=size,
                        entry_price=_dec(r.get("avgPrice")), take_profit=tp if tp > 0 else None)

    def order_by_client_id(self, client_id: str) -> OrderResult | None:
        link = link_id(client_id)
        for fn in ("get_open_orders", "get_order_history"):
            rows = self._call(fn, category="linear", orderLinkId=link).get("list") or []
            if rows:
                return self._order_result(client_id, rows[0])
        return None

    @staticmethod
    def _order_result(client_id: str, o: dict[str, Any]) -> OrderResult:
        filled = _dec(o.get("cumExecQty"))
        status = o.get("orderStatus", "")
        ok = filled > 0 and status in TERMINAL | {"PartiallyFilled"}
        return OrderResult(success=ok, client_id=client_id, order_id=o.get("orderId"), filled_qty=filled,
                           avg_price=_dec(o.get("avgPrice")) if o.get("avgPrice") else None,
                           fee=_dec(o.get("cumExecFee")), error=None if ok else f"{status} {o.get('rejectReason', '')}",
                           filled_at_ms=int(o["updatedTime"]) if o.get("updatedTime") else None)

    def _await_fill(self, client_id: str) -> OrderResult:
        deadline = time.monotonic() + self._timeout
        last: OrderResult | None = None
        while time.monotonic() < deadline:
            rows = self._call("get_open_orders", category="linear", orderLinkId=link_id(client_id)).get("list") or []
            if rows:
                last = self._order_result(client_id, rows[0])
                if rows[0].get("orderStatus") in TERMINAL:
                    return last
            time.sleep(self._poll)
        found = self.order_by_client_id(client_id)
        if found is not None:
            return found
        raise BrokerError(f"order {client_id} not confirmed within {self._timeout}s (last={last})")

    def closed_trades(self, symbol: str, since_ms: int) -> list[ClosedTrade]:
        start = max(since_ms, int(time.time() * 1000) - SEVEN_DAYS_MS + 60_000)
        res = self._call("get_closed_pnl", category="linear", symbol=symbol, startTime=start, limit=100)
        rows = res.get("list") or []
        return [ClosedTrade(symbol=r["symbol"], side=-1 if r.get("side") == "Buy" else 1, qty=_dec(r.get("closedSize")),
                            avg_exit_price=_dec(r.get("avgExitPrice")), realized_pnl=_dec(r.get("closedPnl")),
                            closed_at_ms=int(r.get("updatedTime") or r.get("createdTime") or 0),
                            is_liquidation=r.get("execType") == "BustTrade", open_fee=_dec(r.get("openFee")))
                for r in rows]

    def health(self) -> Health:
        try:
            self._call("get_server_time")
            return Health(True, f"bybit {self.env}")
        except BrokerError as exc:
            return Health(False, str(exc))


class _CodedError(BrokerError):
    def __init__(self, fn: str, code: int, msg: str) -> None:
        super().__init__(f"{fn}: {code} {msg}")
        self.code = code
        self.msg = msg
