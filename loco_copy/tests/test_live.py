"""botup.md §V11: the real-time path -- the feed's silence watchdog and bbo subscriptions, the simulator's live
book-top prices and throttled writes, and the dashboard's Server-Sent Events stream."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import httpx
import pytest
import uvicorn

from loco_copy import feed as feed_mod
from loco_copy.auth import hash_password
from loco_copy.feed import HyperliquidFeed
from loco_copy.notify import Notifier
from loco_copy.portfolio import PortfolioSim
from loco_copy.store import Store
from loco_copy.web import create_app, live_snapshot
from tests.conftest import T0, make_settings
from tests.test_portfolio import LOCO, FakeClient, raw


def make(tmp_path: Any, now: list[int]) -> tuple[PortfolioSim, Store]:
    s = make_settings(tmp_path, portfolio_wallets=f"loco:{LOCO}:1")
    store = Store(s.db_path)
    return PortfolioSim(s, store, Notifier(store, echo=False), FakeClient(), clock=lambda: now[0]), store  # type: ignore[arg-type]


async def test_real_price_is_the_book_top_buy_at_ask_sell_at_bid(tmp_path):
    now = [T0 - 1000]
    sim, store = make(tmp_path, now)
    await sim.start()
    now[0] = T0
    sim.on_mids({"mids": {"BTC": "100"}})
    book = [{"px": "100.9", "sz": "1", "n": 1}, {"px": "101.1", "sz": "1", "n": 1}]
    sim.on_bbo({"coin": "BTC", "time": T0, "bbo": book})
    assert sim.mids["BTC"][0] == pytest.approx(101.0)  # the bbo mid marks the coin ...
    sim.on_mids({"mids": {"BTC": "99"}})
    assert sim.mids["BTC"][0] == pytest.approx(101.0)  # ... and a 5 s old allMids does not overwrite it
    now[0] = T0 + 500
    await sim.on_raw_fills(LOCO, [{**raw(400, "BTC", "B", 100, 10, 0, 1), "_recv": T0 + 450}], False)
    real = sim.ledgers["real:10000"]
    assert real.P["loco|BTC"]["a"] == pytest.approx(101.1)  # bought at the ask
    assert store.pf_events_since(0)[0]["detected_at_ms"] == T0 + 450  # stamped when the frame arrived
    now[0] = T0 + 600
    await sim.on_raw_fills(LOCO, [{**raw(550, "BTC", "A", 100, 10, 10, 2), "_recv": T0 + 600}], False)
    closed = store.pf_trades_since(0, ledger="real:10000")
    assert closed[0]["exit_px"] == pytest.approx(100.9)  # sold at the bid
    now[0] = T0 + 10_000  # the book top is stale now: back to the mid
    sim.on_mids({"mids": {"ETH": "10"}})
    assert sim.bbo_coins() >= {"BTC", "ETH", "SOL", "HYPE"}  # traded + hot coins are priced live


async def test_marks_write_the_view_and_state_on_their_own_clocks(tmp_path):
    now = [T0 - 1000]
    sim, _ = make(tmp_path, now)
    await sim.start()
    writes = {"rt": 0, "st": 0}
    orig_rt, orig_save = sim.write_runtime, sim.save
    sim.write_runtime = lambda: (writes.__setitem__("rt", writes["rt"] + 1), orig_rt())[1]  # type: ignore[method-assign]
    sim.save = lambda: (writes.__setitem__("st", writes["st"] + 1), orig_save())[1]  # type: ignore[method-assign]
    for i in range(10):  # ten one-second marks
        now[0] = T0 + i * 1000
        await sim.mark_once(force=False)
    assert writes["rt"] == 5 and writes["st"] == 1  # view every 2 s, state every 15 s


async def test_silent_socket_is_dropped_and_bbo_is_synced(monkeypatch):
    sent: list[dict[str, Any]] = []

    class Silent:
        async def send(self, m: str) -> None:
            sent.append(json.loads(m))

        async def recv(self) -> str:
            await asyncio.sleep(3600)
            return ""

        async def __aenter__(self) -> Silent:
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

    errors: list[str] = []
    coins = {"BTC", "ETH"}
    feed = HyperliquidFeed(ws_url="wss://x", users={}, client=None, on_frame=lambda: None,  # type: ignore[arg-type]
                           last_seen_ms=lambda _u: None, on_error=errors.append, bbo_coins=lambda: set(coins))
    monkeypatch.setattr(feed_mod, "WS_SILENCE_SECONDS", 0.05)
    monkeypatch.setattr(feed_mod.websockets, "connect", lambda *a, **k: Silent())
    task = asyncio.create_task(feed.run())
    await asyncio.sleep(0.2)
    task.cancel()
    assert errors and "no frame" in errors[0] and feed.reconnects >= 1
    subs = {m["subscription"]["coin"] for m in sent
            if m.get("method") == "subscribe" and m["subscription"]["type"] == "bbo"}
    assert subs == {"BTC", "ETH"}
    coins.discard("ETH")
    await feed.sync_bbo(Silent())
    assert sent[-1] == {"method": "unsubscribe", "subscription": {"type": "bbo", "coin": "ETH"}}


def test_sse_stream_pushes_changes(tmp_path):
    s = make_settings(tmp_path, api_cookie_secure=False, dashboard_origins="http://localhost:3000")
    store = Store(s.db_path)
    store.upsert_user("sami", "Sami", hash_password("correct-horse"))
    store.set_state("portfolio_runtime", {"updated_ms": 1, "capitals": [10000]})
    assert live_snapshot(store)["rt"] == 1
    config = uvicorn.Config(create_app(s), host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    while not server.started:
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base, headers={"origin": "http://localhost:3000"}) as c:
            assert c.get("/api/live").status_code == 401
            c.post("/api/auth/login", json={"username": "sami", "password": "correct-horse"})
            got: list[dict[str, Any]] = []
            with c.stream("GET", "/api/live", timeout=10) as r:
                assert r.headers["content-type"].startswith("text/event-stream")
                for line in r.iter_lines():
                    if line.startswith("data: "):
                        got.append(json.loads(line[6:]))
                        if len(got) == 1:
                            store.set_state("portfolio_runtime", {"updated_ms": 2, "capitals": [10000]})
                        else:
                            break
        assert [g["rt"] for g in got] == [1, 2]
    finally:
        server.should_exit = True
        th.join(5)
