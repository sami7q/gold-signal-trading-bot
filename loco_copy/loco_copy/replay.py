"""Run recorded Loco fills through the real pipeline (tracker -> rules -> engine -> reconciler) with a
PaperBroker whose take-profit / liquidation are driven by recorded candles (botup.md §4, §12).

Time is simulated: the clock is the fill being processed, so no rule ever sees a "stale" signal. Prices are
Loco's own fill prices (no slippage); fees are whatever the caller passes (the research used 0.08 % per side).
"""

from __future__ import annotations

import bisect
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
from loco_copy.tracker import LeaderTracker, chain_order, is_main_perp

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
    fills = chain_order([f for f in fills if f.time_ms >= start_ms and (end_ms is None or f.time_ms <= end_ms)])
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


# -- cap20_cross (botup.md v4 §5 replay.py, §6 test_replay_matches_cap20) ----------------------------------------

TripCandles = dict[str, list[Candle]]  # "coin|t0" -> candles of that leader trade


def load_equity_history(path: Path) -> list[tuple[int, Decimal]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [(int(t), Decimal(str(v))) for t, v in json.load(fh)]


def equity_at(history: list[tuple[int, Decimal]]) -> Callable[[int], Decimal]:
    """W(t): the leader's account value, linearly interpolated between recorded points (the research's W_at)."""
    times = [t for t, _ in history]

    def at(t: int) -> Decimal:
        i = bisect.bisect_right(times, t)
        if i == 0:
            return history[0][1]
        if i >= len(history):
            return history[-1][1]
        (ta, va), (tb, vb) = history[i - 1], history[i]
        return va + (vb - va) * (t - ta) / (tb - ta)

    return at


def load_trip_candles(path: Path) -> TripCandles:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        raw: dict[str, list[list[float]]] = json.load(fh)
    return {k: [(int(c[0]), Decimal(str(c[1])), Decimal(str(c[2])), Decimal(str(c[3])), Decimal(str(c[4])))
                for c in v] for k, v in raw.items()}


async def run_cap20_replay(fills: list[LeaderFill], trip_candles: TripCandles, leader_equity: Callable[[int], Decimal],
                           settings: Settings, *, start_ms: int, end_ms: int, fee_rate: Decimal,
                           start_equity: Decimal, maintenance_rate: Decimal = Decimal("0.05")) -> ReplayResult:
    """The cap20 engine over recorded fills, on a virtual cross account filled at the leader's own prices (fee per
    side = taker + slippage). Candles mark open trades and drive the cross-liquidation check at their adverse
    extreme, as the research did; W0 at each open is the leader's interpolated account value."""
    from loco_copy.brokers.virtual import VirtualAccount  # noqa: PLC0415 - replay-only imports
    from loco_copy.cap20 import STRATEGY, Book, Cap20Engine, book_account_liquidation  # noqa: PLC0415
    from loco_copy.models import Quote  # noqa: PLC0415
    from loco_copy.store import lane_key  # noqa: PLC0415

    fills = sorted((f for f in fills if start_ms <= f.time_ms <= end_ms), key=lambda f: (f.time_ms, f.tid))
    now = [start_ms]

    def clock() -> int:
        return now[0]

    coins = sorted({f.coin for f in fills})
    tiny = Decimal("1e-10")
    instruments = {c: InstrumentInfo(c, tiny, Decimal(0), tiny, 100) for c in coins}
    prices: dict[str, Decimal] = {}

    def quote(symbol: str) -> Quote:
        return Quote(prices[symbol], prices[symbol], prices[symbol])

    account = VirtualAccount(name="replay", instruments=instruments, quote=quote, start_equity=start_equity,
                             fee_rate=fee_rate, leverage=settings.leverage,
                             maintenance_rates={c: maintenance_rate for c in coins}, clock=clock)
    tmp = tempfile.TemporaryDirectory()
    settings = settings.model_copy(update={"data_dir": Path(tmp.name), "copy_enabled": True,
                                           "copy_observe_only": False, "allow_execute": True})
    store = Store(":memory:")
    notifier = Notifier(store, echo=False)
    tracker = LeaderTracker()
    risk = RiskGuard(store, settings, notifier, monitor_feed=False, clock=clock)
    engine = Cap20Engine(store=store, real=Book(account, "replay"), virtual=None, settings=settings, risk=risk,
                         notifier=notifier, mapper=SymbolMapper(coins, identity=True), tracker=tracker,
                         instruments=instruments, clock=clock, run_sync=run_inline)

    timeline: list[tuple[int, int, Any]] = [(f.time_ms, 1, f) for f in fills]
    for key, candles in trip_candles.items():
        coin, t0 = key.rsplit("|", 1)
        if coin in instruments:
            timeline += [(c[0], 2, (coin, int(t0), c)) for c in candles if start_ms <= c[0] <= end_ms]
    timeline.sort(key=lambda x: (x[0], x[1]))  # stable: fills keep their (time, tid) order, candles come after

    open_by_coin: dict[str, Copy] = {}
    liquidations = 0
    peak = start_equity
    max_dd = Decimal(0)
    closest: Decimal | None = None
    for t, kind, payload in timeline:
        now[0] = t
        if kind == 2:  # a candle of one leader trade: only while our copy of that very trade is open
            coin, t0, (_ts, _o, high, low, close) = payload
            live = open_by_coin.get(coin)
            if live is None or live.opened_at != t0:
                continue
            if account.position(coin) is not None:
                adverse = low if live.side > 0 else high
                closed = account.mark_all({coin: Quote(adverse, adverse, adverse)})
                dist = account.liquidation_distance()
                if dist is not None and (closest is None or dist < closest):
                    closest = dist
                if closed:
                    liquidations += 1
                    copies = [c for c in store.copies() if c.closed_at is None and c.strategy == STRATEGY]
                    book_account_liquidation(store, "replay", copies, closed, clock, notifier)
            account.set_mark(coin, close)
        else:
            f: LeaderFill = payload
            prices[f.coin] = f.px
            account.set_mark(f.coin, f.px)
            store.set_state(lane_key("leader_equity", settings.lane),
                            {"value": str(leader_equity(f.time_ms)), "taken_at_ms": f.time_ms})
            for ev in tracker.on_fill(f)[0]:
                await engine.handle(ev)
            open_by_coin = {c.coin: c for c in engine.open_trades()}
        eq = account.equity()
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)

    copies = store.copies()
    decisions: dict[str, int] = {}
    rejections: dict[str, int] = {}
    for row in store.events_since(0):
        decisions[row["decision"]] = decisions.get(row["decision"], 0) + 1
        if row["rejection"]:
            rejections[row["rejection"]] = rejections.get(row["rejection"], 0) + 1
    closed_copies = [c for c in copies if c.closed_at is not None]
    counts = {"trades": len(copies), "closed": len(closed_copies), "open": len(copies) - len(closed_copies),
              "wins": sum(c.realized_pnl > 0 for c in closed_copies), "liq": liquidations,
              "capped": sum(c.capped for c in copies), "adds": sum(c.adds_done for c in copies)}
    result = ReplayResult(copies=copies, decisions=decisions, rejections=rejections, start_equity=start_equity,
                          final_equity=account.equity(), max_dd=max_dd,
                          counts=counts | {"fees": int(account.fees_paid),
                                           "closest_x100": int((closest or 0) * 100)})
    store.close()
    tmp.cleanup()
    return result
