"""«20 % cap, cross» -- botup.md v4 §4 (owner's spec docs/strategy/Loco_Copy_Strategy_Final_Bybit.md), with the
v5 shared account (§V5.3). Mirrors the research engine analysis365/copysim.py fill for fill:

    OPEN    E0 = account equity, W0 = leader equity -> R = K x E0 / W0 and CAP = cap% x L x E0, fixed for the
            trade; then the opening fill is treated as an add of |after|.
    ADD     pending += dq x R, limited by the cap room, free margin and the exposure limit; sent once it reaches
            the minimum order. Room exhausted -> capped: later adds ignored, reduces still followed.
    REDUCE  same fraction as the leader; pending scaled by the same fraction.
    CLOSE   100 %. FLIP = CLOSE + a new trade (the tracker emits both).

Exits only with the leader: no take profit, no stop loss. Exits are never blocked by any rule.

A trade lives on one account ("book") for its whole life: the exchange (EXECUTE with the gate open) or the
virtual account (OBSERVE -- the v5 observe ledger, filled at Bybit's live bid / ask). Several leaders on one
account net out in one-way mode (each keeps its own share in its copies), so exits there are plain market
orders, not reduce-only. Copies opened by the v3 first-fill strategy are still followed by the v3 logic.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from loco_copy.brokers.base import Broker
from loco_copy.config import Settings
from loco_copy.constants import CopyStatus, Decision, EventType, OrderKind, OrderStatus, Rejection
from loco_copy.engine import CopyEngine, RunSync, _now_ms
from loco_copy.models import Account, Copy, InstrumentInfo, LeaderEvent, OrderResult, Quote
from loco_copy.notify import Notifier
from loco_copy.reconcile import Reconciler
from loco_copy.risk import AccountGuard, RiskGuard
from loco_copy.sizing import add_order_qty, cap_notional, copy_ratio, floor_to_step
from loco_copy.store import Store, lane_key
from loco_copy.symbols import SymbolMapper
from loco_copy.tracker import LeaderTracker, is_main_perp

EPS = Decimal("1e-9")
NO_LIMIT = Decimal("Infinity")
STRATEGY = "cap20_cross"
#: C-4: a leader snapshot older than this never moves money.
SNAPSHOT_MAX_AGE_MS = 90_000


@dataclass(slots=True)
class Book:
    """One account a cap20 trade can live on."""

    broker: Broker
    account: str  # name in logs, kill-switch state and equity history ("virtual", keyring service, ...)
    guard: AccountGuard | None = None
    #: Several lanes net out on this account: exits are plain market orders, never reduce-only.
    shared: bool = False


def reconcile_target(copy: Copy, leader_abs: Decimal, price: Decimal) -> Decimal:
    """botup.md v4 §5 reconcile.py: what we should hold for the leader's current |position|.
    Not capped: min(|szi| x R, CAP / avg). Capped: qty_at_cap x |szi| / |szi at cap|."""
    if leader_abs <= 0:
        return Decimal(0)
    if copy.capped and copy.qty_at_cap is not None and copy.leader_pos_at_cap:
        return copy.qty_at_cap * leader_abs / copy.leader_pos_at_cap
    assert copy.ratio_r is not None and copy.cap_notional is not None
    avg = copy.entry_price or price
    return min(leader_abs * copy.ratio_r, copy.cap_notional / avg)


class Cap20Engine(CopyEngine):
    def __init__(self, *, store: Store, real: Book, settings: Settings, risk: RiskGuard, notifier: Notifier,
                 mapper: SymbolMapper, tracker: LeaderTracker, instruments: dict[str, InstrumentInfo],
                 virtual: Book | None, clock: Callable[[], int] = _now_ms,
                 run_sync: RunSync = asyncio.to_thread) -> None:
        super().__init__(store=store, broker=real.broker, settings=settings, risk=risk, notifier=notifier,
                         mapper=mapper, tracker=tracker, instruments=instruments, clock=clock, run_sync=run_sync)
        self.real = real
        self.virtual = virtual

    # -- routing ------------------------------------------------------------------------------------------------

    @staticmethod
    def is_cap20(copy: Copy) -> bool:
        return copy.strategy == STRATEGY

    def _is_virtual(self, copy: Copy) -> bool:
        return self.is_cap20(copy) and copy.status == CopyStatus.OBSERVED_ONLY

    def book_of(self, copy: Copy) -> Book:
        if self._is_virtual(copy):
            if self.virtual is None:
                raise RuntimeError("observed cap20 copy but no virtual account")
            return self.virtual
        return self.real

    def _broker_for(self, copy: Copy) -> Broker:
        return self.book_of(copy).broker

    def _reduce_only(self, copy: Copy) -> bool:
        return not (self.is_cap20(copy) and self.book_of(copy).shared)

    def _closed_status(self, copy: Copy, status: CopyStatus) -> CopyStatus:
        return CopyStatus.OBSERVED_ONLY if self._is_virtual(copy) else status

    # -- entry point --------------------------------------------------------------------------------------------

    async def handle(self, event: LeaderEvent) -> Decision:
        if event.type == EventType.OPEN:
            return await self._c20_open(event)
        copy = self._store.get_copy(event.coin, event.episode_id, leader=self.leader)
        if copy is not None and not self.is_cap20(copy):
            return await super().handle(event)  # a v3 first-fill copy: followed the v3 way to its end
        if event.type == EventType.ADD:
            return await self._c20_add(event, copy)
        if event.type == EventType.REDUCE:
            return await self._c20_reduce(event, copy)
        return await self._c20_close(event, copy)  # CLOSE, FLIP

    # -- OPEN -----------------------------------------------------------------------------------------------------

    def _units(self, coin: str) -> Decimal:
        """Exchange units per leader unit: 1, or 1/1000 for a `1000XUSDT` contract (botup.md v4 C-1)."""
        sym = self._mapper.map(coin)
        return Decimal(1) / sym.price_multiplier if sym else Decimal(1)

    def _new_trade_block(self, book: Book) -> Rejection | None:
        """botup.md v4 §5 rules.py for OPEN and ADD (exits never come here)."""
        if self._risk.kill_switch_on():
            return Rejection.KILL_SWITCH
        if self._risk.paused():
            return Rejection.LANE_PAUSED
        if book.guard is not None and book.guard.tripped():
            return Rejection.ACCOUNT_KILL
        if self._risk.feed_outage():
            return Rejection.FEED_OUTAGE
        return None

    def _leader_equity(self) -> Decimal | None:
        raw = self._store.get_state(lane_key("leader_equity", self.leader))
        if not raw:
            return None
        if self._clock() - int(raw["taken_at_ms"]) > self._s.leader_equity_refresh_seconds * 1000:
            return None  # botup.md v4 §4: W0 at most LEADER_EQUITY_REFRESH_SECONDS (300 s) old
        value = Decimal(raw["value"])
        return value if value > 0 else None

    def open_trades(self) -> list[Copy]:
        """This lane's copies still holding (or observing) a position, on either book."""
        live = self._store.live_copies(leader=self.leader)
        return live + [c for c in self._store.copies_with_status(CopyStatus.OBSERVED_ONLY, leader=self.leader)
                       if c.closed_at is None]

    async def _c20_open(self, e: LeaderEvent) -> Decision:
        if not self._s.copy_enabled:
            return self._reject(e, Rejection.DISABLED)
        if not is_main_perp(e.coin):
            return self._reject(e, Rejection.UNSUPPORTED_MARKET)
        if not self._tracker.is_synced(e.coin):
            return self._reject(e, Rejection.PRE_EXISTING_POSITION)
        sym = self._mapper.map(e.coin)
        inst = self.instruments.get(sym.symbol) if sym else None
        if sym is None or inst is None:
            return self._reject(e, Rejection.SYMBOL_NOT_LISTED)  # the whole trade is skipped
        if self._store.get_copy(e.coin, e.episode_id, leader=self.leader) is not None:
            return self._reject(e, Rejection.DUPLICATE_EPISODE)
        if any(c.coin == e.coin for c in self.open_trades()):
            return self._reject(e, Rejection.COIN_ALREADY_OPEN)

        gated = not self._s.copy_observe_only and not self._s.allow_execute
        virtual = self._s.copy_observe_only or gated
        book = self.virtual if virtual else self.real
        if book is None:
            return self._reject(e, Rejection.BROKER_UNAVAILABLE)
        block = self._new_trade_block(book)
        if block is not None:
            return self._reject(e, block)
        w0 = self._leader_equity()
        if w0 is None:
            return self._reject(e, Rejection.LEADER_EQUITY_UNKNOWN)
        # speed: every read of the open in parallel -- one round trip to the exchange instead of five in a row
        reads: list[Awaitable[Any]] = [self._safe(book.broker.total_equity), self._safe(book.broker.quote, sym.symbol),
                                       self._safe(book.broker.account), self._open_notional(book.broker)]
        if not virtual:
            reads.append(self._leverage(book.broker, sym.symbol))
        e0, quote, account, open_notional, *lev = await asyncio.gather(*reads)
        if e0 is None or e0 <= 0:
            return self._reject(e, Rejection.BROKER_UNAVAILABLE)
        if book.guard is not None:
            book.guard.on_equity(e0)
            if book.guard.tripped():
                return self._reject(e, Rejection.ACCOUNT_KILL)
        if lev and not lev[0]:  # without cross margin + leverage the trade is not the strategy
            return self._reject(e, Rejection.BROKER_UNAVAILABLE)

        copy = Copy(coin=e.coin, symbol=sym.symbol, episode_id=e.episode_id, side=e.side,
                    status=CopyStatus.OBSERVED_ONLY if virtual else CopyStatus.OPEN, qty_initial=Decimal(0),
                    qty_open=Decimal(0), leader_entry_px=e.leader_px * sym.price_multiplier, margin=Decimal(0),
                    opened_at=self._clock(), latency_detect_ms=e.detected_at_ms - e.leader_time_ms, leader=self.leader,
                    strategy=STRATEGY, ratio_r=copy_ratio(self._s.k, e0, w0),
                    cap_notional=cap_notional(self._s.per_trade_margin_cap_pct, self._s.leverage, e0),
                    leader_pos=abs(e.pos_after) if e.pos_after is not None else None)
        copy = self._store.insert_copy(copy)
        self._n.info("CAP20_TRADE", leader=self.leader, coin=e.coin, episode=e.episode_id, side=e.side,
                     account=book.account, e0=str(e0), w0=str(w0), r=str(copy.ratio_r), cap=str(copy.cap_notional),
                     gated=gated)
        dq = abs(e.pos_after) if e.pos_after is not None else Decimal(0)
        return await self._grow(e, copy, inst, dq / sym.price_multiplier, opening=True, gated=gated,
                                pre=(quote, account, open_notional))

    # -- ADD ------------------------------------------------------------------------------------------------------

    def _no_copy(self, e: LeaderEvent, copy: Copy | None) -> Decision:
        detail = None if copy is None else f"copy is {copy.status} (closed)"
        self._event(e, Decision.NO_MATCHING_COPY, detail=detail)
        if e.type != EventType.ADD:
            self._n.info("NO_MATCHING_COPY", leader=self.leader, coin=e.coin, episode=e.episode_id,
                         type=str(e.type), detail=detail)
        return Decision.NO_MATCHING_COPY

    async def _c20_add(self, e: LeaderEvent, copy: Copy | None) -> Decision:
        if copy is None or copy.closed_at is not None:
            return self._no_copy(e, copy)
        inst = self.instruments.get(copy.symbol)
        if inst is None:
            return self._reject(e, Rejection.SYMBOL_NOT_LISTED)
        copy.leader_pos = abs(e.pos_after) if e.pos_after is not None else copy.leader_pos
        if copy.capped:
            self._store.update_copy(copy)
            self._event(e, Decision.ADD_CAPPED, detail="trade at its margin cap: add ignored")
            return Decision.ADD_CAPPED
        block = self._new_trade_block(self.book_of(copy))
        if block is not None:
            self._store.update_copy(copy)
            return self._reject(e, block)
        return await self._grow(e, copy, inst, e.qty_delta * self._units(copy.coin), opening=False, gated=False)

    async def _grow(self, e: LeaderEvent, copy: Copy, inst: InstrumentInfo, dq: Decimal, *, opening: bool,
                    gated: bool, pre: tuple[Quote | None, Account | None, Decimal] | None = None) -> Decision:
        """dq x R joins what is pending; one order once it reaches the minimum (botup.md v4 §4.2)."""
        assert copy.ratio_r is not None and copy.cap_notional is not None
        book = self.book_of(copy)
        if pre is None:  # an add: quote, account and exposure in parallel
            quote, account, open_notional = await asyncio.gather(
                self._safe(book.broker.quote, copy.symbol), self._safe(book.broker.account),
                self._open_notional(book.broker))
        else:
            quote, account, open_notional = pre
        gate = {"rejection": str(Rejection.EXECUTION_NOT_ALLOWED)} if gated else {}
        if quote is None or account is None:
            copy.pending_qty += dq * copy.ratio_r  # the leader's add is kept: the next add or reconcile places it
            self._store.update_copy(copy)
            return self._reject(e, Rejection.BROKER_UNAVAILABLE)
        px = quote.price_for(copy.side)
        plan = add_order_qty(pending=copy.pending_qty + dq * copy.ratio_r,
                             room_notional=copy.cap_notional - copy.cost_notional, price=px, instrument=inst,
                             available=account.available, leverage=self._s.leverage, exposure_left=NO_LIMIT,
                             min_notional=inst.min_notional or self._s.min_order_usdt)
        if plan.qty > 0 and self._over_exposure(open_notional, account.equity, plan.qty * px):
            # spec §7: total open notional would exceed 6 x equity -> this add is blocked (logged), never shrunk
            self._n.warning("EXPOSURE_LIMIT", leader=self.leader, coin=copy.coin, symbol=copy.symbol, tid=e.tid,
                            order_notional=str(plan.qty * px), equity=str(account.equity),
                            limit_x=str(self._s.max_total_notional_x_equity))
            if opening and copy.qty_open == 0:  # the whole trade is skipped (no new trade past the limit)
                copy.closed_at, copy.close_reason = self._clock(), "EXPOSURE_LIMIT"
                copy.status = self._closed_status(copy, CopyStatus.CLOSED_LEADER)
            self._store.update_copy(copy)
            return self._reject(e, Rejection.EXPOSURE_LIMIT)
        if plan.qty <= 0:
            copy.pending_qty = plan.pending
            if plan.capped:
                self._mark_capped(copy)
                self._store.update_copy(copy)
                self._event(e, Decision.ADD_CAPPED, detail=f"cap {copy.cap_notional:.2f} reached", our_px=px,
                            **gate)
                return Decision.ADD_CAPPED
            self._store.update_copy(copy)
            self._event(e, Decision.ADD_PENDING, detail=f"pending {plan.pending:.8g} below the minimum order",
                        our_px=px, **gate)
            return Decision.ADD_PENDING
        kind = "open" if opening and copy.qty_open == 0 else "add"
        client_id = f"{self.leader}:{copy.coin}:{copy.episode_id}:{kind}:{e.tid}"
        res = await self._order(copy, book.broker, copy.side, plan.qty, client_id, OrderKind.OPEN,
                                reduce_only=False)
        if res is None or not res.success or res.avg_price is None or res.filled_qty <= 0:
            copy.pending_qty = Decimal(0)
            self._store.update_copy(copy)
            error = None if res is None else res.error
            self._event(e, Decision.FAILED, detail=error)
            self._n.warning("CAP20_ORDER_FAILED", leader=self.leader, coin=copy.coin, symbol=copy.symbol, error=error)
            return Decision.FAILED
        self._book_entry(copy, e, res, first=copy.qty_open == 0)
        copy.pending_qty = Decimal(0)
        if not opening:
            copy.adds_done += 1
        if plan.capped or copy.cost_notional >= copy.cap_notional - EPS:
            self._mark_capped(copy)
        self._store.update_copy(copy)
        if not opening:
            decision = Decision.ADDED
        else:  # a virtual trade is observed, never "copied"
            decision = Decision.OBSERVED_ONLY if self._is_virtual(copy) else Decision.COPIED
        self._event(e, decision, our_px=res.avg_price,
                    detail=f"qty={res.filled_qty} px={res.avg_price} R={copy.ratio_r:.6g} "
                           f"cap_used={copy.cost_notional / copy.cap_notional:.1%}"
                           + (f" limited_by={plan.limited_by}" if plan.limited_by else ""), **gate)
        self._n.info("CAP20_FILL", leader=self.leader, tid=e.tid, coin=copy.coin, symbol=copy.symbol, side=copy.side,
                     qty=str(res.filled_qty), px=str(res.avg_price), r=str(copy.ratio_r), cap=str(copy.cap_notional),
                     equity=str(account.equity), reason=kind, account=book.account, cost=str(copy.cost_notional),
                     capped=copy.capped, detect_ms=e.detected_at_ms - e.leader_time_ms,
                     order_ms=(res.filled_at_ms or self._clock()) - e.detected_at_ms)
        return decision

    def _mark_capped(self, copy: Copy) -> None:
        if not copy.capped:
            copy.capped, copy.qty_at_cap, copy.leader_pos_at_cap = True, copy.qty_open, copy.leader_pos

    def _book_entry(self, copy: Copy, e: LeaderEvent | None, res: OrderResult, *, first: bool) -> None:
        assert res.avg_price is not None
        qty, px = res.filled_qty, res.avg_price
        total = copy.qty_open + qty
        copy.entry_price = px if copy.entry_price is None or copy.qty_open == 0 else \
            (copy.entry_price * copy.qty_open + px * qty) / total
        copy.qty_open = total
        copy.qty_initial += qty
        copy.cost_notional += qty * px
        copy.margin = copy.cost_notional / self._s.leverage
        copy.fees += res.fee
        copy.realized_pnl -= res.fee  # net of every fee, as for first_fill
        if first and e is not None:
            filled_at = res.filled_at_ms or self._clock()
            copy.latency_order_ms = filled_at - e.detected_at_ms
            copy.slippage_bps = copy.side * (px / copy.leader_entry_px - 1) * 10_000

    def _over_exposure(self, open_notional: Decimal, equity: Decimal, order_notional: Decimal) -> bool:
        return open_notional + order_notional > self._s.max_total_notional_x_equity * equity

    async def _open_notional(self, broker: Broker) -> Decimal:
        positions = await self._safe(broker.positions)  # unknown: 0 -- margin is still checked by the exchange
        return sum((p.qty * p.entry_price for p in positions or []), Decimal(0))

    async def _leverage(self, broker: Broker, symbol: str) -> bool:
        try:
            await self._run(broker.ensure_cross_leverage, symbol, self._s.leverage)
            return True
        except Exception as exc:  # noqa: BLE001
            self._n.warning("CROSS_LEVERAGE_FAILED", leader=self.leader, symbol=symbol, error=str(exc))
            return False

    async def _order(self, copy: Copy, broker: Broker, side: int, qty: Decimal, client_id: str, kind: OrderKind,
                     *, reduce_only: bool) -> OrderResult | None:
        """One market order, recorded in `orders`; a rejected order is retried once (spec §7)."""
        res: OrderResult | None = None
        for attempt, cid in enumerate((client_id, f"{client_id}:retry")):
            if not self._store.insert_order(copy_id=copy.id, client_id=cid, kind=kind, requested_qty=qty):
                prior = self._store.get_order(cid)
                if prior is not None and prior["status"] == str(OrderStatus.FILLED):
                    return await self._safe(broker.order_by_client_id, cid)  # replay: already done
            try:
                res = await self._run(broker.market_order, copy.symbol, side, qty, client_id=cid,
                                      reduce_only=reduce_only)
            except Exception as exc:  # noqa: BLE001 - outcome unknown: ask the exchange before deciding anything
                res = await self._safe(broker.order_by_client_id, cid) or \
                    OrderResult(False, cid, error=f"{type(exc).__name__}: {exc}")
            if res.success:
                self._store.finish_order(cid, OrderStatus.FILLED, filled_qty=res.filled_qty, avg_price=res.avg_price,
                                         fee=res.fee, filled_at_ms=res.filled_at_ms)
                return res
            self._store.finish_order(cid, OrderStatus.FAILED, error=res.error)
            if attempt == 0:
                self._n.warning("ORDER_REJECTED_RETRY", leader=self.leader, symbol=copy.symbol, error=res.error)
        return res

    # -- REDUCE / CLOSE -------------------------------------------------------------------------------------------

    async def _c20_reduce(self, e: LeaderEvent, copy: Copy | None) -> Decision:
        if copy is None or copy.closed_at is not None:
            return self._no_copy(e, copy)
        before = abs(e.pos_before) if e.pos_before is not None else Decimal(0)
        after = abs(e.pos_after) if e.pos_after is not None else Decimal(0)
        keep = after / before if before > 0 else Decimal(1) - (e.fraction or Decimal(0))
        copy.leader_pos = after
        copy.pending_qty *= keep
        inst = self.instruments.get(copy.symbol)
        raw = copy.qty_open * (1 - keep)
        qty = floor_to_step(raw, inst.qty_step) if inst else raw
        if copy.qty_open <= 0 or qty <= 0 or (inst is not None and qty < inst.min_qty):
            self._store.update_copy(copy)
            self._event(e, Decision.REDUCED, detail="below the minimum order: left to reconciliation")
            return Decision.REDUCED
        return await self._exit_qty(copy, e, min(qty, copy.qty_open), qty >= copy.qty_open, "REDUCE")

    async def _c20_close(self, e: LeaderEvent, copy: Copy | None) -> Decision:
        if copy is None or copy.closed_at is not None:
            return self._no_copy(e, copy)
        copy.leader_pos, copy.pending_qty = Decimal(0), Decimal(0)
        reason = "FLIP" if e.type == EventType.FLIP else "CLOSE"
        if copy.qty_open <= 0:  # nothing was ever filled (adds stayed below the minimum)
            copy.status = self._closed_status(copy, CopyStatus.CLOSED_LEADER)
            copy.closed_at, copy.close_reason = self._clock(), f"{reason} (nothing held)"
            self._store.update_copy(copy)
            self._event(e, Decision.CLOSED, detail="closed: nothing was held")
            return Decision.CLOSED
        return await self._exit_qty(copy, e, copy.qty_open, True, reason)

    async def _exit_qty(self, copy: Copy, e: LeaderEvent, qty: Decimal, closing: bool, reason: str) -> Decision:
        base = f"{self.leader}:{copy.coin}:{copy.episode_id}"
        client_id = f"{base}:{'close' if closing else 'reduce'}:{e.tid}"  # botup.md v4 §4 client ids
        book = self.book_of(copy)
        res = await self._order(copy, book.broker, -copy.side, qty, client_id,
                                OrderKind.CLOSE if closing else OrderKind.REDUCE, reduce_only=self._reduce_only(copy))
        if res is None or not res.success or res.avg_price is None:
            self._store.update_copy(copy)
            error = None if res is None else res.error
            self._event(e, Decision.FAILED, detail=error)
            self._n.critical("EXIT_FAILED", leader=self.leader, coin=copy.coin, symbol=copy.symbol,
                             episode=copy.episode_id, type=str(e.type), error=error, account=book.account)
            return Decision.FAILED
        self._book_reduce(copy, res, closing, reason)
        decision = Decision.CLOSED if copy.closed_at is not None else Decision.REDUCED
        self._event(e, decision, our_px=res.avg_price, detail=f"{reason} qty={res.filled_qty} px={res.avg_price}")
        account = await self._safe(book.broker.account)  # after the order: the exit is never delayed for a log line
        self._n.info("COPY_EXIT", leader=self.leader, tid=e.tid, coin=copy.coin, symbol=copy.symbol,
                     episode=copy.episode_id, side=-copy.side, reason=reason, qty=str(res.filled_qty),
                     px=str(res.avg_price), r=str(copy.ratio_r), cap=str(copy.cap_notional),
                     equity=str(account.equity) if account else None, closed=copy.closed_at is not None,
                     pnl=str(copy.realized_pnl), account=book.account)
        return decision

    def _book_reduce(self, copy: Copy, res: OrderResult, closing: bool, reason: str,
                     status: CopyStatus = CopyStatus.CLOSED_LEADER) -> None:
        assert res.avg_price is not None
        before = copy.qty_open
        self._book_exit(copy, res.filled_qty, res.avg_price, res.fee, closing, reason,
                        self._closed_status(copy, status))
        if before > 0:  # the cost basis shrinks with what is still held: room for adds grows back
            copy.cost_notional = copy.cost_notional * copy.qty_open / before
            copy.margin = copy.cost_notional / self._s.leverage
        self._store.update_copy(copy)

    # -- reconciler / kill --close-all --------------------------------------------------------------------------

    async def force_close(self, copy: Copy, *, reason: str, status: CopyStatus) -> bool:
        if not self.is_cap20(copy):
            return await super().force_close(copy, reason=reason, status=status)
        if copy.qty_open <= 0:
            copy.status = self._closed_status(copy, status)
            copy.closed_at, copy.close_reason = self._clock(), reason
            self._store.update_copy(copy)
            return True
        client_id = f"{copy.leader}:{copy.coin}:{copy.episode_id}:{reason.lower()}"
        book = self.book_of(copy)
        res = await self._order(copy, book.broker, -copy.side, copy.qty_open, client_id, OrderKind.CLOSE,
                                reduce_only=self._reduce_only(copy))
        if res is None or not res.success or res.avg_price is None:
            self._n.critical("FORCE_CLOSE_FAILED", leader=copy.leader, coin=copy.coin, symbol=copy.symbol,
                             reason=reason, error=None if res is None else res.error, account=book.account)
            return False
        self._book_reduce(copy, res, True, reason, status)
        return True

    async def correct(self, copy: Copy, leader_abs: Decimal) -> str | None:
        """Reconciliation (spec §5 under C-4 -- the caller checked the snapshot): move our quantity towards the
        target when the gap is worth more than max(minNotional, 0.5 % x E0). Adds go through the same limits as
        a leader add (cap room, free margin, exposure) and never past the cap. Never opens a trade."""
        assert copy.cap_notional is not None
        inst = self.instruments.get(copy.symbol)
        book = self.book_of(copy)
        quote = await self._safe(book.broker.quote, copy.symbol)
        if inst is None or quote is None:
            return None
        target = reconcile_target(copy, leader_abs * self._units(copy.coin), quote.mark)
        gap = copy.qty_open - target
        e0 = copy.cap_notional / (self._s.per_trade_margin_cap_pct / 100 * self._s.leverage)
        min_notional = inst.min_notional or self._s.min_order_usdt
        if abs(gap) * quote.mark <= max(min_notional, e0 * Decimal("0.005")):
            return None
        tag = f"{copy.leader}:{copy.coin}:{copy.episode_id}:reconcile:{self._clock()}"
        if gap > 0:
            qty = min(floor_to_step(gap, inst.qty_step), copy.qty_open)
            if qty <= 0 or qty < inst.min_qty:
                return None
            closing = qty >= copy.qty_open
            res = await self._order(copy, book.broker, -copy.side, qty, tag,
                                    OrderKind.CLOSE if closing else OrderKind.REDUCE,
                                    reduce_only=self._reduce_only(copy))
            if res is None or not res.success or res.avg_price is None:
                return None
            self._book_reduce(copy, res, closing, "RECONCILED")
            return f"reduced {res.filled_qty} towards {target:.8g}"
        if copy.capped or self._new_trade_block(book) is not None:
            return None
        account, open_notional = await asyncio.gather(self._safe(book.broker.account),
                                                      self._open_notional(book.broker))
        if account is None:
            return None
        px = quote.price_for(copy.side)
        plan = add_order_qty(pending=-gap, room_notional=copy.cap_notional - copy.cost_notional, price=px,
                             instrument=inst, available=account.available, leverage=self._s.leverage,
                             exposure_left=NO_LIMIT, min_notional=min_notional)
        if plan.qty <= 0 or self._over_exposure(open_notional, account.equity, plan.qty * px):
            return None
        res = await self._order(copy, book.broker, copy.side, plan.qty, tag, OrderKind.OPEN, reduce_only=False)
        if res is None or not res.success or res.avg_price is None:
            return None
        self._book_entry(copy, None, res, first=False)
        if plan.capped or copy.cost_notional >= copy.cap_notional - EPS:
            self._mark_capped(copy)
        self._store.update_copy(copy)
        return f"added {res.filled_qty} towards {target:.8g}"

    def _event(self, e: LeaderEvent, decision: Decision, *, rejection: str | None = None,
               detail: str | None = None, our_px: Decimal | None = None) -> None:
        self._store.insert_event(e, decision, rejection=rejection, detail=detail, leader=self.leader, our_px=our_px)


