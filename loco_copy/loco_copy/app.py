"""`loco-copy run`: one asyncio process -- one Hyperliquid feed, and per copied leader a "lane" with its own
tracker + engine + reconciler + risk guard + broker (botup.md v3 §3).

Start-up order (so nothing a leader did while we were down is lost, and nothing he already held is ever
copied -- a newly added leader is seeded from a snapshot exactly like Loco was on the first run):
  1. restore the trackers (if any) and settle orders that were in flight when we stopped;
  2. REST-backfill every lane's fills since the last one we saw (exits followed; opens are refused: no good
     feed contact yet / too old);
  3. take each leader's snapshot: first run seeds the tracker (every existing position = never copied),
     later runs compare against it (drift -> resync, never guess);
  4. read each cap20 leader's equity (W0), then connect the WebSocket (its first frame is history; only fills
     newer than what we saw are processed) and start the REST poll that fills its gaps.

cap20_cross lanes (botup.md v4/v5) trade on two books: the exchange account (EXECUTE) and a virtual cross
account filled at Bybit's live bid / ask (OBSERVE -- the v5 "trio" ledger, shared by every lane when
SHARED_EQUITY=true).
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import IO, Any

from loco_copy.brokers.base import Broker
from loco_copy.brokers.bybit import (
    BybitBroker,
    fetch_maintenance_rates,
    fetch_public_instruments,
    fetch_public_tickers,
)
from loco_copy.brokers.bybit_stream import QuoteBook
from loco_copy.brokers.paper import PaperBroker
from loco_copy.brokers.virtual import VirtualAccount
from loco_copy.cap20 import Book, Cap20Engine, Cap20Reconciler, book_account_liquidation
from loco_copy.config import Settings, strategy_warnings, with_mode_override
from loco_copy.constants import CopyStatus
from loco_copy.engine import CopyEngine, HeldElsewhere
from loco_copy.feed import FillHandler, HyperliquidClient, HyperliquidFeed, now_ms
from loco_copy.models import Account, Copy, InstrumentInfo, LeaderFill, Position
from loco_copy.notify import Notifier, configure_logging
from loco_copy.observe import daily_line
from loco_copy.portfolio import PortfolioSim
from loco_copy.reconcile import Reconciler
from loco_copy.risk import AccountGuard, RiskGuard
from loco_copy.store import Store, lane_key
from loco_copy.symbols import SymbolMapper
from loco_copy.tracker import LeaderTracker, aggregate_burst, chain_order

VIRTUAL_MARK_SECONDS = 10
#: the most traded Bybit symbols are streamed from the start (a leader's first trade on them never waits for REST)
WATCH_TOP_SYMBOLS = 150
EQUITY_HISTORY_SECONDS = 300


class AlreadyRunning(RuntimeError):
    pass


class RunLock:
    """One running bot per data folder -- two would both send every order."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh: IO[str] | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self._path, "a+", encoding="utf-8")  # noqa: SIM115 - held for the process lifetime
        try:
            if sys.platform == "win32":
                import msvcrt  # noqa: PLC0415

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl  # noqa: PLC0415

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise AlreadyRunning(f"another loco-copy is already running ({self._path})") from exc
        self._fh = fh

    def is_held_elsewhere(self) -> bool:
        try:
            self.acquire()
        except AlreadyRunning:
            return True
        self.release()
        return False

    def release(self) -> None:
        if self._fh is not None:
            with suppress(OSError):
                self._fh.close()
            self._fh = None


def hl_identity_instruments(meta: dict[str, Any]) -> dict[str, InstrumentInfo]:
    """Fallback paper instruments straight from Hyperliquid (used only if Bybit's public list is unreachable)."""
    out = {}
    for a in meta.get("universe", []):
        if a.get("isDelisted"):
            continue
        dec = int(a.get("szDecimals", 0))
        step = Decimal(1).scaleb(-dec)
        out[a["name"]] = InstrumentInfo(a["name"], step, step, Decimal(1).scaleb(-(6 - dec)),
                                        int(a.get("maxLeverage", 1)))
    return out


def _keys_in(service: str) -> tuple[str, str] | None:
    import keyring  # noqa: PLC0415

    key = keyring.get_password(service, "api_key")
    secret = keyring.get_password(service, "api_secret")
    return (key, secret) if key and secret else None


def lane_keys(lane: Settings) -> tuple[str, str, str]:
    """(service, key, secret). A lane uses its own keys (`set-key bybit-demo --leader <label>`, i.e. its own Bybit
    sub-account) and otherwise shares the main account's: the shared-account layout of botup.md v3 §5.6."""
    for service in dict.fromkeys((lane.keyring_service, lane.shared_keyring_service)):
        found = _keys_in(service)
        if found is not None:
            return service, *found
    raise SystemExit(f"No Bybit {lane.broker_env} keys in the OS keyring for leader {lane.lane!r} -- run: "
                     f"loco-copy set-key bybit-{lane.broker_env}")


