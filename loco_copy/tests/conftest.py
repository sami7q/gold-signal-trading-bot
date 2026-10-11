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
    base: dict[str, Any] = dict(copy_enabled=True, copy_observe_only=False, data_dir=tmp, _env_file=None)
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
