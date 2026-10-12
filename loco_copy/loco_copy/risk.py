"""Risk guard (botup.md §10, v3 §5.7). Every breaker only blocks NEW opens -- exits always go through.

Global (every lane):
- Kill switch: DB flag, or the file data/STOP (works even if the CLI is unavailable).
- Latency breaker: no WS frame (pongs count) for STALE_FEED_SECONDS -- one WS serves every lane.

Per lane (one RiskGuard per copied leader):
- Pause: the leader's on/off switch on the dashboard (`paused:<label>`).
- Daily loss: equity more than DAILY_LOSS_LIMIT_PCT below the UTC day's starting equity.
- Leader breaker: the leader's account value more than LEADER_DD_BREAKER_PCT below its peak since start. Stays
  tripped until resumed (which also resets the peak to the current value).
- Snapshot failures: 3 failed snapshots of this leader in a row.

cap20_cross (botup.md v4 §5, v5 §V5.3):
- Feed outage: no good Hyperliquid contact (WS frame or REST answer) for FEED_OUTAGE_BLOCK_SECONDS -> no new
  trades or adds on any lane.
- Account kill (AccountGuard, one per account): equity below (1 - ACCOUNT_KILL_DD_PCT) x the account's peak ->
  no new trades or adds on any lane trading there, until `resume`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from loco_copy.config import DEFAULT_LABEL, Settings
from loco_copy.constants import Rejection
from loco_copy.notify import Notifier
from loco_copy.store import Store, lane_key


def _now_ms() -> int:
    return int(time.time() * 1000)


def release_everything(store: Store, settings: Settings, notifier: Notifier) -> None:
    """`resume` with no leader: the kill switch, data/STOP, every lane's leader breaker and every account kill."""
    for label in settings.labels:
        RiskGuard(store, settings.for_lane(label), notifier).reset_breaker()
    RiskGuard(store, settings, notifier).release()
    for key in store.state_keys("account_kill:"):
        AccountGuard(store, key.removeprefix("account_kill:"), settings.account_kill_dd_pct, notifier).release()


class AccountGuard:
    """v5 account kill switch for one account (an exchange account or the virtual ledger): its equity peak is
    tracked, and equity below (1 - dd) x peak blocks new trades and adds of every lane trading there. Exits
    are never blocked. Stays tripped until `resume`, which also restarts the peak from the current equity."""

    def __init__(self, store: Store, account: str, dd: Decimal | None, notifier: Notifier) -> None:
        self._store = store
        self.account = account
        self._dd = dd
        self._n = notifier

    def on_equity(self, equity: Decimal) -> None:
        if self._dd is None or equity <= 0:
            return
        raw = self._store.get_state(f"account_peak:{self.account}")
        peak = max(Decimal(raw), equity) if raw is not None else equity
        self._store.set_state(f"account_peak:{self.account}", str(peak))
        if equity < peak * (1 - self._dd) and not self.tripped():
            self._store.set_state(f"account_kill:{self.account}", True)
            self._n.critical("ACCOUNT_KILL_TRIPPED", account=self.account, equity=str(equity), peak=str(peak),
                             limit=str(peak * (1 - self._dd)))

    def peak(self) -> Decimal | None:
        raw = self._store.get_state(f"account_peak:{self.account}")
        return Decimal(raw) if raw is not None else None

    def tripped(self) -> bool:
        return self._dd is not None and bool(self._store.get_state(f"account_kill:{self.account}", False))

    def release(self) -> None:
        self._store.set_state(f"account_kill:{self.account}", False)
        self._store.set_state(f"account_peak:{self.account}", None)


