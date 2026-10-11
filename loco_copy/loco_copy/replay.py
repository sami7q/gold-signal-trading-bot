"""Run recorded Loco fills through the real pipeline (tracker -> rules -> engine -> reconciler) with a
PaperBroker whose take-profit / liquidation are driven by recorded candles (botup.md §4, §12).

Time is simulated: the clock is the fill being processed, so no rule ever sees a "stale" signal. Prices are
Loco's own fill prices (no slippage); fees are whatever the caller passes (the research used 0.08 % per side).
"""

from __future__ import annotations

import gzip
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from loco_copy.brokers.paper import PaperBroker
from loco_copy.config import Settings
from loco_copy.constants import CopyStatus, EventType
from loco_copy.engine import CopyEngine, run_inline
from loco_copy.models import ClosedTrade, Copy, InstrumentInfo, LeaderFill
from loco_copy.notify import Notifier
from loco_copy.reconcile import Reconciler
from loco_copy.risk import RiskGuard
from loco_copy.store import Store
from loco_copy.symbols import SymbolMapper
from loco_copy.tracker import LeaderTracker, is_main_perp

Candle = tuple[int, Decimal, Decimal, Decimal, Decimal]
CandleSource = Callable[[str, int, int], list[Candle]]  # (coin, open_ms, close_ms) -> candles


@dataclass
class ReplayResult:
    copies: list[Copy]
    decisions: dict[str, int]
    rejections: dict[str, int]
    start_equity: Decimal
    final_equity: Decimal
    max_dd: Decimal
    counts: dict[str, int] = field(default_factory=dict)


def fill_from_raw(raw: dict[str, Any]) -> LeaderFill | None:
    coin = str(raw.get("coin") or "")
    if not is_main_perp(coin) or raw.get("side") not in ("B", "A"):
        return None
    t = int(raw["time"])
    return LeaderFill(coin=coin, side=raw["side"], px=Decimal(str(raw["px"])), sz=Decimal(str(raw["sz"])),
                      start_position=Decimal(str(raw.get("startPosition") or "0")), time_ms=t,
                      tid=int(raw.get("tid") or 0), oid=raw.get("oid"), received_at_ms=t)


def load_fixture_fills(path: Path) -> list[LeaderFill]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        raw = json.load(fh)
    return [f for f in (fill_from_raw(r) for r in raw) if f is not None]


def fixture_candle_source(path: Path) -> CandleSource:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        raw: dict[str, list[list[float]]] = json.load(fh)
    table = {k: [(int(c[0]), Decimal(str(c[1])), Decimal(str(c[2])), Decimal(str(c[3])), Decimal(str(c[4])))
                 for c in v] for k, v in raw.items()}

    def source(coin: str, t0: int, _t1: int) -> list[Candle]:
        return table.get(f"{coin}|{t0}", [])

    return source


def episode_ends(fills: list[LeaderFill]) -> dict[tuple[str, int], int]:
    """Pre-pass: (coin, open time) -> time the episode ended (needed to know which candles to load)."""
    t = LeaderTracker()
    opened: dict[tuple[str, int], int] = {}
    ends: dict[tuple[str, int], int] = {}
    for f in fills:
        for ev in t.on_fill(f)[0]:
            if ev.type == EventType.OPEN:
                opened[(ev.coin, ev.episode_id)] = ev.leader_time_ms
            elif ev.type in (EventType.CLOSE, EventType.FLIP) and (ev.coin, ev.episode_id) in opened:
                ends[(ev.coin, opened[(ev.coin, ev.episode_id)])] = ev.leader_time_ms
    last = fills[-1].time_ms if fills else 0
    for key, t0 in opened.items():
        ends.setdefault((key[0], t0), last)
    return ends


