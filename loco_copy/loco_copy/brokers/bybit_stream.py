"""Live Bybit linear prices in memory: the public `tickers` stream (mainnet market data -- the demo account trades
at the same prices). Reading a quote costs microseconds instead of a REST round trip (~175 ms from a European
server), so an order -- or, when observing, the would-be price -- uses the price of the moment the leader's fill
arrived. A symbol not watched yet, or quiet for longer than `stale_ms`, falls back to REST and is watched from then on.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from loco_copy.brokers.bybit import MAINNET_REST, fetch_public_tickers, parse_quote
from loco_copy.models import Quote

#: Bybit takes a list of topics per subscribe request; keep each request small.
SUBSCRIBE_BATCH = 10


def _now_ms() -> int:
    return int(time.time() * 1000)


def _pybit_linear() -> Any:
    from pybit.unified_trading import WebSocket  # noqa: PLC0415 - only when the stream is really used

    return WebSocket(testnet=False, channel_type="linear", ping_interval=20, ping_timeout=10, restart_on_error=True)


class QuoteBook:
    def __init__(self, *, stale_ms: int = 3000, clock: Callable[[], int] = _now_ms,
                 connect: Callable[[], Any] = _pybit_linear,
                 rest: Callable[[str], dict[str, Quote]] | None = None) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._seen: dict[str, int] = {}
        self._lock = threading.Lock()
        self._watched: set[str] = set()
        self._stale = stale_ms
        self._clock = clock
        self._connect = connect
        self._rest = rest or (lambda symbol: fetch_public_tickers(MAINNET_REST, symbol))
        self._ws: Any = None
        self.rest_fallbacks = 0

    # -- subscriptions -------------------------------------------------------------------------------------------

    def watch(self, symbols: Iterable[str]) -> None:
        with self._lock:
            new = sorted(set(symbols) - self._watched)
            self._watched |= set(new)
        if not new:
            return
        if self._ws is None:
            self._ws = self._connect()
        for i in range(0, len(new), SUBSCRIBE_BATCH):
            self._ws.ticker_stream(symbol=new[i:i + SUBSCRIBE_BATCH], callback=self.on_message)

    def _watch_quietly(self, symbols: list[str]) -> None:
        try:
            self.watch(symbols)
        except Exception:  # noqa: BLE001 - the stream is an optimisation: REST stays correct without it
            with self._lock:
                self._watched -= set(symbols)

    @property
    def watched(self) -> frozenset[str]:
        return frozenset(self._watched)

    def on_message(self, msg: dict[str, Any]) -> None:
        """A `tickers.<symbol>` frame: a snapshot replaces the row, a delta carries only the fields that changed."""
        data = msg.get("data") or {}
        symbol = data.get("symbol")
        if not symbol:
            return
        with self._lock:
            row = self._rows.get(symbol)
            if row is None or msg.get("type") == "snapshot":
                row = self._rows[symbol] = {}
            row.update({k: v for k, v in data.items() if v not in (None, "")})
            self._seen[symbol] = self._clock()

    # -- reads ---------------------------------------------------------------------------------------------------

    def get(self, symbol: str) -> Quote | None:
        """The live quote, or None when the symbol is not streamed (yet) or has been quiet for too long."""
        with self._lock:
            row = self._rows.get(symbol)
            seen = self._seen.get(symbol)
            if row is None or seen is None or self._clock() - seen > self._stale:
                return None
            return parse_quote(row)

    def quote(self, symbol: str) -> Quote:
        """Live if possible; else one REST call (and the symbol is streamed from now on)."""
        q = self.get(symbol)
        if q is not None:
            return q
        self.rest_fallbacks += 1
        q = self._rest(symbol).get(symbol)
        # streamed from now on -- in the background: subscribing waits for the socket, an order never does
        threading.Thread(target=self._watch_quietly, args=([symbol],), daemon=True).start()
        if q is None:
            from loco_copy.brokers.base import BrokerError  # noqa: PLC0415

            raise BrokerError(f"no quote for {symbol}")
        return q

    def fresh(self, symbols: Iterable[str]) -> dict[str, Quote]:
        out = {}
        for s in symbols:
            q = self.get(s)
            if q is not None:
                out[s] = q
        return out

    def top_symbols(self, n: int) -> list[str]:
        """The n most traded linear symbols (24h turnover): watched from the start, so a leader's first trade on a
        usual coin never waits for REST."""
        import httpx  # noqa: PLC0415

        rows = httpx.get(f"{MAINNET_REST}/v5/market/tickers", params={"category": "linear"}, timeout=10).json()
        rows = rows.get("result", {}).get("list", [])
        rows.sort(key=lambda r: float(r.get("turnover24h") or 0), reverse=True)
        return [r["symbol"] for r in rows[:n] if str(r.get("symbol", "")).endswith("USDT")]

    def stats(self) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            fresh = sum(1 for s in self._watched if now - self._seen.get(s, 0) <= self._stale)
        return {"watched": len(self._watched), "fresh": fresh, "rest_fallbacks": self.rest_fallbacks,
                "connected": self._ws is not None}
