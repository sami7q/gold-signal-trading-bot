"""Speed paths: live prices in memory, the feed never blocked by a handler, an open's reads in parallel,
reconciliation's network reads outside the lock, fills confirmed without long sleeps."""

from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal

from loco_copy.brokers.bybit import BybitBroker
from loco_copy.brokers.bybit_stream import QuoteBook
from loco_copy.brokers.virtual import VirtualAccount
from loco_copy.constants import Decision
from loco_copy.feed import HyperliquidFeed
from loco_copy.models import Quote
from tests.conftest import INSTRUMENTS, F, make_settings
from tests.test_bybit_adapter import FakeSession


class FakeTickerSocket:
    """Stands in for pybit's linear WebSocket: records subscriptions, never touches the network."""

    def __init__(self) -> None:
        self.topics: list[str] = []

    def ticker_stream(self, symbol: list[str], callback: object) -> None:
        self.topics += [f"tickers.{s}" for s in symbol]


def tick(book: QuoteBook, symbol: str, kind: str = "snapshot", **fields: str) -> None:
    book.on_message({"topic": f"tickers.{symbol}", "type": kind, "data": {"symbol": symbol, **fields}})


def test_quote_book_merges_snapshot_and_deltas_and_goes_stale():
    now = [1_000]
    book = QuoteBook(clock=lambda: now[0], connect=FakeTickerSocket, rest=lambda s: {})
    tick(book, "BTCUSDT", bid1Price="99", ask1Price="101", markPrice="100")
    tick(book, "BTCUSDT", "delta", ask1Price="102")  # a delta carries only what changed
    q = book.get("BTCUSDT")
    assert q == Quote(Decimal(99), Decimal(102), Decimal(100))
    now[0] += 3_001
    assert book.get("BTCUSDT") is None  # quiet for too long: not trusted any more


def test_quote_book_falls_back_to_rest_once_then_streams():
    calls = []
    book = QuoteBook(connect=FakeTickerSocket, rest=lambda s: calls.append(s) or {s: Quote(Decimal(1), Decimal(2),
                                                                                             Decimal(1))})
    assert book.quote("HYPEUSDT").ask == 2 and calls == ["HYPEUSDT"]
    for _ in range(50):  # the subscription is made in the background, never on the order's path
        if "HYPEUSDT" in book.watched:
            break
        time.sleep(0.01)
    assert "HYPEUSDT" in book.watched and book.rest_fallbacks == 1
    tick(book, "HYPEUSDT", bid1Price="40", ask1Price="40.1", markPrice="40")
    assert book.quote("HYPEUSDT").ask == Decimal("40.1") and calls == ["HYPEUSDT"]


def test_bybit_broker_takes_quotes_from_the_stream():
    b = BybitBroker(api_key="k", api_secret="s", env="demo", session=FakeSession(), poll_seconds=0)
    b.quotes = QuoteBook(connect=FakeTickerSocket, rest=lambda s: {})
    tick(b.quotes, "HYPEUSDT", bid1Price="39.9", ask1Price="40.1", markPrice="40")
    assert b.quote("HYPEUSDT").bid == Decimal("39.9") and not b._s.calls


async def test_the_feed_keeps_reading_while_a_leader_is_being_copied():
    started, released, order = asyncio.Event(), asyncio.Event(), []

    def handler(label):
        async def handle(fills, _history):
            order.append((label, fills[0].tid))
            if label == "a":
                started.set()
                await released.wait()  # lane a is busy placing its order ...

        return handle

    a, b = "0x" + "a" * 40, "0x" + "b" * 40
    feed = HyperliquidFeed(ws_url="wss://x", users={a: handler("a"), b: handler("b")}, client=None,  # type: ignore[arg-type]
                           on_frame=lambda: None, last_seen_ms=lambda _u: None)
    feed._start_workers()

    def frame(user, tid):
        return json.dumps({"channel": "userFills", "data": {"user": user, "fills": [
            {"coin": "BTC", "side": "B", "px": "1", "sz": "1", "startPosition": "0", "time": tid, "tid": tid}]}})

    await feed._dispatch(frame(a, 1))
    await started.wait()
    await feed._dispatch(frame(b, 2))  # ... and lane b's fill is still read and handled meanwhile
    await feed._dispatch(frame(a, 3))
    for _ in range(20):
        await asyncio.sleep(0)
    assert order == [("a", 1), ("b", 2)]
    released.set()
    for _ in range(20):
        await asyncio.sleep(0)
    assert order == [("a", 1), ("b", 2), ("a", 3)]  # each leader's own fills stay in order
    for w in feed._workers:
        w.cancel()


class SlowAccount(VirtualAccount):
    """Every read takes one 'round trip'."""

    RTT = 0.15

    def total_equity(self):
        time.sleep(self.RTT)
        return super().total_equity()

    def account(self):
        time.sleep(self.RTT)
        return super().account()

    def positions(self):
        time.sleep(self.RTT)
        return super().positions()


async def test_an_open_reads_everything_in_one_round_trip(cap20):
    h = cap20()
    slow = SlowAccount(name="exchange", instruments=dict(INSTRUMENTS), quote=lambda s: h.prices[s],
                       start_equity=Decimal(10000), fee_rate=Decimal(0), leverage=3)
    h.engine.real.broker = h.engine._broker = slow
    h.engine._run = asyncio.to_thread  # real threads, as in production
    t = time.perf_counter()
    assert await h.fill(F(0, "HYPE", "B", 40, 1000, 0)) == [Decision.COPIED]
    elapsed = time.perf_counter() - t
    # equity, account and exposure (3 reads x 0.15 s) at once, not one after the other
    assert elapsed < 3 * SlowAccount.RTT, elapsed
    assert slow.position("HYPEUSDT").qty == 24


async def test_reconcile_reads_the_exchange_before_taking_the_lock(cap20):
    h = cap20()
    await h.fill(F(0, "HYPE", "B", 40, 1000, 0))
    await h.recon.prefetch()
    assert h.recon._pre is not None
    h.engine.real.broker.positions = lambda: (_ for _ in ()).throw(AssertionError("read again under the lock"))
    await h.recon.run_once()  # uses what prefetch read
    assert h.recon._pre is None


def test_fill_confirmation_polls_fast():
    assert BybitBroker(api_key="k", api_secret="s", env="demo", session=FakeSession())._poll <= 0.05


def test_settings_still_parse(tmp_path):
    assert make_settings(tmp_path).fill_poll_seconds == 2
