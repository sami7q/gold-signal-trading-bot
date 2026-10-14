"""The copied leaders' trades from Hyperliquid (botup.md §5.1, v3 §5.4). Public API, no keys.

- One WebSocket, `userFills` for every leader. Pings every 10 s (the pong counts as a frame for the latency breaker).
  A socket that goes silent for WS_SILENCE_SECONDS is dropped at once (a half-open TCP connection would otherwise
  hang for minutes); reconnects with backoff 0.5 s -> 15 s cap; after every (re)connect the REST backfill fills any
  gap. (botup.md §V11: real-time.)
- `bbo` (best bid / offer, pushed every ~0.1 s) for the coins the simulator wants priced live -- `allMids` is
  pushed only every ~5 s. The set is re-synced every BBO_SYNC_SECONDS on the open socket (subscribe / unsubscribe).
- The first WS frame (`isSnapshot: true`) is history: the app stores it and only processes fills newer than
  what it has already seen.
- `clearinghouseState` snapshots are the safety net for the tracker (and the leader breaker).
- Spot (`@...`, `X/USDC`) and builder-dex (`dex:COIN`) coins are dropped here.
- A weight budget keeps us well under Hyperliquid's 1200 weight/min.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import httpx
import websockets

from loco_copy.models import LeaderFill, LeaderSnapshot
from loco_copy.tracker import chain_order, is_main_perp

FillHandler = Callable[[list[LeaderFill], bool], Awaitable[None]]  # (one delivery in chain order, is_history)
FrameHandler = Callable[[], None]
#: raw `userFills` items of one address, every dex (the portfolio simulator, botup.md §V6), and is_history
RawFillHandler = Callable[[str, list[dict[str, Any]], bool], Awaitable[None]]

WEIGHT_BUDGET_PER_MIN = 900
PING_SECONDS = 10
WS_SILENCE_SECONDS = 25.0
BBO_SYNC_SECONDS = 2.0
#: Hyperliquid allows 1000 subscriptions per IP; the bbo set is capped well below it
MAX_BBO = 120


def now_ms() -> int:
    return int(time.time() * 1000)


def parse_fill(raw: dict[str, Any], received_at_ms: int) -> LeaderFill | None:
    coin = str(raw.get("coin", ""))
    if not is_main_perp(coin):
        return None
    side = raw.get("side")
    if side not in ("B", "A"):
        return None
    return LeaderFill(
        coin=coin, side=side, px=Decimal(str(raw["px"])), sz=Decimal(str(raw["sz"])),
        start_position=Decimal(str(raw.get("startPosition") or "0")), time_ms=int(raw["time"]),
        tid=int(raw["tid"]), oid=int(raw["oid"]) if raw.get("oid") is not None else None,
        received_at_ms=received_at_ms)


def parse_snapshot(raw: dict[str, Any], taken_at_ms: int) -> LeaderSnapshot:
    positions: dict[str, Decimal] = {}
    entries: dict[str, Decimal] = {}
    for ap in raw.get("assetPositions", []) or []:
        p = ap.get("position", {}) or {}
        coin = str(p.get("coin", ""))
        szi = Decimal(str(p.get("szi") or "0"))
        if is_main_perp(coin) and szi != 0:
            positions[coin] = szi
            if p.get("entryPx") is not None:
                entries[coin] = Decimal(str(p["entryPx"]))
    summary = raw.get("marginSummary") or {}
    return LeaderSnapshot(positions=positions, entry_px=entries,
                          account_value=Decimal(str(summary.get("accountValue") or "0")), taken_at_ms=taken_at_ms)


class HyperliquidClient:
    """REST /info with a simple per-minute weight budget."""

    def __init__(self, info_url: str, client: httpx.AsyncClient | None = None) -> None:
        self._url = info_url
        self._http = client or httpx.AsyncClient(timeout=15)
        self._spent: deque[tuple[float, int]] = deque()

    async def close(self) -> None:
        await self._http.aclose()

    async def _budget(self, weight: int) -> None:
        while True:
            now = time.monotonic()
            while self._spent and now - self._spent[0][0] > 60:
                self._spent.popleft()
            if sum(w for _, w in self._spent) + weight <= WEIGHT_BUDGET_PER_MIN:
                self._spent.append((now, weight))
                return
            await asyncio.sleep(1)

    async def info(self, body: dict[str, Any], weight: int) -> Any:
        await self._budget(weight)
        r = await self._http.post(self._url, json=body)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and len(data) > 20:
            self._spent.append((time.monotonic(), len(data) // 20))
        return data

    async def portfolio_equity(self, user: str) -> Decimal | None:
        """The leader's account value W for cap20 sizing: last point of `portfolio` -> "day" ->
        accountValueHistory (botup.md v4 §2.4). None when Hyperliquid has no history for the wallet."""
        raw = await self.info({"type": "portfolio", "user": user}, 20)
        periods = {name: data for name, data in raw} if isinstance(raw, list) else {}
        history = (periods.get("day") or {}).get("accountValueHistory") or []
        return Decimal(str(history[-1][1])) if history else None

    async def snapshot(self, user: str) -> LeaderSnapshot:
        taken = now_ms()
        raw = await self.info({"type": "clearinghouseState", "user": user}, 2)
        return parse_snapshot(raw, taken)

    async def fills_since(self, user: str, start_ms: int) -> list[LeaderFill]:
        out: list[LeaderFill] = []
        t = start_ms
        for _ in range(20):
            page = await self.info({"type": "userFillsByTime", "user": user, "startTime": t}, 20)
            if not page:
                break
            received = now_ms()
            out += [f for f in (parse_fill(x, received) for x in page) if f is not None]
            if len(page) < 2000:
                break
            t = max(int(x["time"]) for x in page) + 1
        seen: set[int] = set()
        uniq = []
        for f in out:
            if f.tid not in seen:
                seen.add(f.tid)
                uniq.append(f)
        return chain_order(uniq)

    async def all_mids(self) -> dict[str, Decimal]:
        raw = await self.info({"type": "allMids"}, 2)
        return {k: Decimal(str(v)) for k, v in raw.items() if is_main_perp(k)}

    async def meta(self) -> dict[str, Any]:
        data: dict[str, Any] = await self.info({"type": "meta"}, 20)
        return data


class HyperliquidFeed:
    """One WebSocket for every copied leader (botup.md v3 §5.4): one `userFills` subscription per address, and
    each frame is routed by its `user` field to that leader's handler. Frames for an address we did not
    subscribe to are dropped (and reported)."""

    def __init__(self, *, ws_url: str, users: dict[str, FillHandler], client: HyperliquidClient,
                 on_frame: FrameHandler, last_seen_ms: Callable[[str], int | None],
                 on_connected: Callable[[], None] | None = None,
                 on_error: Callable[[str], None] | None = None,
                 on_unknown_user: Callable[[str], None] | None = None,
                 raw_users: dict[str, RawFillHandler] | None = None, mid_dexes: list[str] | None = None,
                 on_mids: Callable[[dict[str, Any]], None] | None = None,
                 on_bbo: Callable[[dict[str, Any]], None] | None = None,
                 bbo_coins: Callable[[], set[str]] | None = None) -> None:
        self._ws_url = ws_url
        self._handlers = {address.lower(): handler for address, handler in users.items()}
        self._raw = {address.lower(): handler for address, handler in (raw_users or {}).items()}
        self._mid_dexes = list(mid_dexes or [])  # "" = main dex
        self._on_mids = on_mids
        self._on_bbo = on_bbo
        self._bbo_coins = bbo_coins
        self._bbo_subscribed: set[str] = set()
        self._client = client
        self._on_frame = on_frame
        self._last_seen = last_seen_ms
        self._on_connected = on_connected
        self._on_error = on_error
        self._on_unknown_user = on_unknown_user
        self.connected = False
        self.reconnects = 0
        self._queues: dict[str, asyncio.Queue[tuple[list[LeaderFill], bool]]] = {}
        self._raw_queues: dict[str, asyncio.Queue[tuple[list[dict[str, Any]], bool]]] = {}
        self._workers: list[asyncio.Task[None]] = []

    def _start_workers(self) -> None:
        """One worker per leader, in arrival order: the socket keeps reading (and time-stamping) frames while a
        leader's fill is being copied -- another leader's fill is never detected late because of it."""
        if self._workers:
            return
        for user, handler in self._handlers.items():
            queue: asyncio.Queue[tuple[list[LeaderFill], bool]] = asyncio.Queue()
            self._queues[user] = queue
            self._workers.append(asyncio.create_task(self._work(queue, handler)))
        for user, raw_handler in self._raw.items():
            raw_queue: asyncio.Queue[tuple[list[dict[str, Any]], bool]] = asyncio.Queue()
            self._raw_queues[user] = raw_queue
            self._workers.append(asyncio.create_task(self._work_raw(user, raw_queue, raw_handler)))

    async def _work_raw(self, user: str, queue: asyncio.Queue[tuple[list[dict[str, Any]], bool]],
                        handler: RawFillHandler) -> None:
        while True:
            raws, is_history = await queue.get()
            try:
                await handler(user, raws, is_history)
            except Exception as exc:  # noqa: BLE001 - the simulator must never stop the feed
                if self._on_error:
                    self._on_error(f"raw handler: {type(exc).__name__}: {exc}")

    async def _work(self, queue: asyncio.Queue[tuple[list[LeaderFill], bool]], handler: FillHandler) -> None:
        while True:
            fills, is_history = await queue.get()
            try:
                await handler(fills, is_history)
            except Exception as exc:  # noqa: BLE001 - one bad batch must not stop this leader's copying
                if self._on_error:
                    self._on_error(f"handler: {type(exc).__name__}: {exc}")

    async def _deliver(self, user: str, handler: FillHandler, fills: list[LeaderFill], is_history: bool) -> None:
        queue = self._queues.get(user)
        if queue is None:
            await handler(fills, is_history)  # not running (tests): straight through
        else:
            queue.put_nowait((fills, is_history))

    async def run(self) -> None:
        self._start_workers()
        backoff = 0.5
        while True:
            try:
                async with websockets.connect(self._ws_url, ping_interval=None, close_timeout=2, open_timeout=10,
                                              max_size=50 * 1024 * 1024) as ws:
                    self._bbo_subscribed = set()
                    for user in dict.fromkeys([*self._handlers, *self._raw]):
                        await ws.send(json.dumps({"method": "subscribe",
                                                  "subscription": {"type": "userFills", "user": user}}))
                    for dex in self._mid_dexes:
                        sub: dict[str, Any] = {"type": "allMids"} | ({"dex": dex} if dex else {})
                        await ws.send(json.dumps({"method": "subscribe", "subscription": sub}))
                    self.connected = True
                    if self._on_connected:
                        self._on_connected()
                    await self.sync_bbo(ws)
                    await self._backfill()
                    backoff = 0.5
                    helpers = [asyncio.create_task(self._ping(ws)), asyncio.create_task(self._bbo_loop(ws))]
                    try:
                        while True:
                            try:  # a silent socket is a dead socket: drop it now, not after the TCP timeout
                                message = await asyncio.wait_for(ws.recv(), timeout=WS_SILENCE_SECONDS)
                            except TimeoutError:
                                raise ConnectionError(f"no frame for {WS_SILENCE_SECONDS:.0f} s") from None
                            self._on_frame()
                            await self._dispatch(message)
                    finally:
                        for h in helpers:
                            h.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure -> reconnect with backoff
                if self._on_error:
                    self._on_error(f"{type(exc).__name__}: {exc}")
            self.connected = False
            self.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 15.0)

    async def _ping(self, ws: Any) -> None:
        while True:
            await asyncio.sleep(PING_SECONDS)  # the pong keeps a quiet socket inside WS_SILENCE_SECONDS
            await ws.send(json.dumps({"method": "ping"}))

    async def sync_bbo(self, ws: Any) -> None:
        """Subscribe the coins the simulator wants priced live, unsubscribe the ones it no longer needs."""
        if self._bbo_coins is None:
            return
        wanted = set(sorted(self._bbo_coins())[:MAX_BBO])
        for coin in sorted(wanted - self._bbo_subscribed):
            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "bbo", "coin": coin}}))
        for coin in sorted(self._bbo_subscribed - wanted):
            await ws.send(json.dumps({"method": "unsubscribe", "subscription": {"type": "bbo", "coin": coin}}))
        self._bbo_subscribed = wanted

    async def _bbo_loop(self, ws: Any) -> None:
        while True:
            await asyncio.sleep(BBO_SYNC_SECONDS)
            await self.sync_bbo(ws)

    async def _backfill(self) -> None:
        """Per leader, from the last fill that leader's lane saw."""
        for user, handler in self._handlers.items():
            last = self._last_seen(user)
            if last is None:
                continue
            fills = await self._client.fills_since(user, last + 1)
            if fills:
                await self._deliver(user, handler, fills, False)  # same queue: never interleaved with live frames

    async def _dispatch(self, message: str | bytes) -> None:
        received = now_ms()  # stamped on arrival: queueing behind another wallet's fill does not count as our lag
        msg = json.loads(message)
        channel = msg.get("channel")
        if channel == "bbo":
            if self._on_bbo:
                self._on_bbo(msg.get("data") or {})
            return
        if channel == "allMids":
            if self._on_mids:
                self._on_mids(msg.get("data") or {})
            return
        if channel != "userFills":
            return
        data = msg.get("data") or {}
        user = str(data.get("user") or "").lower()
        raw_queue = self._raw_queues.get(user)
        raw_handler = self._raw.get(user)
        if raw_handler is not None:
            raws = [{**x, "_recv": received} for x in data.get("fills", []) or []]
            if raws:
                if raw_queue is None:
                    await raw_handler(user, raws, bool(data.get("isSnapshot")))
                else:
                    raw_queue.put_nowait((raws, bool(data.get("isSnapshot"))))
            if user not in self._handlers:
                return
        handler = self._handlers.get(user)
        if handler is None and not user and len(self._handlers) == 1:
            handler = next(iter(self._handlers.values()))  # a frame without `user` can only be the one leader
        if handler is None:
            if self._on_unknown_user:
                self._on_unknown_user(user or "?")
            return
        is_history = bool(data.get("isSnapshot"))
        fills = [f for f in (parse_fill(x, received) for x in data.get("fills", []) or []) if f is not None]
        if fills:
            await self._deliver(user, handler, chain_order(fills), is_history)