async def run_replay(fills: list[LeaderFill], candles: CandleSource, settings: Settings, *,
                     start_ms: int = 0, end_ms: int | None = None, fee_rate: Decimal = Decimal("0.0008"),
                     start_equity: Decimal | None = None) -> ReplayResult:
    fills = sorted((f for f in fills if f.time_ms >= start_ms and (end_ms is None or f.time_ms <= end_ms)),
                   key=lambda f: (f.time_ms, f.tid))
    ends = episode_ends(fills)
    now = [fills[0].time_ms if fills else 0]

    def clock() -> int:
        return now[0]

    coins = sorted({f.coin for f in fills})
    tiny = Decimal("1e-10")
    instruments = {c: InstrumentInfo(c, tiny, Decimal(0), tiny, 100) for c in coins}
    equity0 = start_equity if start_equity is not None else settings.paper_start_equity
    paper = PaperBroker(instruments=instruments, start_equity=equity0, fee_rate=fee_rate, slippage_bps=Decimal(0),
                        leverage=settings.leverage, maintenance_margin_rate=settings.maintenance_margin_rate,
                        include_unrealized=False, clock=clock)
    tmp = tempfile.TemporaryDirectory()
    settings = settings.model_copy(update={"data_dir": Path(tmp.name)})
    store = Store(":memory:")
    notifier = Notifier(store, echo=False)
    tracker = LeaderTracker()
    risk = RiskGuard(store, settings, notifier, monitor_feed=False, clock=clock)
    mapper = SymbolMapper(coins, identity=True)
    engine = CopyEngine(store=store, broker=paper, settings=settings, risk=risk, notifier=notifier, mapper=mapper,
                        tracker=tracker, instruments=instruments, clock=clock, run_sync=run_inline)
    recon = Reconciler(store=store, broker=paper, settings=settings, notifier=notifier, tracker=tracker, risk=risk,
                       engine=engine, run_sync=run_inline, clock=clock)

    pending: dict[str, list[Candle]] = {}  # symbol -> candles not yet fed
    peak = equity0
    max_dd = Decimal(0)

    def track_dd() -> None:
        nonlocal peak, max_dd
        bal = paper.account().equity
        peak = max(peak, bal)
        if peak > 0:
            max_dd = max(max_dd, (peak - bal) / peak)

    async def advance(t: int) -> None:
        closed: list[ClosedTrade] = []
        for sym in list(pending):
            queue = pending[sym]
            while queue and queue[0][0] <= t:
                ts, _o, h, low, c = queue.pop(0)
                now[0] = ts
                closed += paper.on_candle(sym, ts, h, low, c)
                if paper.position(sym) is None:
                    break
            if paper.position(sym) is None or not queue:
                pending.pop(sym, None)
        now[0] = t
        if closed:
            await recon.run_once()
            track_dd()

    for f in fills:
        await advance(f.time_ms)
        now[0] = f.time_ms
        # Loco's fill is a real traded price: if it is beyond our TP or liquidation price, that level was
        # crossed (this also covers a trade that opens and closes inside one coarse candle -- the research's
        # exit-price fallback). Settle that before Loco's event is handled.
        if paper.set_price(f.coin, f.px, f.time_ms):
            await recon.run_once()
            pending.pop(f.coin, None)
            track_dd()
        events, _drift = tracker.on_fill(f)
        for ev in events:
            had = paper.position(ev.coin) is not None
            await engine.handle(ev)
            if ev.type == EventType.OPEN and not had and paper.position(ev.coin) is not None:
                t1 = ends.get((ev.coin, ev.leader_time_ms), f.time_ms)
                pending[ev.coin] = [c for c in candles(ev.coin, ev.leader_time_ms, t1) if c[0] >= ev.leader_time_ms]
            elif ev.type in (EventType.CLOSE, EventType.FLIP, EventType.REDUCE) and paper.position(ev.coin) is None:
                pending.pop(ev.coin, None)
                track_dd()
    await advance(max((c[-1][0] for c in pending.values() if c), default=now[0]))

    copies = store.copies()
    decisions: dict[str, int] = {}
    rejections: dict[str, int] = {}
    for row in store.events_since(0):
        decisions[row["decision"]] = decisions.get(row["decision"], 0) + 1
        if row["rejection"]:
            rejections[row["rejection"]] = rejections.get(row["rejection"], 0) + 1
    traded = [c for c in copies if c.status != CopyStatus.FAILED]
    closed_copies = [c for c in traded if c.closed_at is not None]
    counts = {
        "copied": len(traded),
        "tp": sum(c.status == CopyStatus.CLOSED_TP for c in traded),
        "liq": sum(c.status == CopyStatus.CLOSED_LIQ for c in traded),
        "exits": sum(c.status in (CopyStatus.CLOSED_LEADER, CopyStatus.CLOSED_RECONCILED) for c in traded),
        "open": sum(c.closed_at is None for c in traded),
        "wins": sum(c.realized_pnl > 0 for c in closed_copies),
        "losses": sum(c.realized_pnl <= 0 for c in closed_copies),
        "failed": sum(c.status == CopyStatus.FAILED for c in copies),
    }
    result = ReplayResult(copies=copies, decisions=decisions, rejections=rejections, start_equity=equity0,
                          final_equity=paper.account().equity, max_dd=max_dd, counts=counts)
    store.close()
    tmp.cleanup()
    return result
