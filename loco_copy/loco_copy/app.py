"""`loco-copy run`: one asyncio process -- Hyperliquid feed + tracker + engine + reconciler + risk guard.

Start-up order (so nothing Loco did while we were down is lost, and nothing he already held is ever copied):
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
from loco_copy.engine import CopyEngine
from loco_copy.feed import HyperliquidClient, HyperliquidFeed, now_ms
from loco_copy.models import InstrumentInfo, LeaderFill
from loco_copy.notify import Notifier, configure_logging
from loco_copy.reconcile import Reconciler
from loco_copy.risk import RiskGuard
from loco_copy.store import Store
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


def load_keys(settings: Settings) -> tuple[str, str]:
    import keyring  # noqa: PLC0415

    key = keyring.get_password(settings.keyring_service, "api_key")
    secret = keyring.get_password(settings.keyring_service, "api_secret")
    if not key or not secret:
        raise SystemExit(f"No Bybit {settings.broker_env} keys in the OS keyring -- run: "
                         f"loco-copy set-key bybit-{settings.broker_env}")
    return key, secret


class App:
    def __init__(self, settings: Settings) -> None:
        self.store = Store(settings.db_path)
        self.base = settings
        self.s = with_mode_override(settings, self.store.get_state("mode_override"))
        settings = self.s
        self.n = Notifier(self.store, telegram_token=settings.alert_telegram_token,
                          telegram_chat_id=settings.alert_telegram_chat_id)
        self.lock = asyncio.Lock()
        self.client = HyperliquidClient(settings.hl_info_url)
        self.risk = RiskGuard(self.store, settings, self.n)
        saved = self.store.get_state("tracker")
        self.restored = saved is not None
        self.tracker = LeaderTracker.from_dict(saved) if saved else LeaderTracker()
        self.paper: PaperBroker | None = None
        self.broker: Broker
        self.mapper: SymbolMapper
        self.instruments: dict[str, InstrumentInfo]
        self.engine: CopyEngine
        self.recon: Reconciler
        self.feed: HyperliquidFeed | None = None
        self.last_seen_ms: int | None = self.store.get_state("last_fill_ms")
        self.last_snapshot_ms: int | None = None

    # -- set-up ----------------------------------------------------------------------------------------------

    async def build_broker(self) -> None:
        if self.s.broker == "bybit":
            key, secret = load_keys(self.s)
            broker = BybitBroker(api_key=key, api_secret=secret, env=self.s.broker_env)
            self.instruments = await asyncio.to_thread(broker.instruments)
            self.mapper = SymbolMapper(self.instruments)
            self.broker = broker
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
        paper = PaperBroker(instruments=self.instruments, start_equity=self.s.paper_start_equity,
                            fee_rate=self.s.paper_fee_rate, slippage_bps=self.s.paper_slippage_bps,
                            leverage=self.s.leverage, maintenance_margin_rate=self.s.maintenance_margin_rate,
                            stale_after_ms=30_000)
        saved = self.store.get_state("paper_broker")
        if saved:
            paper.import_state(saved)
        self.paper = paper
        self.broker = paper
        self.n.info("PAPER_BROKER_READY", instruments=len(self.instruments), source=source)

    def save_runtime(self) -> None:
        self.store.set_state("tracker", self.tracker.to_dict())
        if self.paper is not None:
            self.store.set_state("paper_broker", self.paper.export_state())

    # -- fills -----------------------------------------------------------------------------------------------

    async def on_fill(self, fill: LeaderFill, is_history: bool) -> None:
        async with self.lock:
            if self.store.has_fill(fill.tid):
                return
            if is_history and (self.last_seen_ms is None or fill.time_ms <= self.last_seen_ms):
                self.store.insert_fill(fill)  # history only: stored, never traded
                return
            if not self.store.insert_fill(fill):
                return
            events, drift = self.tracker.on_fill(fill)
            if drift is not None:
                self.n.warning("LEADER_STATE_DRIFT", coin=drift.coin, tracker=str(drift.tracker_position),
                               leader=str(drift.snapshot_position), source="fill")
            for ev in events:
                await self.engine.handle(ev)
            self.last_seen_ms = max(self.last_seen_ms or 0, fill.time_ms)
            self.store.set_state("last_fill_ms", self.last_seen_ms)
            self.save_runtime()

    # -- loops -----------------------------------------------------------------------------------------------

    async def snapshot_once(self, *, seed: bool = False) -> None:
        try:
            snap = await self.client.snapshot(self.s.leader_address)
        except Exception as exc:  # noqa: BLE001
            self.risk.on_snapshot_result(False)
            self.n.warning("LEADER_SNAPSHOT_FAILED", error=str(exc), failures=self.risk.snapshot_failures)
            return
        self.risk.on_snapshot_result(True)
        self.last_snapshot_ms = snap.taken_at_ms
        self.risk.on_leader_account_value(snap.account_value)
        async with self.lock:
            if seed:
                self.tracker.seed(snap)
                self.n.info("TRACKER_SEEDED", pre_existing={c: str(p) for c, p in snap.positions.items()})
            else:
                for d in self.tracker.on_snapshot(snap):
                    self.n.warning("LEADER_STATE_DRIFT", coin=d.coin, tracker=str(d.tracker_position),
                                   leader=str(d.snapshot_position), source="snapshot")
            self.store.set_state("leader_snapshot", {
                "taken_at_ms": snap.taken_at_ms, "account_value": str(snap.account_value),
                "positions": {c: str(p) for c, p in snap.positions.items()},
                "entry_px": {c: str(p) for c, p in snap.entry_px.items()}})
            self.save_runtime()

    async def snapshot_loop(self) -> None:
        while True:
            await asyncio.sleep(self.s.leader_snapshot_seconds)
            await self.snapshot_once()

    async def reconcile_loop(self) -> None:
        while True:
            async with self.lock:
                await self.recon.run_once()
                self.save_runtime()
            self.write_heartbeat()
            await asyncio.sleep(self.s.reconcile_seconds)

    async def control_loop(self) -> None:
        """`loco-copy kill --close-all` from another terminal asks the running bot to do it (one writer)."""
        while True:
            await asyncio.sleep(2)
            wanted = with_mode_override(self.base, self.store.get_state("mode_override"))
            if (wanted.copy_enabled, wanted.copy_observe_only) != (self.s.copy_enabled, self.s.copy_observe_only):
                async with self.lock:
                    self.s = wanted
                    self.engine.use_settings(wanted)
                    self.recon.use_settings(wanted)
                self.n.warning("MODE_CHANGED", copy_enabled=wanted.copy_enabled,
                               observe_only=wanted.copy_observe_only)
                self.write_heartbeat()
            if self.store.get_state("close_all_requested"):
                async with self.lock:
                    await close_all(self.store, self.engine, self.n)
                    self.store.set_state("close_all_requested", False)
                    self.save_runtime()

    async def paper_price_loop(self) -> None:
        assert self.paper is not None
        while True:
            try:
                mids = await self.client.all_mids()
                async with self.lock:
                    for coin, mid in mids.items():
                        m = self.mapper.map(coin)
                        if m is not None and m.symbol in self.instruments:
                            self.paper.set_price(m.symbol, mid * m.price_multiplier)
            except Exception as exc:  # noqa: BLE001
                self.n.warning("PAPER_PRICES_FAILED", error=str(exc))
            await asyncio.sleep(2)

    def write_heartbeat(self) -> None:
        acct = None
        with suppress(Exception):
            acct = self.broker.account()
        self.store.set_state("runtime", {
            "pid": os.getpid(), "updated_ms": now_ms(), "broker": self.broker.name, "env": self.s.broker_env,
            "copy_enabled": self.s.copy_enabled, "observe_only": self.s.copy_observe_only,
            "ws_connected": bool(self.feed and self.feed.connected),
            "ws_reconnects": self.feed.reconnects if self.feed else 0,
            "last_frame_ms": self.risk.last_frame_ms, "last_snapshot_ms": self.last_snapshot_ms,
            "snapshot_failures": self.risk.snapshot_failures,
            "equity": str(acct.equity) if acct else None, "available": str(acct.available) if acct else None})

    # -- main ------------------------------------------------------------------------------------------------

    async def run(self) -> None:
        await self.build_broker()
        self.engine = CopyEngine(store=self.store, broker=self.broker, settings=self.s, risk=self.risk,
                                 notifier=self.n, mapper=self.mapper, tracker=self.tracker,
                                 instruments=self.instruments)
        self.recon = Reconciler(store=self.store, broker=self.broker, settings=self.s, notifier=self.n,
                                tracker=self.tracker, risk=self.risk, engine=self.engine, run_sync=asyncio.to_thread)
        self.n.info("STARTING", broker=self.broker.name, env=self.s.broker_env, copy_enabled=self.s.copy_enabled,
                    observe_only=self.s.copy_observe_only, leader=self.s.leader_address, restored=self.restored)
        tasks: list[asyncio.Task[None]] = []
        if self.paper is not None:
            tasks.append(asyncio.create_task(self.paper_price_loop()))
            await asyncio.sleep(3)  # first prices in before anything is sized
        async with self.lock:
            await self.engine.recover()
        if self.restored and self.last_seen_ms is not None:
            for fill in await self.client.fills_since(self.s.leader_address, self.last_seen_ms + 1):
                await self.on_fill(fill, False)
        await self.snapshot_once(seed=not self.restored)
        if self.last_seen_ms is None:
            self.last_seen_ms = self.last_snapshot_ms or now_ms()
            self.store.set_state("last_fill_ms", self.last_seen_ms)

        self.feed = HyperliquidFeed(
            ws_url=self.s.hl_ws_url, user=self.s.leader_address, client=self.client, on_fill=self.on_fill,
            on_frame=self.risk.on_frame, last_seen_ms=lambda: self.last_seen_ms,
            on_connected=lambda: self.n.info("WS_CONNECTED"),
            on_error=lambda err: self.n.warning("WS_DISCONNECTED", error=err))
        tasks += [asyncio.create_task(self.feed.run()), asyncio.create_task(self.snapshot_loop()),
                  asyncio.create_task(self.reconcile_loop()), asyncio.create_task(self.control_loop())]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            self.save_runtime()
            await self.client.close()
            self.n.info("STOPPED")


async def close_all(store: Store, engine: CopyEngine, n: Notifier) -> None:
    for copy in store.copies_with_status(CopyStatus.OPEN):
        ok = await engine.force_close(copy, reason="KILL", status=CopyStatus.CLOSED_MANUAL)
        n.critical("KILL_CLOSE", coin=copy.coin, symbol=copy.symbol, ok=ok)


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