def book_account_liquidation(store: Store, account: str, copies: list[Copy],
                             closed: list[tuple[str, Decimal, Decimal]], clock: Callable[[], int],
                             notifier: Notifier) -> int:
    """A virtual / paper cross account was liquidated (everything closed at the mark): every cap20 copy living
    there, of every lane, is booked closed at that price. Returns how many copies were closed."""
    prices = {symbol: px for symbol, _, px in closed}
    n = 0
    for copy in copies:
        if copy.closed_at is not None or copy.strategy != STRATEGY:
            continue
        px = prices.get(copy.symbol)
        if px is not None and copy.entry_price is not None and copy.qty_open > 0:
            copy.realized_pnl += copy.side * (px - copy.entry_price) * copy.qty_open
            copy.exit_price_avg, copy.exit_qty_total = px, copy.exit_qty_total + copy.qty_open
        if copy.status != CopyStatus.OBSERVED_ONLY:
            copy.status = CopyStatus.CLOSED_LIQ
        copy.qty_open, copy.closed_at, copy.close_reason = Decimal(0), clock(), "LIQUIDATED"
        store.update_copy(copy)
        n += 1
    notifier.critical("ACCOUNT_LIQUIDATED", account=account, copies=n, positions={s: str(q) for s, q, _ in closed})
    return n


