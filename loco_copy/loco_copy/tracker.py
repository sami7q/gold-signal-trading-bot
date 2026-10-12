"""Rebuilds Loco's position per coin from his fills and emits OPEN / ADD / REDUCE / CLOSE / FLIP.

Pure -- no I/O, no clock. Episode logic ported from the research project's `hl_common.reconstruct_trades()`
(copied, not imported), with the bot's extra rule: never guess. Whenever the tracker's own position and the
leader's reality disagree, the coin is resynced and marked unsynced, and it gets no new copies until it has
been flat once.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from itertools import groupby
from typing import Any

from loco_copy.constants import EventType
from loco_copy.models import DriftInfo, LeaderEvent, LeaderFill, LeaderSnapshot

ZERO = Decimal(0)


def is_main_perp(coin: str) -> bool:
    """Hyperliquid main perp dex only: spot is `@123` / `X/USDC`, builder dexes are `dex:COIN`, outcome
    contracts are `#123` (no candles, not on Bybit)."""
    return bool(coin) and coin[0] not in "@#" and "/" not in coin and ":" not in coin


def _sign(x: Decimal) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


def aggregate_burst(fills: list[LeaderFill]) -> list[LeaderFill]:
    """Merges consecutive pieces of one order (same coin, oid, side and millisecond, chained start -> end) into
    one fill: start of the first, total size, volume-weighted price, first tid. Input in `chain_order`. One
    order filled in 15 pieces is then one leader move -- one copy order instead of 15."""
    out: list[LeaderFill] = []
    for f in fills:
        prev = out[-1] if out else None
        if (prev is not None and f.oid is not None and prev.oid == f.oid and prev.coin == f.coin
                and prev.side == f.side and prev.time_ms == f.time_ms and prev.new_position == f.start_position):
            size = prev.sz + f.sz
            out[-1] = replace(prev, sz=size, px=(prev.px * prev.sz + f.px * f.sz) / size,
                              received_at_ms=max(prev.received_at_ms, f.received_at_ms))
        else:
            out.append(f)
    return out


def chain_order(fills: list[LeaderFill]) -> list[LeaderFill]:
    """Puts fills in the order they happened. Hyperliquid splits one order into many fills that share a
    millisecond, and tids are not sequential, so sorting by (time, tid) scrambles them -- which made the tracker
    see drift in the middle of an open and then miss the close (BTC, 2026-10-11). Every fill states the
    position it started from, so within one coin's burst at one millisecond each fill follows the one that
    ended where it starts."""
    out: list[LeaderFill] = []
    for _key, burst in groupby(sorted(fills, key=lambda f: (f.time_ms, f.coin)), key=lambda f: (f.time_ms, f.coin)):
        left = sorted(burst, key=lambda f: f.tid)
        if len(left) > 1:
            ends = {f.new_position for f in left}
            cur: LeaderFill | None = next((f for f in left if f.start_position not in ends), left[0])
            while cur is not None:
                out.append(cur)
                left.remove(cur)
                end = cur.new_position
                cur = next((f for f in left if f.start_position == end), left[0] if left else None)
        out += left
    return out


@dataclass(slots=True)
class CoinState:
    position: Decimal = ZERO
    episode_id: int = 0
    synced: bool = True
    side: int = 0
    last_fill_received_ms: int = 0
    flat_since_ms: int | None = 0
    #: Snapshot value seen once that disagrees with us; acted on only if the next snapshot agrees with it.
    pending_drift: Decimal | None = None
    #: The current episode began with an OPEN we emitted, so it may have a copy that must follow its exit.
    opened: bool = False
    #: Hyperliquid order id of the fill that opened the episode: its other pieces are the open, not adds.
    open_oid: int | None = None


class LeaderTracker:
    def __init__(self, *, drift_confirmations: int = 2) -> None:
        if drift_confirmations not in (1, 2):
            raise ValueError("drift_confirmations must be 1 or 2")
        self._confirm = drift_confirmations
        self._coins: dict[str, CoinState] = {}

    # -- queries -----------------------------------------------------------------------------------------

    def state(self, coin: str) -> CoinState:
        return self._coins.setdefault(coin, CoinState())

    def position(self, coin: str) -> Decimal:
        return self.state(coin).position

    def is_synced(self, coin: str) -> bool:
        return self.state(coin).synced

    def positions(self) -> dict[str, Decimal]:
        return {c: s.position for c, s in self._coins.items() if s.position != 0}

    # -- seeding / persistence -----------------------------------------------------------------------------

    def seed(self, snapshot: LeaderSnapshot) -> None:
        """First start: every position Loco already holds is pre-existing and is never copied."""
        for coin, pos in snapshot.positions.items():
            if not is_main_perp(coin) or pos == 0:
                continue
            st = self.state(coin)
            st.position, st.synced, st.side, st.flat_since_ms, st.opened = pos, False, _sign(pos), None, False

    def to_dict(self) -> dict[str, Any]:
        return {c: {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(s).items()}
                for c, s in self._coins.items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, drift_confirmations: int = 2) -> LeaderTracker:
        t = cls(drift_confirmations=drift_confirmations)
        for coin, raw in data.items():
            pd = raw.get("pending_drift")
            t._coins[coin] = CoinState(
                position=Decimal(raw["position"]),
                episode_id=int(raw["episode_id"]),
                synced=bool(raw["synced"]),
                side=int(raw["side"]),
                last_fill_received_ms=int(raw.get("last_fill_received_ms") or 0),
                flat_since_ms=raw.get("flat_since_ms"),
                pending_drift=Decimal(pd) if pd is not None else None,
                opened=bool(raw.get("opened", False)),
                open_oid=raw.get("open_oid"),
            )
        return t

    # -- fills -----------------------------------------------------------------------------------------------

    def on_fill(self, fill: LeaderFill) -> tuple[list[LeaderEvent], DriftInfo | None]:
        if not is_main_perp(fill.coin):
            return [], None
        st = self.state(fill.coin)
        st.last_fill_received_ms = max(st.last_fill_received_ms, fill.received_at_ms)
        st.pending_drift = None
        start, new = fill.start_position, fill.new_position

        if st.synced and start != st.position:
            # Our own position disagrees with what the leader's fill says it was -> a gap, a duplicate we
            # didn't catch, or out-of-order delivery. Resync and stop copying this coin until it is flat.
            drift = DriftInfo(fill.coin, st.position, start)
            exits = self._exits(st, fill, start, new)
            self._set_position(st, new, fill.received_at_ms)
            st.synced = new == 0
            if new != 0 and _sign(start) * _sign(new) < 0:  # flipped: the new side is not ours
                st.episode_id, st.side, st.opened = st.episode_id + 1, _sign(new), False
            return exits, drift

        if not st.synced:
            if start != 0:
                exits = self._exits(st, fill, start, new)
                self._set_position(st, new, fill.received_at_ms)
                if new == 0:
                    st.synced = True
                elif _sign(start) * _sign(new) < 0:
                    # Synced from here, but this new-side position was never opened by us: give it its own
                    # episode number so its later exits are recognised (and find no copy).
                    st.synced, st.episode_id, st.side, st.opened = True, st.episode_id + 1, _sign(new), False
                return exits, None
            st.synced = True  # start == 0: a clean start, treat normally

        events = self._classify(st, fill, start, new)
        self._set_position(st, new, fill.received_at_ms)
        return events, None

    @staticmethod
    def _event(st: CoinState, f: LeaderFill, t: EventType, fraction: Decimal | None = None, *,
               before: Decimal | None = None, after: Decimal | None = None) -> LeaderEvent:
        return LeaderEvent(type=t, coin=f.coin, episode_id=st.episode_id, side=st.side, leader_px=f.px,
                           leader_time_ms=f.time_ms, detected_at_ms=f.received_at_ms, tid=f.tid, fraction=fraction,
                           pos_before=f.start_position if before is None else before,
                           pos_after=f.new_position if after is None else after)

    def _exits(self, st: CoinState, f: LeaderFill, start: Decimal, new: Decimal) -> list[LeaderEvent]:
        """Exits while unsynced. The exchange saying the leader is now flat, or on the other side, is a fact,
        not a guess: whatever we copied for the current episode must go now, not 60 s later when the
        reconciler notices. Only for an episode we saw open -- a pre-existing position has nothing to close."""
        if st.opened and (new == 0 or _sign(start) * _sign(new) < 0):
            return [self._event(st, f, EventType.CLOSE, after=ZERO)]
        return []

    def _classify(self, st: CoinState, f: LeaderFill, start: Decimal, new: Decimal) -> list[LeaderEvent]:
        def ev(t: EventType, fraction: Decimal | None = None) -> LeaderEvent:
            return self._event(st, f, t, fraction)

        if start == 0:
            if new == 0:
                return []
            st.episode_id += 1
            st.side, st.opened, st.open_oid = _sign(new), True, f.oid
            return [ev(EventType.OPEN)]
        if new == 0:
            return [ev(EventType.CLOSE)]
        if _sign(start) == _sign(new):
            if abs(new) > abs(start):
                # Every add is reported, also later pieces of the opening order: cap20 sizes adds from them
                # (first_fill records them as IGNORED_ADD). Pieces arriving together are merged first by
                # `aggregate_burst`, so one order is one event per delivery, not one per piece.
                return [ev(EventType.ADD)]
            return [ev(EventType.REDUCE, (abs(start) - abs(new)) / abs(start))]
        flip = self._event(st, f, EventType.FLIP, after=ZERO)  # closes the old side ...
        st.episode_id += 1
        st.side, st.opened, st.open_oid = _sign(new), True, f.oid
        return [flip, self._event(st, f, EventType.OPEN, before=ZERO)]  # ... and opens |new| on the other

    @staticmethod
    def _set_position(st: CoinState, new: Decimal, at_ms: int) -> None:
        if new == 0 and st.position != 0:
            st.flat_since_ms = at_ms
        elif new != 0:
            st.flat_since_ms = None
        if new == 0:
            st.opened, st.open_oid = False, None  # the episode is over
        st.position = new

    # -- snapshots ----------------------------------------------------------------------------------------

    def on_snapshot(self, snapshot: LeaderSnapshot) -> list[DriftInfo]:
        """Compare with the leader's real positions. A coin that received a fill after the snapshot request was
        sent is skipped (the snapshot can't reflect it yet). A disagreement must show up on `drift_confirmations`
        consecutive snapshots before it is acted on, so a fill still in flight on the WS never causes a false
        resync."""
        drifts: list[DriftInfo] = []
        coins = {c for c in snapshot.positions if is_main_perp(c)} | set(self.positions())
        for coin in sorted(coins):
            st = self.state(coin)
            snap = snapshot.positions.get(coin, ZERO)
            if st.last_fill_received_ms >= snapshot.taken_at_ms:
                st.pending_drift = None
                continue
            if snap == st.position:
                st.pending_drift = None
                continue
            if self._confirm == 2 and st.pending_drift != snap:
                st.pending_drift = snap
                continue
            drifts.append(DriftInfo(coin, st.position, snap))
            self._set_position(st, snap, snapshot.taken_at_ms)
            st.synced = snap == 0
            st.side = _sign(snap)
            st.pending_drift = None
        return drifts