class RiskGuard:
    def __init__(self, store: Store, settings: Settings, notifier: Notifier, *, monitor_feed: bool = True,
                 clock: Callable[[], int] = _now_ms) -> None:
        self._store = store
        self._s = settings
        self._n = notifier
        self._monitor_feed = monitor_feed
        self._clock = clock
        self.lane = settings.lane or DEFAULT_LABEL
        self.last_frame_ms: int | None = None
        self.last_hl_ok_ms: int | None = None
        self.last_equity: Decimal | None = None
        self.snapshot_failures = 0

    def _key(self, key: str) -> str:
        return lane_key(key, self.lane)

    # -- kill switch (global) and pause (this lane) ------------------------------------------------------------

    def kill_switch_on(self) -> bool:
        return bool(self._store.get_state("kill_switch", False)) or self._s.stop_file.exists()

    def engage_kill(self, reason: str) -> None:
        self._store.set_state("kill_switch", True)
        self._n.critical("KILL_SWITCH_ENGAGED", reason=reason)

    def release(self) -> None:
        """Global resume: kill switch, data/STOP and this lane's leader breaker. Lane pauses are the owner's
        per-leader switches and stay as they are."""
        self._store.set_state("kill_switch", False)
        self.reset_breaker()
        if self._s.stop_file.exists():
            self._s.stop_file.unlink()
        self._n.warning("KILL_SWITCH_RELEASED")

    def reset_breaker(self) -> None:
        self._store.set_state(self._key("leader_breaker_tripped"), False)
        self._store.set_state(self._key("leader_peak"), None)

    def paused(self) -> bool:
        return bool(self._store.get_state(self._key("paused"), False))

    def set_paused(self, paused: bool, by: str) -> None:
        self._store.set_state(self._key("paused"), paused)
        self._n.warning("LANE_PAUSED" if paused else "LANE_RESUMED", leader=self.lane, user=by)

    # -- daily loss -------------------------------------------------------------------------------------------

    def _today(self) -> str:
        return datetime.fromtimestamp(self._clock() / 1000, UTC).strftime("%Y-%m-%d")

    def on_equity(self, equity: Decimal) -> None:
        self.last_equity = equity
        if self._store.get_state(self._key("day_start_date")) != self._today():
            self._store.set_state(self._key("day_start_date"), self._today())
            self._store.set_state(self._key("day_start_equity"), str(equity))

    def daily_loss_hit(self, equity: Decimal) -> bool:
        # A day start recorded on an earlier UTC day is stale: the limit is per day, so a new day starts clean
        # even if nothing has refreshed the day start yet (otherwise one bad day could block opens forever).
        if self._store.get_state(self._key("day_start_date")) != self._today():
            return False
        start = self._store.get_state(self._key("day_start_equity"))
        limit = self._s.daily_loss_limit_pct
        if start is None or limit is None:  # None: off (cap20_cross)
            return False
        return equity < Decimal(start) * (1 - limit)

    # -- leader breaker ---------------------------------------------------------------------------------------

    def on_leader_account_value(self, value: Decimal) -> None:
        dd = self._s.leader_dd_breaker_pct
        if value <= 0 or dd is None:
            return  # 0: unified/portfolio-margin wallets report 0 here, no signal. None: off (cap20_cross)
        peak_raw = self._store.get_state(self._key("leader_peak"))
        peak = max(Decimal(peak_raw), value) if peak_raw is not None else value
        self._store.set_state(self._key("leader_peak"), str(peak))
        if value < peak * (1 - dd) and not self.leader_breaker_tripped():
            self._store.set_state(self._key("leader_breaker_tripped"), True)
            self._n.critical("LEADER_BREAKER_TRIPPED", leader=self.lane, leader_value=str(value), peak=str(peak))

    def leader_breaker_tripped(self) -> bool:
        return bool(self._store.get_state(self._key("leader_breaker_tripped"), False))

    # -- feed latency -----------------------------------------------------------------------------------------

    def on_frame(self) -> None:
        self.last_frame_ms = self._clock()

    def on_hl_ok(self) -> None:
        """A REST answer from Hyperliquid (snapshot, fill poll, portfolio): the feed is reachable."""
        self.last_hl_ok_ms = self._clock()

    def feed_outage(self) -> bool:
        """cap20: no WS frame and no REST answer for FEED_OUTAGE_BLOCK_SECONDS."""
        if not self._monitor_feed:
            return False
        last = max(self.last_frame_ms or 0, self.last_hl_ok_ms or 0)
        return self._clock() - last > self._s.feed_outage_block_seconds * 1000

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
        if self.paused():
            return Rejection.LANE_PAUSED
        if equity is not None and self.daily_loss_hit(equity):
            return Rejection.DAILY_LOSS_LIMIT
        if self.leader_breaker_tripped():
            return Rejection.LEADER_BREAKER
        if self.latency_breaker():
            return Rejection.LATENCY_BREAKER
        return None
