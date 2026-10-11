"""Risk guard (botup.md آ§10). Every breaker only blocks NEW opens -- exits always go through.

- Kill switch: DB flag, or the file data/STOP (works even if the CLI is unavailable).
- Daily loss: equity more than DAILY_LOSS_LIMIT_PCT below the UTC day's starting equity.
- Leader breaker: Loco's account value more than LEADER_DD_BREAKER_PCT below its peak since start. Stays
  tripped until `loco-copy resume` (which also resets the peak to the current value).
- Latency breaker: no WS frame (pongs count) for STALE_FEED_SECONDS, or 3 snapshot failures in a row.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from loco_copy.config import Settings
from loco_copy.constants import Rejection
from loco_copy.notify import Notifier
from loco_copy.store import Store


def _now_ms() -> int:
    return int(time.time() * 1000)


class RiskGuard:
    def __init__(self, store: Store, settings: Settings, notifier: Notifier, *, monitor_feed: bool = True,
                 clock: Callable[[], int] = _now_ms) -> None:
        self._store = store
        self._s = settings
        self._n = notifier
        self._monitor_feed = monitor_feed
        self._clock = clock
        self.last_frame_ms: int | None = None
        self.last_equity: Decimal | None = None
        self.snapshot_failures = 0

    # -- kill switch -------------------------------------------------------------------------------------------

    def kill_switch_on(self) -> bool:
        return bool(self._store.get_state("kill_switch", False)) or self._s.stop_file.exists()

    def engage_kill(self, reason: str) -> None:
        self._store.set_state("kill_switch", True)
        self._n.critical("KILL_SWITCH_ENGAGED", reason=reason)

    def release(self) -> None:
        self._store.set_state("kill_switch", False)
        self._store.set_state("leader_breaker_tripped", False)
        self._store.set_state("leader_peak", None)
        if self._s.stop_file.exists():
            self._s.stop_file.unlink()
        self._n.warning("KILL_SWITCH_RELEASED")

    # -- daily loss -------------------------------------------------------------------------------------------

    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock() / 1000, UTC).strftime("%Y-%m-%d")

    def on_equity(self, equity: Decimal) -> None:
        self.last_equity = equity
        if self._store.get_state("day_start_date") != self._today():
            self._store.set_state("day_start_date", self._today())
            self._store.set_state("day_start_equity", str(equity))

    def daily_loss_hit(self, equity: Decimal) -> bool:
        # A day start recorded on an earlier UTC day is stale: the limit is per day, so a new day starts clean
        # even if nothing has refreshed the day start yet (otherwise one bad day could block opens forever).
        if self._store.get_state("day_start_date") != self._today():
            return False
        start = self._store.get_state("day_start_equity")
        if start is None:
            return False
        return equity < Decimal(start) * (1 - self._s.daily_loss_limit_pct)

    # -- leader breaker ---------------------------------------------------------------------------------------

    def on_leader_account_value(self, value: Decimal) -> None:
        if value <= 0:
            return  # unified/portfolio-margin wallets can report 0 here; no signal, never a trip
        peak_raw = self._store.get_state("leader_peak")
        peak = max(Decimal(peak_raw), value) if peak_raw is not None else value
        self._store.set_state("leader_peak", str(peak))
        if value < peak * (1 - self._s.leader_dd_breaker_pct) and not self._store.get_state("leader_breaker_tripped"):
            self._store.set_state("leader_breaker_tripped", True)
            self._n.critical("LEADER_BREAKER_TRIPPED", leader_value=str(value), peak=str(peak))

    def leader_breaker_tripped(self) -> bool:
        return bool(self._store.get_state("leader_breaker_tripped", False))

    # -- feed latency -----------------------------------------------------------------------------------------

    def on_frame(self) -> None:
        self.last_frame_ms = self._clock()

    def on_snapshot_result(self, ok: bool) -> None:
        self.snapshot_failures = 0 if ok else self.snapshot_failures + 1

    def latency_breaker(self) -> bool:
        if not self._monitor_feed:
            return False
        if self.snapshot_failures >= 3:
            return True
        return self.last_frame_ms is None or self._clock() - self.last_frame_ms > self._s.stale_feed_seconds * 1000

    # -- the one question the rules ask ------------------------------------------------------------------------

    def open_block(self, equity: Decimal | None) -> Rejection | None:
        if self.kill_switch_on():
            return Rejection.KILL_SWITCH
        if equity is not None and self.daily_loss_hit(equity):
            return Rejection.DAILY_LOSS_LIMIT
        if self.leader_breaker_tripped():
            return Rejection.LEADER_BREAKER
        if self.latency_breaker():
            return Rejection.LATENCY_BREAKER
        return None
