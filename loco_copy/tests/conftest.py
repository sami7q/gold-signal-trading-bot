from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from loco_copy.brokers.paper import PaperBroker
from loco_copy.config import Settings
from loco_copy.engine import CopyEngine, run_inline
from loco_copy.models import InstrumentInfo, LeaderFill
from loco_copy.notify import Notifier
from loco_copy.reconcile import Reconciler
from loco_copy.risk import RiskGuard
from loco_copy.store import Store
from loco_copy.symbols import SymbolMapper
from loco_copy.tracker import LeaderTracker

T0 = 1_790_000_000_000


def F(t: int, coin: str, side: str, px: Any, sz: Any, start: Any, tid: int | None = None,
      received: int | None = None) -> LeaderFill:
    """A Loco fill. `t` is an offset in ms from T0."""
    return LeaderFill(coin=coin, side=side, px=Decimal(str(px)), sz=Decimal(str(sz)),  # type: ignore[arg-type]
                      start_position=Decimal(str(start)), time_ms=T0 + t, tid=tid if tid is not None else T0 + t,
                      oid=None, received_at_ms=T0 + (received if received is not None else t))


def make_settings(tmp: Path, **overrides: Any) -> Settings:
    # allow_execute: the execution gate is open in the harness; tests of the gate close it explicitly
    base: dict[str, Any] = dict(copy_enabled=True, copy_observe_only=False, allow_execute=True, data_dir=tmp,
                                _env_file=None)
    base.update(overrides)
    return Settings(**base)


INSTRUMENTS = {
    "HYPEUSDT": InstrumentInfo("HYPEUSDT", Decimal("0.01"), Decimal("0.01"), Decimal("0.001"), 50),
    "BTCUSDT": InstrumentInfo("BTCUSDT", Decimal("0.001"), Decimal("0.001"), Decimal("0.1"), 100),
    "1000PEPEUSDT": InstrumentInfo("1000PEPEUSDT", Decimal("1"), Decimal("1"), Decimal("0.0000001"), 50),
}


@dataclass
class Harness:
    settings: Settings
    store: Store
    paper: PaperBroker
    tracker: LeaderTracker
    risk: RiskGuard
    engine: CopyEngine
    recon: Reconciler
    now: list[int]

    def tick(self, ms: int) -> None:
        self.now[0] = T0 + ms

    async def fill(self, f: LeaderFill) -> list[Any]:
        self.now[0] = f.received_at_ms
        events, _ = self.tracker.on_fill(f)
        return [await self.engine.handle(e) for e in events]


@pytest.fixture
def harness(tmp_path: Path) -> Any:
    def build(**overrides: Any) -> Harness:
        s = make_settings(tmp_path, **overrides)
        now = [T0]

        def clock() -> int:
            return now[0]

        store = Store(":memory:")
        n = Notifier(store, echo=False)
        paper = PaperBroker(instruments=dict(INSTRUMENTS), start_equity=Decimal("10000"), fee_rate=Decimal(0),
                            slippage_bps=Decimal(0), leverage=s.leverage,
                            maintenance_margin_rate=s.maintenance_margin_rate,
                            clock=clock)
        for sym, px in (("HYPEUSDT", "40"), ("BTCUSDT", "100000"), ("1000PEPEUSDT", "0.01")):
            paper.set_price(sym, Decimal(px))
        tracker = LeaderTracker()
        risk = RiskGuard(store, s, n, monitor_feed=False, clock=clock)
        mapper = SymbolMapper(INSTRUMENTS)
        engine = CopyEngine(store=store, broker=paper, settings=s, risk=risk, notifier=n, mapper=mapper,
                            tracker=tracker, instruments=dict(INSTRUMENTS), clock=clock, run_sync=run_inline)
        recon = Reconciler(store=store, broker=paper, settings=s, notifier=n, tracker=tracker, risk=risk,
                           engine=engine, run_sync=run_inline, clock=clock)
        return Harness(s, store, paper, tracker, risk, engine, recon, now)

    return build


# -- cap20_cross (botup.md v4 / v5) -----------------------------------------------------------------------------