class Lane:
    """One copied leader: its settings (with the dashboard's mode applied), tracker, risk guard, broker,
    engine and reconciler. Lanes that resolve to the same exchange account share one broker object."""

    def __init__(self, label: str, base: Settings, settings: Settings, store: Store, notifier: Notifier) -> None:
        self.label = label
        self.base = base
        self.s = settings
        self.address = base.leader_address
        self.cap20 = base.is_cap20
        self.risk = RiskGuard(store, settings, notifier)
        saved = store.get_state(lane_key("tracker", label))
        self.restored = saved is not None
        self.tracker = LeaderTracker.from_dict(saved) if saved else LeaderTracker()
        self.last_seen_ms: int | None = store.get_state(lane_key("last_fill_ms", label))
        self.last_snapshot_ms: int | None = None
        self.paper: PaperBroker | None = None
        self.broker: Broker
        self.account = ""  # which exchange account: keyring service, "paper:<label>", or a paper cross account
        self.virtual_name: str | None = None  # cap20: the observe ledger this lane fills when observing
        self.engine: CopyEngine
        self.recon: Reconciler


class App:
    def __init__(self, settings: Settings) -> None:
        self.store = Store(settings.db_path)
        self.base = settings
        self.n = Notifier(self.store, telegram_token=settings.alert_telegram_token,
                          telegram_chat_id=settings.alert_telegram_chat_id)
        self.lock = asyncio.Lock()
        self.client = HyperliquidClient(settings.hl_info_url)
        self.lanes: dict[str, Lane] = {}
        for label in settings.labels:
            base = settings.for_lane(label)
            eff = with_mode_override(base, self.store.get_state(lane_key("mode_override", label)))
            self.lanes[label] = Lane(label, base, eff, self.store, self.n)
        self.mapper: SymbolMapper
        self.instruments: dict[str, InstrumentInfo]
        self.feed: HyperliquidFeed | None = None
        #: cross accounts simulated here: the observe ledgers ("virtual") and cap20 paper accounts ("paper")
        self.virtual: dict[str, VirtualAccount] = {}
        self.guards: dict[str, AccountGuard] = {}
        self.mmr: dict[str, Decimal] = {}
        self._poll_failures = 0
        #: live Bybit prices for every quote the bot takes (orders, the observe ledger, marks)
        self.quotes = QuoteBook()
        #: botup.md §V6: the «new trio» portfolio simulator (Hyperliquid prices, every dex, never an order)
        self.portfolio = PortfolioSim(settings, self.store, self.n, self.client) if settings.portfolio_enabled \
            else None

    @property
    def default(self) -> Lane:
        return next(iter(self.lanes.values()))

    @property
    def cap20_lanes(self) -> list[Lane]:
        return [x for x in self.lanes.values() if x.cap20]

    def lanes_on(self, account: str) -> list[Lane]:
        return [x for x in self.lanes.values() if x.account == account]

    # -- set-up ----------------------------------------------------------------------------------------------

    async def build_brokers(self) -> None:
        if self.base.broker == "bybit":
            by_service: dict[str, BybitBroker] = {}
            for lane in self.lanes.values():
                service, key, secret = lane_keys(lane.base)
                if service not in by_service:
                    by_service[service] = BybitBroker(api_key=key, api_secret=secret, env=self.base.broker_env)
                    by_service[service].quotes = self.quotes
                lane.broker, lane.account = by_service[service], service
            self.instruments = await asyncio.to_thread(next(iter(by_service.values())).instruments)
            self.mapper = SymbolMapper(self.instruments)
            for service in by_service:
                sharing = [x.label for x in self.lanes_on(service)]
                if len(sharing) > 1:
                    self.n.warning("SHARED_ACCOUNT", account=service, leaders=sharing,
                                   note="one position per symbol: cap20 lanes net out, first-fill lanes block")
        else:
            try:
                self.instruments = await asyncio.to_thread(fetch_public_instruments)
                self.mapper = SymbolMapper(self.instruments)
                source = "bybit public instrument list"
            except Exception as exc:  # noqa: BLE001
                self.instruments = hl_identity_instruments(await self.client.meta())
                self.mapper = SymbolMapper(self.instruments, identity=True)
                source = f"hyperliquid meta (bybit unreachable: {exc})"
                self.n.warning("PAPER_INSTRUMENTS_FALLBACK", error=str(exc))
            for lane in self.lanes.values():
                if lane.cap20:
                    continue  # a cap20 lane trades on a paper cross account, built below
                s = lane.base  # one paper account per first-fill leader: lanes never collide on paper
                paper = PaperBroker(instruments=self.instruments, start_equity=s.paper_start_equity,
                                    fee_rate=s.paper_fee_rate, slippage_bps=s.paper_slippage_bps,
                                    leverage=s.leverage, maintenance_margin_rate=s.maintenance_margin_rate,
                                    stale_after_ms=30_000)
                saved = self.store.get_state(lane_key("paper_broker", lane.label))
                if saved:
                    paper.import_state(saved)
                lane.paper, lane.broker, lane.account = paper, paper, f"paper:{lane.label}"
            self.n.info("PAPER_BROKER_READY", instruments=len(self.instruments), source=source,
                        leaders=list(self.lanes))
        if self.cap20_lanes:
            await self.build_cross_accounts()

    async def build_cross_accounts(self) -> None:
        """cap20: the observe ledger(s) and, on BROKER=paper, the paper cross account(s) -- all filled at
        Bybit's live mainnet bid / ask, liquidated with Bybit's tier-1 maintenance rates (C-6)."""
        try:
            self.mmr = await asyncio.to_thread(fetch_maintenance_rates)
        except Exception as exc:  # noqa: BLE001 - the 0.5 % default is used instead
            self.n.warning("MAINTENANCE_RATES_UNAVAILABLE", error=str(exc))
        quote = self.quotes.quote  # live stream, REST only for a symbol not streamed yet
        shared = self.base.shared_equity
        for lane in self.cap20_lanes:
            s = lane.base
            lane.virtual_name = "virtual" if shared else f"virtual:{lane.label}"
            names = [lane.virtual_name]
            if self.base.broker == "paper":
                lane.account = "paper" if shared else f"paper:{lane.label}"
                names.append(lane.account)
            for name in names:
                if name not in self.virtual:
                    paper = name.startswith("paper")
                    v = VirtualAccount(name=name, instruments=self.instruments, quote=quote,
                                       start_equity=s.paper_start_equity if paper else s.virtual_start_equity,
                                       fee_rate=s.paper_fee_rate if paper else s.virtual_fee_rate,
                                       leverage=s.leverage, maintenance_rates=self.mmr,
                                       slippage_bps=s.paper_slippage_bps if paper else Decimal(0))
                    saved = self.store.get_state(f"virtual_account:{name}")
                    if saved:
                        v.import_state(saved)
                    self.virtual[name] = v
            if self.base.broker == "paper":
                lane.broker = self.virtual[lane.account]
        self.n.info("CROSS_ACCOUNTS_READY", accounts={n: str(v.equity()) for n, v in self.virtual.items()},
                    maintenance_rates=len(self.mmr), shared_equity=shared)

    def guard(self, account: str) -> AccountGuard | None:
        if self.base.account_kill_dd_pct is None:
            return None
        if account not in self.guards:
            self.guards[account] = AccountGuard(self.store, account, self.base.account_kill_dd_pct, self.n)
        return self.guards[account]

    def held_elsewhere(self, lane: Lane) -> HeldElsewhere:
        def check(symbol: str) -> str | None:
            for other in self.lanes_on(lane.account):
                if other is not lane and any(c.symbol == symbol for c in self.store.live_copies(leader=other.label)):
                    return other.label
            return None

        return check

    def account_symbols(self, lane: Lane) -> set[str]:
        return {c.symbol for c in self.account_copies(lane)}

    def account_copies(self, lane: Lane) -> list[Copy]:
        return [c for other in self.lanes_on(lane.account) for c in self.store.live_copies(leader=other.label)]

    def copies_on(self, account: str) -> list[Copy]:
        """Every open cap20 copy living on this simulated cross account (an observe ledger or paper account)."""
        out: list[Copy] = []
        for lane in self.cap20_lanes:
            if lane.virtual_name == account:
                out += [c for c in self.store.copies_with_status(CopyStatus.OBSERVED_ONLY, leader=lane.label)
                        if c.closed_at is None]
            if lane.account == account:
                out += self.store.live_copies(leader=lane.label)
        return out

    def build_lanes(self) -> None:
        for lane in self.lanes.values():
            first_on_account = self.lanes_on(lane.account)[0] is lane
            if lane.cap20:
                assert lane.virtual_name is not None
                real = Book(lane.broker, lane.account, self.guard(lane.account),
                            shared=len(self.lanes_on(lane.account)) > 1)
                virtual = Book(self.virtual[lane.virtual_name], lane.virtual_name, self.guard(lane.virtual_name),
                               shared=sum(x.virtual_name == lane.virtual_name for x in self.cap20_lanes) > 1)
                engine = Cap20Engine(store=self.store, real=real, virtual=virtual, settings=lane.s, risk=lane.risk,
                                     notifier=self.n, mapper=self.mapper, tracker=lane.tracker,
                                     instruments=self.instruments)
                lane.engine = engine
                lane.recon = Cap20Reconciler(store=self.store, engine=engine, settings=lane.s, notifier=self.n,
                                             tracker=lane.tracker, risk=lane.risk, run_sync=asyncio.to_thread,
                                             account_copies=lambda lane=lane: self.account_copies(lane),  # type: ignore[misc]
                                             check_account=first_on_account)
                continue
            lane.engine = CopyEngine(store=self.store, broker=lane.broker, settings=lane.s, risk=lane.risk,
                                     notifier=self.n, mapper=self.mapper, tracker=lane.tracker,
                                     instruments=self.instruments, held_elsewhere=self.held_elsewhere(lane))
            lane.recon = Reconciler(store=self.store, broker=lane.broker, settings=lane.s, notifier=self.n,
                                    tracker=lane.tracker, risk=lane.risk, engine=lane.engine,
                                    run_sync=asyncio.to_thread,
                                    account_symbols=lambda lane=lane: self.account_symbols(lane),  # type: ignore[misc]
                                    check_orphans=first_on_account)

    def save_runtime(self) -> None:
        for lane in self.lanes.values():
            self.store.set_state(lane_key("tracker", lane.label), lane.tracker.to_dict())
            if lane.paper is not None:
                self.store.set_state(lane_key("paper_broker", lane.label), lane.paper.export_state())
        for name, v in self.virtual.items():
            self.store.set_state(f"virtual_account:{name}", v.export_state())

    def hl_ok(self) -> None:
        for lane in self.lanes.values():
            lane.risk.on_hl_ok()

    # -- fills -----------------------------------------------------------------------------------------------

    def fill_handler(self, lane: Lane) -> FillHandler:
        async def handle(fills: list[LeaderFill], is_history: bool) -> None:
            await self.on_fills(lane, fills, is_history)

        return handle

    async def on_fills(self, lane: Lane, fills: list[LeaderFill], is_history: bool) -> None:
        """One delivery (WS frame, REST page). New fills only (deduped by tid); the pieces of one order are put
        back in sequence (chain_order) and merged into one move (aggregate_burst) before the tracker."""
        async with self.lock:
            new = []
            for fill in fills:
                if self.store.has_fill(fill.tid, leader=lane.label):
                    continue
                if is_history and (lane.last_seen_ms is None or fill.time_ms <= lane.last_seen_ms):
                    self.store.insert_fill(fill, leader=lane.label)  # history only: stored, never traded
                    continue
                if self.store.insert_fill(fill, leader=lane.label):
                    new.append(fill)
            if not new:
                return
            for fill in aggregate_burst(chain_order(new)):
                synced = lane.tracker.is_synced(fill.coin)
                events, drift = lane.tracker.on_fill(fill)
                if not synced and not events and drift is None:
                    self.n.info("SKIPPED_PREEXISTING", leader=lane.label, coin=fill.coin, tid=fill.tid,
                                position=str(fill.new_position), note="open before the bot started: ignored until flat")
                if drift is not None:
                    self.n.warning("LEADER_STATE_DRIFT", leader=lane.label, coin=drift.coin,
                                   tracker=str(drift.tracker_position), leader_position=str(drift.snapshot_position),
                                   source="fill")
                for ev in events:
                    await lane.engine.handle(ev)
            lane.last_seen_ms = max(lane.last_seen_ms or 0, *(f.time_ms for f in new))
            self.store.set_state(lane_key("last_fill_ms", lane.label), lane.last_seen_ms)
            self.save_runtime()

    async def on_fill(self, lane: Lane, fill: LeaderFill, is_history: bool) -> None:
        await self.on_fills(lane, [fill], is_history)

    # -- loops -----------------------------------------------------------------------------------------------

    async def snapshot_once(self, lane: Lane, *, seed: bool = False) -> None:
        try:
            snap = await self.client.snapshot(lane.address)
        except Exception as exc:  # noqa: BLE001
            lane.risk.on_snapshot_result(False)
            self.n.warning("LEADER_SNAPSHOT_FAILED", leader=lane.label, error=str(exc),
                           failures=lane.risk.snapshot_failures)
            return
        lane.risk.on_snapshot_result(True)
        self.hl_ok()
        lane.last_snapshot_ms = snap.taken_at_ms
        lane.risk.on_leader_account_value(snap.account_value)
        async with self.lock:
            if seed:
                lane.tracker.seed(snap)
                self.n.info("TRACKER_SEEDED", leader=lane.label,
                            pre_existing={c: str(p) for c, p in snap.positions.items()})
            else:
                for d in lane.tracker.on_snapshot(snap):
                    self.n.warning("LEADER_STATE_DRIFT", leader=lane.label, coin=d.coin,
                                   tracker=str(d.tracker_position), leader_position=str(d.snapshot_position),
                                   source="snapshot")
            self.store.set_state(lane_key("leader_snapshot", lane.label), {
                "taken_at_ms": snap.taken_at_ms, "account_value": str(snap.account_value),
                "positions": {c: str(p) for c, p in snap.positions.items()},
                "entry_px": {c: str(p) for c, p in snap.entry_px.items()}})
            self.save_runtime()

    async def snapshot_loop(self, lane: Lane, offset_s: float) -> None:
        await asyncio.sleep(offset_s)  # the leaders' snapshots are staggered over the interval
        while True:
            await asyncio.sleep(self.base.leader_snapshot_seconds)
            await self.snapshot_once(lane)

    async def leader_equity_once(self, lane: Lane) -> None:
        """W0 of a cap20 lane: the leader's portfolio "day" account value (botup.md v4 §2.4); the last snapshot's
        account value if Hyperliquid has no history for the wallet."""
        value: Decimal | None = None
        source = "portfolio"
        try:
            value = await self.client.portfolio_equity(lane.address)
            self.hl_ok()
        except Exception as exc:  # noqa: BLE001
            self.n.warning("LEADER_EQUITY_FAILED", leader=lane.label, error=str(exc))
        if value is None or value <= 0:
            snap = self.store.get_state(lane_key("leader_snapshot", lane.label))
            value, source = (Decimal(snap["account_value"]) if snap else None), "snapshot"
        if value is not None and value > 0:
            self.store.set_state(lane_key("leader_equity", lane.label),
                                 {"value": str(value), "taken_at_ms": now_ms(), "source": source})

    async def leader_equity_loop(self) -> None:
        """W0 must be at most LEADER_EQUITY_REFRESH_SECONDS old (botup.md v4 §4): each lane is refreshed a minute
        before that, and a failed refresh is retried the next minute."""
        refresh_ms = max(self.base.leader_equity_refresh_seconds - 60, 30) * 1000
        while True:
            await asyncio.sleep(60)
            for lane in self.cap20_lanes:
                raw = self.store.get_state(lane_key("leader_equity", lane.label)) or {}
                if now_ms() - int(raw.get("taken_at_ms", 0)) >= refresh_ms:
                    await self.leader_equity_once(lane)

    async def fill_poll_loop(self) -> None:
        """REST gap filler next to the WS (botup.md v4 §5 feed.py): one lane per FILL_POLL_SECONDS, in turn."""
        i = 0
        while True:
            await asyncio.sleep(self.base.fill_poll_seconds)
            lanes = [x for x in self.lanes.values() if self.needs_poll(x)]
            if not lanes:
                continue
            lane = lanes[i % len(lanes)]
            i += 1
            if lane.last_seen_ms is None:
                continue
            try:
                fills = await self.client.fills_since(lane.address, lane.last_seen_ms + 1)
            except Exception as exc:  # noqa: BLE001 - the WS is still there; say so only if it lasts
                self._poll_failures += 1
                if self._poll_failures in (5, 50, 500):
                    self.n.warning("FILL_POLL_FAILING", failures=self._poll_failures, error=str(exc))
                continue
            self._poll_failures = 0
            self.hl_ok()
            if fills:
                await self.on_fills(lane, fills, False)

    def needs_poll(self, lane: Lane) -> bool:
        """A lane that copies (OBSERVE / EXECUTE) or still holds a copy whose exit must be followed. Watch-only
        lanes with nothing open rely on the WS alone: Hyperliquid's REST budget (1200 weight / min) cannot poll
        every wallet every 2 s."""
        if lane.s.copy_enabled or self.store.live_copies(leader=lane.label):
            return True
        return any(c.closed_at is None for c in self.store.copies_with_status(CopyStatus.OBSERVED_ONLY,
                                                                                leader=lane.label))

    async def reconcile_loop(self) -> None:
        while True:
            for lane in self.lanes.values():
                await lane.recon.prefetch()  # the exchange's answer is awaited outside the lock
                async with self.lock:
                    await lane.recon.run_once()
                    self.save_runtime()
            await self.heartbeat()
            await asyncio.sleep(self.base.reconcile_seconds)

    async def heartbeat(self) -> None:
        """The heartbeat's exchange reads in worker threads: the event loop (and the feed) never waits for them."""
        fetched: dict[str, tuple[Account | None, list[Position] | None]] = {}
        for lane in self.lanes.values():
            if lane.account in fetched:
                continue
            account = positions = None
            with suppress(Exception):
                account = await asyncio.to_thread(lane.broker.account)
            if lane.cap20 and lane.account not in self.virtual:
                with suppress(Exception):
                    positions = await asyncio.to_thread(lane.broker.positions)
            fetched[lane.account] = (account, positions)
        self.write_heartbeat(fetched)

    def watch_symbols(self) -> set[str]:
        """Symbols worth streaming: what the leaders hold now and what we hold."""
        out: set[str] = set()
        for lane in self.lanes.values():
            snap = self.store.get_state(lane_key("leader_snapshot", lane.label)) or {}
            for coin in snap.get("positions", {}):
                m = self.mapper.map(coin)
                if m is not None:
                    out.add(m.symbol)
        for v in self.virtual.values():
            out |= set(v.held_symbols())
        return out

    async def start_quotes(self) -> None:
        try:
            top = await asyncio.to_thread(self.quotes.top_symbols, WATCH_TOP_SYMBOLS)
            await asyncio.to_thread(self.quotes.watch, (set(top) | self.watch_symbols()) & set(self.instruments))
            self.n.info("QUOTES_STREAMING", **self.quotes.stats())
        except Exception as exc:  # noqa: BLE001 - REST quotes still work, just slower
            self.n.warning("QUOTES_STREAM_FAILED", error=str(exc))

    def execute_lanes(self) -> list[Lane]:
        return [x for x in self.cap20_lanes if x.s.copy_enabled and not x.s.copy_observe_only and x.s.allow_execute
                and self.base.broker == "bybit"]

    async def prewarm_loop(self) -> None:
        """Cross margin + leverage set in advance for every coin a leader holds (EXECUTE lanes only): the open of
        an add or a reduce never waits for it. New coins are covered by the open itself, in parallel."""
        while True:
            for lane in self.execute_lanes():
                snap = self.store.get_state(lane_key("leader_snapshot", lane.label)) or {}
                symbols = {m.symbol for c in snap.get("positions", {}) if (m := self.mapper.map(c)) is not None}
                for symbol in sorted(symbols or {"BTCUSDT"}):
                    try:
                        await asyncio.to_thread(lane.broker.ensure_cross_leverage, symbol, lane.s.leverage)
                    except Exception as exc:  # noqa: BLE001
                        self.n.warning("CROSS_LEVERAGE_FAILED", leader=lane.label, symbol=symbol, error=str(exc))
            for lane in self.lanes.values():
                if lane.cap20:
                    with suppress(Exception):
                        await asyncio.to_thread(self.quotes.watch, self.watch_symbols() & set(self.instruments))
                    break
            await asyncio.sleep(300)

    async def control_loop(self) -> None:
        """Dashboard / CLI requests, one writer: the mode of each lane, close-all globally or per lane."""
        while True:
            await asyncio.sleep(2)
            close_everything = bool(self.store.get_state("close_all_requested"))
            for lane in self.lanes.values():
                wanted = with_mode_override(lane.base, self.store.get_state(lane_key("mode_override", lane.label)))
                if (wanted.copy_enabled, wanted.copy_observe_only) != (lane.s.copy_enabled, lane.s.copy_observe_only):
                    async with self.lock:
                        lane.s = wanted
                        lane.engine.use_settings(wanted)
                        lane.recon.use_settings(wanted)
                    self.n.warning("MODE_CHANGED", leader=lane.label, copy_enabled=wanted.copy_enabled,
                                   observe_only=wanted.copy_observe_only)
                    await self.heartbeat()
                own_key = lane_key("close_all_requested", lane.label)
                if close_everything or self.store.get_state(own_key):
                    async with self.lock:
                        await close_all(self.store, lane.engine, self.n)
                        self.store.set_state(own_key, False)
                        self.save_runtime()
            if close_everything:
                self.store.set_state("close_all_requested", False)

    async def paper_price_loop(self) -> None:
        papers = [x.paper for x in self.lanes.values() if x.paper is not None]
        while True:
            try:
                mids = await self.client.all_mids()
                async with self.lock:
                    for coin, mid in mids.items():
                        m = self.mapper.map(coin)
                        if m is not None and m.symbol in self.instruments:
                            for paper in papers:
                                paper.set_price(m.symbol, mid * m.price_multiplier)
            except Exception as exc:  # noqa: BLE001
                self.n.warning("PAPER_PRICES_FAILED", error=str(exc))
            await asyncio.sleep(2)

    async def virtual_loop(self) -> None:
        """Marks every simulated cross account at Bybit's live prices and applies Bybit's cross liquidation
        rule; feeds the account kill switch; keeps the equity history."""
        last_history = 0
        while True:
            await asyncio.sleep(VIRTUAL_MARK_SECONDS)
            held = {s for v in self.virtual.values() for s in v.held_symbols()}
            quotes = self.quotes.fresh(held)
            if held - set(quotes):
                try:
                    quotes = await asyncio.to_thread(fetch_public_tickers) | quotes
                except Exception as exc:  # noqa: BLE001
                    self.n.warning("VIRTUAL_PRICES_FAILED", error=str(exc))
                    continue
            async with self.lock:
                for name, v in self.virtual.items():
                    closed = v.mark_all(quotes)
                    if closed:
                        book_account_liquidation(self.store, name, self.copies_on(name), closed, now_ms, self.n)
                    guard = self.guard(name)
                    if guard is not None:
                        guard.on_equity(v.equity())
                self.save_runtime()
            if now_ms() - last_history >= EQUITY_HISTORY_SECONDS * 1000:
                last_history = now_ms()
                await self.record_equity()

    async def record_equity(self) -> None:
        ts = now_ms()
        for name, v in self.virtual.items():
            self.store.record_equity(name, ts, v.equity())
        seen = set()
        for lane in self.lanes.values():
            if lane.account in seen or lane.account in self.virtual:
                continue
            seen.add(lane.account)
            with suppress(Exception):
                equity = await asyncio.to_thread(lane.broker.total_equity) if self.base.broker == "bybit" else \
                    (await asyncio.to_thread(lane.broker.account)).equity
                self.store.record_equity(lane.account, ts, equity)

    async def daily_loop(self) -> None:
        """Once per UTC day: the observe line (botup.md v5 §V5.5 item 4) to the log, dashboard and Telegram."""
        while True:
            await asyncio.sleep(60)
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            last = self.store.get_state("daily_summary_date")
            if last == today:
                continue
            self.store.set_state("daily_summary_date", today)
            if last is None or not self.cap20_lanes:
                continue  # first start: the first line comes tomorrow
            labels = [x.label for x in self.cap20_lanes]
            name = self.cap20_lanes[0].virtual_name
            v = self.virtual.get(name) if name else None
            self.n.report("DAILY_SUMMARY", daily_line(self.store, labels, v.summary() if v else None, now_ms()),
                          leaders=labels)

    def on_frame(self) -> None:
        for lane in self.lanes.values():  # one WS serves every lane: its silence pauses all of them
            lane.risk.on_frame()

    def _account_figures(self, lane: Lane, positions: list[Position] | None = None) -> dict[str, Any]:
        """Exposure and distance to liquidation of an exchange account (Bybit tier-1 MMR, C-6)."""
        if lane.account in self.virtual:
            v = self.virtual[lane.account]
            dist = v.liquidation_distance()
            eq = v.equity()
            return {"notional": str(v.notional()), "exposure_x": str(v.notional() / eq) if eq > 0 else None,
                    "liq_distance": str(dist) if dist is not None else None}
        if positions is None:
            try:
                positions = lane.broker.positions()
            except Exception:  # noqa: BLE001
                return {}
        notional = sum((p.qty * p.entry_price for p in positions), Decimal(0))
        mm = sum((p.qty * p.entry_price * self.mmr.get(p.symbol, Decimal("0.005")) for p in positions), Decimal(0))
        return {"notional": str(notional), "maintenance": str(mm), "positions": len(positions)}

    def write_heartbeat(self, fetched: dict[str, tuple[Account | None, list[Position] | None]] | None = None) -> None:
        """`fetched`: the exchange reads, already done off the event loop (`heartbeat`); None reads them here."""
        accounts: dict[str, Account | None] = {}
        figures: dict[str, dict[str, Any]] = {}
        lanes: dict[str, dict[str, object]] = {}
        for lane in self.lanes.values():
            if lane.account not in accounts:
                pre = (fetched or {}).get(lane.account)
                accounts[lane.account] = pre[0] if pre else None
                if pre is None:
                    with suppress(Exception):
                        accounts[lane.account] = lane.broker.account()
                figures[lane.account] = self._account_figures(lane, pre[1] if pre else None) if lane.cap20 else {}
            acct = accounts[lane.account]
            fig = figures[lane.account]
            if acct and fig.get("notional") is not None and "exposure_x" not in fig and acct.equity > 0:
                mm = Decimal(fig.get("maintenance") or 0)
                fig |= {"exposure_x": str(Decimal(fig["notional"]) / acct.equity),
                        "liq_distance": str(acct.equity / mm) if mm > 0 else None}
            w0 = self.store.get_state(lane_key("leader_equity", lane.label))
            guard = self.guards.get(lane.account)
            lanes[lane.label] = {
                "address": lane.address, "copy_enabled": lane.s.copy_enabled, "observe_only": lane.s.copy_observe_only,
                "allow_execute": lane.s.allow_execute, "account": lane.account, "broker": lane.broker.name,
                "shared_account": len(self.lanes_on(lane.account)) > 1, "strategy": lane.s.strategy,
                "virtual_account": lane.virtual_name, "leader_equity": w0,
                "equity": str(acct.equity) if acct else None, "available": str(acct.available) if acct else None,
                "account_kill": guard.tripped() if guard else False, **fig,
                "last_snapshot_ms": lane.last_snapshot_ms, "snapshot_failures": lane.risk.snapshot_failures}
        virtual = {}
        for name, v in self.virtual.items():
            guard = self.guards.get(name)
            peak = guard.peak() if guard else None
            virtual[name] = v.summary() | {
                "peak": str(peak) if peak is not None else None, "kill_tripped": guard.tripped() if guard else False,
                "lanes": [x.label for x in self.cap20_lanes if name in (x.virtual_name, x.account)]}
        first = self.default
        acct = accounts.get(first.account)
        self.store.set_state("runtime", {
            "pid": os.getpid(), "updated_ms": now_ms(), "broker": first.broker.name, "env": self.base.broker_env,
            "copy_enabled": first.s.copy_enabled, "observe_only": first.s.copy_observe_only,
            "ws_connected": bool(self.feed and self.feed.connected),
            "ws_reconnects": self.feed.reconnects if self.feed else 0,
            "last_frame_ms": first.risk.last_frame_ms, "last_snapshot_ms": first.last_snapshot_ms,
            "last_hl_ok_ms": first.risk.last_hl_ok_ms, "feed_outage": first.risk.feed_outage(),
            "snapshot_failures": first.risk.snapshot_failures,
            "equity": str(acct.equity) if acct else None, "available": str(acct.available) if acct else None,
            "lanes": lanes, "virtual": virtual, "quotes": self.quotes.stats()})

    # -- main ------------------------------------------------------------------------------------------------

    async def run(self) -> None:
        await self.build_brokers()
        self.build_lanes()
        first = self.default
        self.n.info("STARTING", broker=first.broker.name, env=self.base.broker_env, copy_enabled=first.s.copy_enabled,
                    observe_only=first.s.copy_observe_only, leader=first.address, restored=first.restored,
                    leaders={x.label: {"address": x.address, "copy_enabled": x.s.copy_enabled,
                                       "observe_only": x.s.copy_observe_only, "allow_execute": x.s.allow_execute,
                                       "account": x.account, "restored": x.restored, "strategy": x.s.strategy,
                                       "virtual_account": x.virtual_name} for x in self.lanes.values()})
        for lane in self.lanes.values():
            for warning in strategy_warnings(lane.s):
                self.n.warning("STRATEGY_WARNING", leader=lane.label, warning=warning)
        tasks: list[asyncio.Task[None]] = []
        if any(x.paper is not None for x in self.lanes.values()):
            tasks.append(asyncio.create_task(self.paper_price_loop()))
            await asyncio.sleep(3)  # first prices in before anything is sized
        async with self.lock:
            for lane in self.lanes.values():
                await lane.engine.recover()
        for lane in self.lanes.values():  # every backfill before any snapshot: no fresh contact -> no stale open
            if lane.restored and lane.last_seen_ms is not None:
                await self.on_fills(lane, await self.client.fills_since(lane.address, lane.last_seen_ms + 1), False)
        for lane in self.lanes.values():
            await self.snapshot_once(lane, seed=not lane.restored)
            if lane.last_seen_ms is None:
                lane.last_seen_ms = lane.last_snapshot_ms or now_ms()
                self.store.set_state(lane_key("last_fill_ms", lane.label), lane.last_seen_ms)
        for lane in self.cap20_lanes:
            await self.leader_equity_once(lane)
        if self.cap20_lanes:
            await self.start_quotes()

        pf = self.portfolio
        if pf is not None:
            await pf.start()

        def connected() -> None:
            self.n.info("WS_CONNECTED", leaders=list(self.lanes))
            if pf is not None:
                pf.backfill_wanted.set()  # the simulator's wallets: REST fills any gap the WS left

        by_address = {lane.address: lane for lane in self.lanes.values()}
        self.feed = HyperliquidFeed(
            ws_url=self.base.hl_ws_url, users={a: self.fill_handler(x) for a, x in by_address.items()},
            client=self.client, on_frame=self.on_frame, last_seen_ms=lambda a: by_address[a].last_seen_ms,
            on_connected=connected,
            on_error=lambda err: self.n.warning("WS_DISCONNECTED", error=err),
            on_unknown_user=lambda user: self.n.warning("WS_UNKNOWN_USER", user=user),
            raw_users={a: pf.on_raw_fills for a in pf.addresses} if pf else None,
            mid_dexes=pf.dexes if pf else None, on_mids=pf.on_mids if pf else None)
        step = self.base.leader_snapshot_seconds / len(self.lanes)
        tasks += [asyncio.create_task(self.feed.run()), asyncio.create_task(self.reconcile_loop()),
                  asyncio.create_task(self.control_loop()), asyncio.create_task(self.fill_poll_loop())]
        tasks += [asyncio.create_task(self.snapshot_loop(x, i * step)) for i, x in enumerate(self.lanes.values())]
        if self.cap20_lanes:
            tasks += [asyncio.create_task(self.leader_equity_loop()), asyncio.create_task(self.virtual_loop()),
                      asyncio.create_task(self.daily_loop()), asyncio.create_task(self.prewarm_loop())]
        if pf is not None:
            tasks += pf.tasks()
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            self.save_runtime()
            if pf is not None:
                pf.save()
            await self.client.close()
            self.n.info("STOPPED")


async def close_all(store: Store, engine: CopyEngine, n: Notifier) -> None:
    """Close every open exchange copy of the engine's lane (observed copies hold nothing on the exchange)."""
    for copy in store.copies_with_status(CopyStatus.OPEN, leader=engine.leader):
        ok = await engine.force_close(copy, reason="KILL", status=CopyStatus.CLOSED_MANUAL)
        n.critical("KILL_CLOSE", leader=engine.leader, coin=copy.coin, symbol=copy.symbol, ok=ok)


def run(settings: Settings) -> None:
    configure_logging(settings.log_dir)
    lock = RunLock(settings.data_dir / "run.lock")
    lock.acquire()
    try:
        asyncio.run(App(settings).run())
    except KeyboardInterrupt:
        pass
    finally:
        lock.release()
