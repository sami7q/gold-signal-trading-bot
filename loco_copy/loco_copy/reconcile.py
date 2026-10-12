"""DB vs exchange vs Loco (botup.md §7). Runs at start-up and every RECONCILE_SECONDS.

- DB says OPEN, exchange has no position -> resolve TP / LIQ / MANUAL from closed PnL, else UNKNOWN.
- Exchange position with no DB copy -> ORPHAN_POSITION critical alert; never auto-closed.
- Loco's episode is over but our copy is still open -> after MISSED_EXIT_SECONDS, auto-close
  (CLOSED_RECONCILED). This is the only automatic correction.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal

from loco_copy.brokers.base import Broker
from loco_copy.config import Settings
from loco_copy.constants import CopyStatus
from loco_copy.engine import CopyEngine, RunSync
from loco_copy.models import ClosedTrade, Copy
from loco_copy.notify import Notifier
from loco_copy.risk import RiskGuard
from loco_copy.store import Store
from loco_copy.tracker import LeaderTracker

TP_TOLERANCE = Decimal("0.002")  # an exit within 0.2 % of our TP counts as the TP


def _now_ms() -> int:
    return int(time.time() * 1000)


def classify_close(copy: Copy, closes: list[ClosedTrade]) -> tuple[CopyStatus, ClosedTrade | None]:
    if not closes:
        return CopyStatus.UNKNOWN, None
    last = max(closes, key=lambda c: c.closed_at_ms)
    if any(c.is_liquidation for c in closes):
        return CopyStatus.CLOSED_LIQ, last
    if copy.tp_price is not None:
        tp = copy.tp_price
        hit = last.avg_exit_price >= tp * (1 - TP_TOLERANCE) if copy.side > 0 else \
            last.avg_exit_price <= tp * (1 + TP_TOLERANCE)
        if hit:
            return CopyStatus.CLOSED_TP, last
    return CopyStatus.CLOSED_MANUAL, last


class Reconciler:
    """One per lane. `account_symbols` returns every symbol any lane on the same exchange account has a live
    copy on: a position another lane holds there is known, not an orphan. `check_orphans` is true for one lane
    per account, so a real orphan is reported once."""

    def __init__(self, *, store: Store, broker: Broker, settings: Settings, notifier: Notifier,
                 tracker: LeaderTracker, risk: RiskGuard, engine: CopyEngine, run_sync: RunSync,
                 clock: Callable[[], int] = _now_ms, account_symbols: Callable[[], set[str]] | None = None,
                 check_orphans: bool = True) -> None:
        self._store = store
        self.leader = settings.lane
        self._account_symbols = account_symbols or (lambda: {c.symbol for c in store.live_copies(leader=self.leader)})
        self._check_orphans = check_orphans
        self._broker = broker
        self._s = settings
        self._n = notifier
        self._tracker = tracker
        self._risk = risk
        self._engine = engine
        self._run = run_sync
        self._clock = clock
        self._episode_over_since: dict[int, int] = {}

    def use_settings(self, settings: Settings) -> None:
        self._s = settings

    async def prefetch(self) -> None:
        """Network reads done before the app's lock is taken (a copy is never held up by reconciliation)."""
        return None

    async def run_once(self) -> None:
        try:
            account = await self._run(self._broker.account)
            self._risk.on_equity(account.equity)
            positions = {p.symbol: p for p in await self._run(self._broker.positions)}
        except Exception as exc:  # noqa: BLE001 - try again next cycle
            self._n.warning("RECONCILE_BROKER_UNAVAILABLE", error=str(exc))
            return

        open_copies = self._store.copies_with_status(CopyStatus.OPEN, leader=self.leader)
        for copy in open_copies:
            pos = positions.get(copy.symbol)
            if pos is None:
                await self._resolve_vanished(copy)
            elif pos.qty != copy.qty_open or pos.side != copy.side:
                self._n.warning("POSITION_MISMATCH", leader=self.leader, coin=copy.coin, symbol=copy.symbol,
                                db_qty=str(copy.qty_open),
                                exchange_qty=str(pos.qty), exchange_side=pos.side)

        if self._check_orphans:
            tracked = self._account_symbols()
            for sym, pos in positions.items():
                if sym not in tracked:
                    self._n.critical("ORPHAN_POSITION", symbol=sym, side=pos.side, qty=str(pos.qty))

        await self._missed_exits()

    async def _resolve_vanished(self, copy: Copy) -> None:
        try:
            closes = await self._run(self._broker.closed_trades, copy.symbol, (copy.opened_at or 0) - 1)
        except Exception as exc:  # noqa: BLE001
            self._n.warning("CLOSED_PNL_UNAVAILABLE", symbol=copy.symbol, error=str(exc))
            return
        status, last = classify_close(copy, closes)
        if last is None:
            copy.status, copy.closed_at, copy.close_reason = status, self._clock(), "position gone, no close record"
            copy.qty_open = Decimal(0)
            self._store.update_copy(copy)
            self._n.critical("COPY_UNKNOWN", leader=self.leader, coin=copy.coin, symbol=copy.symbol,
                             episode=copy.episode_id)
            return
        # we booked the opening fee when the copy opened; the exchange's figure may include it again
        pnl = sum((c.realized_pnl + c.open_fee for c in closes), Decimal(0))
        self._engine.book_exchange_close(copy, qty=copy.qty_open, px=last.avg_exit_price, pnl=pnl, status=status,
                                         reason=status.removeprefix("CLOSED_"))
        level = "INFO" if status == CopyStatus.CLOSED_TP else "WARNING"
        self._n.emit(level, f"COPY_{status}", leader=self.leader, coin=copy.coin, symbol=copy.symbol,
                     episode=copy.episode_id,
                     exit=str(last.avg_exit_price), pnl=str(pnl))

    async def _missed_exits(self) -> None:
        now = self._clock()
        live = self._store.copies_with_status(CopyStatus.OPEN, leader=self.leader)
        observed = [c for c in self._store.copies_with_status(CopyStatus.OBSERVED_ONLY, leader=self.leader)
                    if c.closed_at is None]
        alive_ids = set()
        for copy in live + observed:
            st = self._tracker.state(copy.coin)
            if st.position != 0 and st.episode_id == copy.episode_id:
                continue  # Loco still in this episode
            if copy.status == CopyStatus.OBSERVED_ONLY:
                copy.qty_open, copy.status = Decimal(0), CopyStatus.OBSERVED_ONLY
                copy.closed_at, copy.close_reason = now, "RECONCILED"
                self._store.update_copy(copy)
                continue
            assert copy.id is not None
            alive_ids.add(copy.id)
            since = self._episode_over_since.setdefault(copy.id, now)
            if now - since < self._s.missed_exit_seconds * 1000:
                continue
            ok = await self._engine.force_close(copy, reason="RECONCILED", status=CopyStatus.CLOSED_RECONCILED)
            if ok:
                self._n.critical("MISSED_EXIT_AUTO_CLOSED", leader=self.leader, coin=copy.coin, symbol=copy.symbol,
                                 episode=copy.episode_id, waited_s=(now - since) // 1000)
                self._episode_over_since.pop(copy.id, None)
        for cid in list(self._episode_over_since):
            if cid not in alive_ids:
                self._episode_over_since.pop(cid, None)