W0 = Decimal("1000000")  # the leader's equity in the cap20 tests: R = K x 10,000 / 1,000,000


@dataclass
class Cap20Harness:
    settings: Any
    store: Store
    exchange: Any  # a VirtualAccount standing in for the Bybit account
    virtual: Any  # the observe ledger
    tracker: LeaderTracker
    risk: RiskGuard
    engine: Any
    recon: Any
    now: list[int]
    prices: dict[str, Any]
    feed_alive: bool = True

    def tick(self, ms: int) -> None:
        self.now[0] = T0 + ms

    def price(self, symbol: str, px: Any, spread: Any = 0) -> None:
        from loco_copy.models import Quote

        p, half = Decimal(str(px)), Decimal(str(spread)) / 2
        self.prices[symbol] = Quote(bid=p - half, ask=p + half, mark=p)

    def leader_equity(self, value: Any, label: str = "loco") -> None:
        self.store.set_state(f"leader_equity:{label}", {"value": str(value), "taken_at_ms": self.now[0]})

    def copy(self, coin: str, episode: int = 1) -> Any:
        return self.store.get_copy(coin, episode, leader=self.settings.lane)

    def last_event(self) -> Any:
        return self.store.events_since(0)[-1]

    async def fill(self, f: LeaderFill) -> list[Any]:
        self.now[0] = f.received_at_ms
        if self.feed_alive:
            self.risk.on_hl_ok()
        events, _ = self.tracker.on_fill(f)
        return [await self.engine.handle(e) for e in events]


CAP20 = dict(strategy="cap20_cross", margin_mode="CROSS", follow_adds=True, leverage=3)


@pytest.fixture
def cap20(tmp_path: Path) -> Any:
    from loco_copy.brokers.virtual import VirtualAccount
    from loco_copy.cap20 import Book, Cap20Engine, Cap20Reconciler
    from loco_copy.risk import AccountGuard

    def build(*, fee: Any = 0, guard: bool = False, store: Store | None = None, virtual: Any = None,
              shared: bool = False, label: str = "loco", **overrides: Any) -> Cap20Harness:
        s = make_settings(tmp_path, **(CAP20 | overrides))
        if label != "loco":
            from loco_copy.config import LOCO_ADDRESS

            s = s.model_copy(update={"lane": label, "leader_address": LOCO_ADDRESS})
        now = [T0]
        prices: dict[str, Any] = {}

        def clock() -> int:
            return now[0]

        def quote(symbol: str) -> Any:
            return prices[symbol]

        store = store or Store(":memory:")
        n = Notifier(store, echo=False)

        def account(name: str) -> Any:
            return VirtualAccount(name=name, instruments=dict(INSTRUMENTS), quote=quote, start_equity=Decimal(10000),
                                  fee_rate=Decimal(str(fee)), leverage=s.leverage, clock=clock)

        exchange = account("exchange")
        ledger = virtual or account("virtual")
        tracker = LeaderTracker()
        risk = RiskGuard(store, s, n, clock=clock)
        dd = s.account_kill_dd_pct

        def book(broker: Any, name: str) -> Any:
            return Book(broker, name, AccountGuard(store, name, dd, n) if guard else None, shared=shared)

        engine = Cap20Engine(store=store, real=book(exchange, "exchange"), virtual=book(ledger, "virtual"),
                             settings=s, risk=risk, notifier=n, mapper=SymbolMapper(INSTRUMENTS), tracker=tracker,
                             instruments=dict(INSTRUMENTS), clock=clock, run_sync=run_inline)
        recon = Cap20Reconciler(store=store, engine=engine, settings=s, notifier=n, tracker=tracker, risk=risk,
                                run_sync=run_inline, clock=clock)
        h = Cap20Harness(s, store, exchange, ledger, tracker, risk, engine, recon, now, prices)
        for sym, px in (("HYPEUSDT", "40"), ("BTCUSDT", "100000"), ("1000PEPEUSDT", "0.01")):
            h.price(sym, px)
        h.leader_equity(W0, label)
        return h

    return build
