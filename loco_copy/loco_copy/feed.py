"""The copied leaders' trades from Hyperliquid (botup.md §5.1, v3 §5.4). Public API, no keys.

- One WebSocket, `userFills` for every leader. Pings every 30 s (the pong counts as a frame for the latency breaker).
  Reconnects with backoff 1 s -> 30 s cap; after every (re)connect the REST backfill fills any gap.
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

FillHandler = Callable[[LeaderFill, bool], Awaitable[None]]  # (fill, is_history_frame)
FrameHandler = Callable[[], None]

WEIGHT_BUDGET_PER_MIN = 900


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
                 on_unknown_user: Callable[[str], None] | None = None) -> None:
        self._ws_url = ws_url
        self._handlers = {address.lower(): handler for address, handler in users.items()}
        self._client = client
        self._on_frame = on_frame
        self._last_seen = last_seen_ms
        self._on_connected = on_connected
        self._on_error = on_error
        self._on_unknown_user = on_unknown_user
        self.connected = False
        self.reconnects = 0

    async def run(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self._ws_url, ping_interval=None, close_timeout=5,
                                              max_size=50 * 1024 * 1024) as ws:
                    for user in self._handlers:
                        await ws.send(json.dumps({"method": "subscribe",
                                                  "subscription": {"type": "userFills", "user": user}}))
                    self.connected = True
                    if self._on_connected:
                        self._on_connected()
                    await self._backfill()
                    backoff = 1.0
                    pinger = asyncio.create_task(self._ping(ws))
                    try:
                        async for message in ws:
                            self._on_frame()
                            await self._dispatch(message)
                    finally:
                        pinger.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - any failure -> reconnect with backoff
                if self._on_error:
                    self._on_error(f"{type(exc).__name__}: {exc}")
            self.connected = False
            self.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _ping(self, ws: Any) -> None:
        while True:
            await asyncio.sleep(20)  # well inside the 60 s latency breaker, and Hyperliquid's 60 s idle timeout
            await ws.send(json.dumps({"method": "ping"}))

    async def _backfill(self) -> None:
        """Per leader, from the last fill that leader's lane saw."""
        for user, handler in self._handlers.items():
            last = self._last_seen(user)
            if last is None:
                continue
            for fill in await self._client.fills_since(user, last + 1):
                await handler(fill, False)

    async def _dispatch(self, message: str | bytes) -> None:
        msg = json.loads(message)
        if msg.get("channel") != "userFills":
            return
        data = msg.get("data") or {}
        user = str(data.get("user") or "").lower()
        handler = self._handlers.get(user)
        if handler is None and not user and len(self._handlers) == 1:
            handler = next(iter(self._handlers.values()))  # a frame without `user` can only be the one leader
        if handler is None:
            if self._on_unknown_user:
                self._on_unknown_user(user or "?")
            return
        is_history = bool(data.get("isSnapshot"))
        received = now_ms()
        fills = [f for f in (parse_fill(x, received) for x in data.get("fills", []) or []) if f is not None]
        for fill in chain_order(fills):
            await handler(fill, is_history)
