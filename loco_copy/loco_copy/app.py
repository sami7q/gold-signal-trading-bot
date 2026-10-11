"""`loco-copy run`: one asyncio process -- one Hyperliquid feed, and per copied leader a "lane" with its own
tracker + engine + reconciler + risk guard + broker (botup.md v3 §3).

Start-up order, per lane (so nothing a leader did while we were down is lost, and nothing he already held is
ever copied -- a newly added leader is seeded from a snapshot exactly like Loco was on the first run):
  1. restore the tracker (if any) and settle orders that were in flight when we stopped;
  2. REST-backfill every fill since the last one we saw (opens will be too old -> rejected; exits followed);
  3. take a leader snapshot: first run seeds the tracker (every existing position = never copied),
     later runs compare against it (drift -> resync, never guess);
  4. connect the WebSocket (its first frame is history; only fills newer than what we saw are processed).
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import IO, Any

from loco_copy.brokers.base import Broker
from loco_copy.brokers.bybit import BybitBroker, fetch_public_instruments
from loco_copy.brokers.paper import PaperBroker
from loco_copy.config import Settings, with_mode_override
from loco_copy.constants import CopyStatus
from loco_copy.engine import CopyEngine, HeldElsewhere
from loco_copy.feed import FillHandler, HyperliquidClient, HyperliquidFeed, now_ms
from loco_copy.models import Account, InstrumentInfo, LeaderFill
from loco_copy.notify import Notifier, configure_logging
from loco_copy.reconcile import Reconciler
from loco_copy.risk import RiskGuard
from loco_copy.store import Store, lane_key
from loco_copy.symbols import SymbolMapper
from loco_copy.tracker import LeaderTracker


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
        self.risk = RiskGuard(store, settings, notifier)
        saved = store.get_state(lane_key("tracker", label))
        self.restored = saved is not None
        self.tracker = LeaderTracker.from_dict(saved) if saved else LeaderTracker()
        self.last_seen_ms: int | None = store.get_state(lane_key("last_fill_ms", label))
        self.last_snapshot_ms: int | None = None
        self.paper: PaperBroker | None = None
        self.broker: Broker
        self.account = ""  # which exchange account: keyring service, or "paper:<label>"
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

    @property
    def default(self) -> Lane:
        return next(iter(self.lanes.values()))

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
                lane.broker, lane.account = by_service[service], service
            self.instruments = await asyncio.to_thread(next(iter(by_service.values())).instruments)
            self.mapper = SymbolMapper(self.instruments)
            for service in by_service:
                sharing = [x.label for x in self.lanes_on(service)]
                if len(sharing) > 1:
                    self.n.warning("SHARED_ACCOUNT", account=service, leaders=sharing,
                                   note="one position per symbol: a coin held by one leader blocks the others")
            return
        try:
            self.instruments = await asyncio.to_thread(fetch_public_instruments)
            self.mapper = SymbolMapper(self.instruments)
            source = "bybit public instrument list"
        except Exception as exc:  # noqa: BLE001
            self.instruments = hl_identity_instruments(await self.client.meta())
            self.mapper = SymbolMapper(self.instruments, identity=True)
            source = f"hyperliquid meta (bybit unreachable: {exc})"
            self.n.warning("PAPER_INSTRUMENTS_FALLBACK", error=str(exc))
        for lane in self.lanes.values():  # one paper account per leader: lanes never collide on paper
            s = lane.base
            paper = PaperBroker(instruments=self.instruments, start_equity=s.paper_start_equity,
                                fee_rate=s.paper_fee_rate, slippage_bps=s.paper_slippage_bps, leverage=s.leverage,
                                maintenance_margin_rate=s.maintenance_margin_rate, stale_after_ms=30_000)
            saved = self.store.get_state(lane_key("paper_broker", lane.label))
            if saved:
                paper.import_state(saved)
            lane.paper, lane.broker, lane.account = paper, paper, f"paper:{lane.label}"
        self.n.info("PAPER_BROKER_READY", instruments=len(self.instruments), source=source, leaders=list(self.lanes))

    def held_elsewhere(self, lane: Lane) -> HeldElsewhere:
        def check(symbol: str) -> str | None:
            for other in self.lanes_on(lane.account):
                if other is not lane and any(c.symbol == symbol for c in self.store.live_copies(leader=other.label)):
                    return other.label
            return None

        return check

    def account_symbols(self, lane: Lane) -> set[str]:
        return {c.symbol for other in self.lanes_on(lane.account) for c in self.store.live_copies(leader=other.label)}

    def build_lanes(self) -> None:
        for lane in self.lanes.values():
            lane.engine = CopyEngine(store=self.store, broker=lane.broker, settings=lane.s, risk=lane.risk,
                                     notifier=self.n, mapper=self.mapper, tracker=lane.tracker,
                                     instruments=self.instruments, held_elsewhere=self.held_elsewhere(lane))
            lane.recon = Reconciler(store=self.store, broker=lane.broker, settings=lane.s, notifier=self.n,
                                    tracker=lane.tracker, risk=lane.risk, engine=lane.engine,
                                    run_sync=asyncio.to_thread,
                                    account_symbols=lambda lane=lane: self.account_symbols(lane),  # type: ignore[misc]
                                    check_orphans=self.lanes_on(lane.account)[0] is lane)

    def save_runtime(self) -> None:
        for lane in self.lanes.values():
            self.store.set_state(lane_key("tracker", lane.label), lane.tracker.to_dict())
            if lane.paper is not None:
                self.store.set_state(lane_key("paper_broker", lane.label), lane.paper.export_state())

    # -- fills -----------------------------------------------------------------------------------------------

    def fill_handler(self, lane: Lane) -> FillHandler:
        async def handle(fill: LeaderFill, is_history: bool) -> None:
            await self.on_fill(lane, fill, is_history)

        return handle

    async def on_fill(self, lane: Lane, fill: LeaderFill, is_history: bool) -> None:
        async with self.lock:
            if self.store.has_fill(fill.tid, leader=lane.label):
                return
            if is_history and (lane.last_seen_ms is None or fill.time_ms <= lane.last_seen_ms):
                self.store.insert_fill(fill, leader=lane.label)  # history only: stored, never traded
                return
            if not self.store.insert_fill(fill, leader=lane.label):
                return
            events, drift = lane.tracker.on_fill(fill)
            if drift is not None:
                self.n.warning("LEADER_STATE_DRIFT", leader=lane.label, coin=drift.coin,
                               tracker=str(drift.tracker_position), leader_position=str(drift.snapshot_position),
                               source="fill")
            for ev in events:
                await lane.engine.handle(ev)
            lane.last_seen_ms = max(lane.last_seen_ms or 0, fill.time_ms)
            self.store.set_state(lane_key("last_fill_ms", lane.label), lane.last_seen_ms)
            self.save_runtime()

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

    async def reconcile_loop(self) -> None:
        while True:
            for lane in self.lanes.values():
                async with self.lock:
                    await lane.recon.run_once()
                    self.save_runtime()
            self.write_heartbeat()
            await asyncio.sleep(self.base.reconcile_seconds)

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
                    self.write_heartbeat()
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

    def on_frame(self) -> None:
        for lane in self.lanes.values():  # one WS serves every lane: its silence pauses all of them
            lane.risk.on_frame()

    def write_heartbeat(self) -> None:
        accounts: dict[str, Account | None] = {}
        lanes: dict[str, dict[str, object]] = {}
        for lane in self.lanes.values():
            if lane.account not in accounts:
                accounts[lane.account] = None
                with suppress(Exception):
                    accounts[lane.account] = lane.broker.account()
            acct = accounts[lane.account]
            lanes[lane.label] = {
                "address": lane.address, "copy_enabled": lane.s.copy_enabled, "observe_only": lane.s.copy_observe_only,
                "allow_execute": lane.s.allow_execute, "account": lane.account, "broker": lane.broker.name,
                "shared_account": len(self.lanes_on(lane.account)) > 1,
                "equity": str(acct.equity) if acct else None, "available": str(acct.available) if acct else None,
                "last_snapshot_ms": lane.last_snapshot_ms, "snapshot_failures": lane.risk.snapshot_failures}
        first = self.default
        acct = accounts.get(first.account)
        self.store.set_state("runtime", {
            "pid": os.getpid(), "updated_ms": now_ms(), "broker": first.broker.name, "env": self.base.broker_env,
            "copy_enabled": first.s.copy_enabled, "observe_only": first.s.copy_observe_only,
            "ws_connected": bool(self.feed and self.feed.connected),
            "ws_reconnects": self.feed.reconnects if self.feed else 0,
            "last_frame_ms": first.risk.last_frame_ms, "last_snapshot_ms": first.last_snapshot_ms,
            "snapshot_failures": first.risk.snapshot_failures,
            "equity": str(acct.equity) if acct else None, "available": str(acct.available) if acct else None,
            "lanes": lanes})

    # -- main ------------------------------------------------------------------------------------------------

    async def run(self) -> None:
        await self.build_brokers()
        self.build_lanes()
        first = self.default
        self.n.info("STARTING", broker=first.broker.name, env=self.base.broker_env, copy_enabled=first.s.copy_enabled,
                    observe_only=first.s.copy_observe_only, leader=first.address, restored=first.restored,
                    leaders={x.label: {"address": x.address, "copy_enabled": x.s.copy_enabled,
                                       "observe_only": x.s.copy_observe_only, "allow_execute": x.s.allow_execute,
                                       "account": x.account, "restored": x.restored} for x in self.lanes.values()})
        tasks: list[asyncio.Task[None]] = []
        if any(x.paper is not None for x in self.lanes.values()):
            tasks.append(asyncio.create_task(self.paper_price_loop()))
            await asyncio.sleep(3)  # first prices in before anything is sized
        async with self.lock:
            for lane in self.lanes.values():
                await lane.engine.recover()
        for lane in self.lanes.values():
            if lane.restored and lane.last_seen_ms is not None:
                for fill in await self.client.fills_since(lane.address, lane.last_seen_ms + 1):
                    await self.on_fill(lane, fill, False)
            await self.snapshot_once(lane, seed=not lane.restored)
            if lane.last_seen_ms is None:
                lane.last_seen_ms = lane.last_snapshot_ms or now_ms()
                self.store.set_state(lane_key("last_fill_ms", lane.label), lane.last_seen_ms)

        by_address = {lane.address: lane for lane in self.lanes.values()}
        self.feed = HyperliquidFeed(
            ws_url=self.base.hl_ws_url, users={a: self.fill_handler(x) for a, x in by_address.items()},
            client=self.client, on_frame=self.on_frame, last_seen_ms=lambda a: by_address[a].last_seen_ms,
            on_connected=lambda: self.n.info("WS_CONNECTED", leaders=list(self.lanes)),
            on_error=lambda err: self.n.warning("WS_DISCONNECTED", error=err),
            on_unknown_user=lambda user: self.n.warning("WS_UNKNOWN_USER", user=user))
        step = self.base.leader_snapshot_seconds / len(self.lanes)
        tasks += [asyncio.create_task(self.feed.run()), asyncio.create_task(self.reconcile_loop()),
                  asyncio.create_task(self.control_loop())]
        tasks += [asyncio.create_task(self.snapshot_loop(x, i * step)) for i, x in enumerate(self.lanes.values())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            self.save_runtime()
            await self.client.close()
            self.n.info("STOPPED")


async def close_all(store: Store, engine: CopyEngine, n: Notifier) -> None:
    """Close every open copy of the engine's lane (reduce-only)."""
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