class Cap20Reconciler(Reconciler):
    """Reconciliation for a cap20 lane (botup.md v4 §5):
    - exchange account: positions are net over every lane trading there, so the check is per symbol (sum of
      the live copies vs the exchange) -- done once per account; a copy whose symbol vanished is resolved;
    - missed leader exits (tracker says the episode is over) -> our share is closed;
    - the target correction under C-4 (fresh snapshot, consistent with the tracker). Never opens a trade."""

    def __init__(self, *, store: Store, engine: Cap20Engine, settings: Settings, notifier: Notifier,
                 tracker: LeaderTracker, risk: RiskGuard, run_sync: RunSync, clock: Callable[[], int] = _now_ms,
                 account_copies: Callable[[], list[Copy]] | None = None, check_account: bool = True) -> None:
        super().__init__(store=store, broker=engine.real.broker, settings=settings, notifier=notifier, tracker=tracker,
                         risk=risk, engine=engine, run_sync=run_sync, clock=clock, check_orphans=check_account)
        self._c20 = engine
        self._account_copies = account_copies or (lambda: store.live_copies(leader=self.leader))
        self._stale_said_at = 0
        self._pre: tuple[Any, list[Any]] | None = None

    async def prefetch(self) -> None:
        self._pre = None
        if self._store.copies_with_status(CopyStatus.OPEN, leader=self.leader) or self._check_orphans:
            try:
                account, positions = await asyncio.gather(self._run(self._broker.account),
                                                          self._run(self._broker.positions))
                self._pre = (account, positions)
            except Exception:  # noqa: BLE001 - run_once fetches (and reports) itself
                pass

    async def run_once(self) -> None:
        mine = self._store.copies_with_status(CopyStatus.OPEN, leader=self.leader)
        if mine or self._check_orphans:
            await self._check_exchange(mine)
        await self._missed_exits()
        await self._targets()

    async def _check_exchange(self, mine: list[Copy]) -> None:
        pre, self._pre = self._pre, None
        try:
            if pre is not None:
                account, raw = pre
            else:
                account, raw = await asyncio.gather(self._run(self._broker.account), self._run(self._broker.positions))
            positions = {p.symbol: p for p in raw}
        except Exception as exc:  # noqa: BLE001 - try again next cycle
            self._n.warning("RECONCILE_BROKER_UNAVAILABLE", leader=self.leader, error=str(exc))
            return
        self._risk.on_equity(account.equity)
        if self._c20.real.guard is not None:
            self._c20.real.guard.on_equity(account.equity)
        for copy in mine:
            if copy.symbol not in positions and copy.qty_open > 0:
                await self._resolve_vanished(copy)
        if not self._check_orphans:
            return
        expected: dict[str, Decimal] = {}
        for c in self._account_copies():
            expected[c.symbol] = expected.get(c.symbol, Decimal(0)) + c.side * c.qty_open
        for sym, pos in positions.items():
            want = expected.get(sym)
            if want is None:
                self._n.critical("ORPHAN_POSITION", symbol=sym, side=pos.side, qty=str(pos.qty))
            elif pos.side * pos.qty != want:
                self._n.warning("POSITION_MISMATCH", symbol=sym, db_net=str(want), exchange=str(pos.side * pos.qty))

    async def _missed_exits(self) -> None:
        now = self._clock()
        alive = set()
        for copy in self._c20.open_trades():
            st = self._tracker.state(copy.coin)
            if st.position != 0 and st.episode_id == copy.episode_id:
                continue  # the leader is still in this trade
            if not self._c20.is_cap20(copy) and copy.status == CopyStatus.OBSERVED_ONLY:
                copy.qty_open, copy.closed_at, copy.close_reason = Decimal(0), now, "RECONCILED"
                self._store.update_copy(copy)  # a v3 observed copy holds nothing anywhere
                continue
            assert copy.id is not None
            alive.add(copy.id)
            since = self._episode_over_since.setdefault(copy.id, now)
            if now - since < self._s.missed_exit_seconds * 1000:
                continue
            ok = await self._c20.force_close(copy, reason="RECONCILED", status=CopyStatus.CLOSED_RECONCILED)
            if ok:
                self._n.critical("MISSED_EXIT_AUTO_CLOSED", leader=self.leader, coin=copy.coin, symbol=copy.symbol,
                                 episode=copy.episode_id, waited_s=(now - since) // 1000,
                                 account=self._c20.book_of(copy).account)
                self._episode_over_since.pop(copy.id, None)
        for cid in list(self._episode_over_since):
            if cid not in alive:
                self._episode_over_since.pop(cid, None)

    def _fresh_leader_positions(self) -> dict[str, Decimal] | None:
        snap = self._store.get_state(lane_key("leader_snapshot", self.leader))
        if not snap or self._clock() - int(snap["taken_at_ms"]) > SNAPSHOT_MAX_AGE_MS:
            return None
        return {c: Decimal(p) for c, p in snap.get("positions", {}).items()}

    async def _targets(self) -> None:
        trades = [c for c in self._c20.open_trades() if self._c20.is_cap20(c)]
        if not trades:
            return
        leader = self._fresh_leader_positions()
        if leader is None:  # C-4: no fresh snapshot, nothing moves -- and the owner is told (every 10 min at most)
            if self._clock() - self._stale_said_at >= 600_000:
                self._stale_said_at = self._clock()
                self._n.warning("RECONCILE_STALE_SNAPSHOT", leader=self.leader, trades=len(trades),
                                max_age_s=SNAPSHOT_MAX_AGE_MS // 1000)
            return
        for copy in trades:
            st = self._tracker.state(copy.coin)
            szi = leader.get(copy.coin, Decimal(0))
            if (not self._tracker.is_synced(copy.coin) or st.episode_id != copy.episode_id or st.position == 0
                    or szi != st.position):
                continue  # the snapshot and the fills disagree (or a fill is in flight): never guess
            done = await self._c20.correct(copy, abs(szi))
            if done:
                self._n.warning("RECONCILE_CORRECTED", leader=self.leader, coin=copy.coin, episode=copy.episode_id,
                                action=done, account=self._c20.book_of(copy).account)


def describe(settings: Settings) -> dict[str, Any]:
    """The active strategy parameters (CLI `strategy`, dashboard settings card)."""
    s = settings
    return {"strategy": s.strategy, "margin_mode": s.margin_mode, "leverage": s.leverage, "k": str(s.k),
            "per_trade_margin_cap_pct": str(s.per_trade_margin_cap_pct),
            "cap_x_equity": str(s.per_trade_margin_cap_pct / 100 * s.leverage),
            "max_total_notional_x_equity": str(s.max_total_notional_x_equity),
            "feed_outage_block_seconds": s.feed_outage_block_seconds, "fill_poll_seconds": s.fill_poll_seconds,
            "leader_equity_refresh_seconds": s.leader_equity_refresh_seconds, "follow_adds": s.follow_adds,
            "shared_equity": s.shared_equity,
            "account_kill_dd_pct": str(s.account_kill_dd_pct) if s.account_kill_dd_pct is not None else None,
            "virtual_start_equity": str(s.virtual_start_equity), "virtual_fee_rate": str(s.virtual_fee_rate)}
