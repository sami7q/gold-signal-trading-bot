"""Turns leader events into orders (botup.md §7). Every order is idempotent through its client_id (UNIQUE in
`orders`); a copy is only ever written as OPEN after the exchange confirmed a fill -- never a phantom.

    OPEN    -> rules -> size -> copy SUBMITTING -> leverage -> market open -> OPEN (+TP from the real fill price)
    ADD     -> logged as IGNORED_ADD, never traded (owner decision)
    REDUCE  -> same fraction as the leader, reduce-only
    CLOSE   -> reduce-only close of everything, TP cancelled, CLOSED_LEADER
    FLIP    -> like CLOSE; the tracker then emits a separate OPEN that goes through the full rules
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, TypeVar

from loco_copy.brokers.base import Broker
from loco_copy.config import Settings
from loco_copy.constants import CopyStatus, Decision, EventType, OrderKind, OrderStatus, Rejection
from loco_copy.models import Copy, InstrumentInfo, LeaderEvent, OrderResult
from loco_copy.notify import Notifier
from loco_copy.risk import RiskGuard
from loco_copy.rules import BrokerContext, PreContext, broker_checks, pre_broker_checks
from loco_copy.sizing import Sizing, reduce_qty, size_order, take_profit_price
from loco_copy.store import Store
from loco_copy.symbols import SymbolMapper
from loco_copy.tracker import LeaderTracker, is_main_perp

T = TypeVar("T")
RunSync = Callable[..., Awaitable[Any]]


def _now_ms() -> int:
    return int(time.time() * 1000)


async def run_inline[R](fn: Callable[..., R], *args: Any, **kwargs: Any) -> R:
    """Replay/tests: call the (in-memory) broker directly instead of in a worker thread."""
    return fn(*args, **kwargs)


def open_client_id(coin: str, episode: int, leader: str = "loco") -> str:
    """`<label>:<coin>:<episode>:open` -- lane `loco` keeps the v2 ids, so recovery of old orders still works."""
    return f"{leader}:{coin}:{episode}:open"


#: symbol -> label of another lane that really holds it on the same exchange account (shared-account layout).
HeldElsewhere = Callable[[str], str | None]


def _nobody_else(_symbol: str) -> str | None:
    return None


class CopyEngine:
    def __init__(self, *, store: Store, broker: Broker, settings: Settings, risk: RiskGuard, notifier: Notifier,
                 mapper: SymbolMapper, tracker: LeaderTracker, instruments: dict[str, InstrumentInfo],
                 clock: Callable[[], int] = _now_ms, run_sync: RunSync = asyncio.to_thread,
                 held_elsewhere: HeldElsewhere = _nobody_else) -> None:
        self._store = store
        self._broker = broker
        self._s = settings
        self._risk = risk
        self._n = notifier
        self._mapper = mapper
        self._tracker = tracker
        self.instruments = instruments
        self._clock = clock
        self._run = run_sync
        self._held_elsewhere = held_elsewhere
        self.leader = settings.lane

    def use_settings(self, settings: Settings) -> None:
        """Runtime mode switch from the dashboard (watch / observe / execute)."""
        self._s = settings

    # -- entry point -----------------------------------------------------------------------------------------

    async def handle(self, event: LeaderEvent) -> Decision:
        if event.type == EventType.ADD:
            self._event(event, Decision.IGNORED_ADD)
            self._n.info("IGNORED_ADD", leader=self.leader, coin=event.coin, episode=event.episode_id,
                         px=str(event.leader_px))
            return Decision.IGNORED_ADD
        if event.type == EventType.OPEN:
            return await self._open(event)
        return await self._exit(event)

    # -- OPEN -------------------------------------------------------------------------------------------------

    async def _open(self, e: LeaderEvent) -> Decision:
        sym = self._mapper.map(e.coin)
        inst = self.instruments.get(sym.symbol) if sym else None
        pre = PreContext(
            copy_enabled=self._s.copy_enabled, risk_block=self._risk.open_block(self._risk_equity()),
            main_perp=is_main_perp(e.coin), synced=self._tracker.is_synced(e.coin), side=e.side,
            allow_long=self._s.allow_long, allow_short=self._s.allow_short, coin=e.coin,
            allowlist=self._s.allowlist, blocklist=self._s.blocklist, symbol=sym, instrument=inst,
            now_ms=self._clock(), leader_time_ms=e.leader_time_ms, max_signal_age_ms=self._s.max_signal_age_ms)
        rejection = pre_broker_checks(pre)
        if rejection is not None or sym is None or inst is None:
            return self._reject(e, rejection or Rejection.SYMBOL_NOT_LISTED)

        health = await self._safe(self._broker.health)
        mark = await self._safe(self._broker.mark_price, sym.symbol)
        account = await self._safe(self._broker.account)
        if account is not None:
            self._risk.on_equity(account.equity)
            if self._risk.daily_loss_hit(account.equity):
                return self._reject(e, Rejection.DAILY_LOSS_LIMIT)
        leader_px = e.leader_px * sym.price_multiplier
        sizing: Sizing | None = None
        if mark is not None and account is not None and account.equity > 0:
            sizing = size_order(equity=account.equity, mark=mark, side=e.side, instrument=inst,
                                risk_per_trade=self._s.risk_per_trade, leverage=self._s.leverage,
                                tp_roi=self._s.take_profit_roi, max_notional=self._s.max_notional_per_coin)
        live = self._store.live_copies(leader=self.leader)
        observed_open = [c for c in self._store.copies_with_status(CopyStatus.OBSERVED_ONLY, leader=self.leader)
                         if c.closed_at is None]
        bctx = BrokerContext(
            side=e.side, leader_px=leader_px, mark=mark, max_entry_drift=self._s.max_entry_drift,
            tp_roi=self._s.take_profit_roi, leverage=self._s.leverage,
            copy_exists_for_episode=self._store.get_copy(e.coin, e.episode_id, leader=self.leader) is not None,
            open_copy_on_coin=any(c.coin == e.coin for c in live + observed_open),
            broker_healthy=bool(health and health.healthy), open_copies=len(live + observed_open),
            max_open_positions=self._s.max_open_positions, account=account,
            risk_per_trade=self._s.risk_per_trade, sizing=sizing, max_notional=self._s.max_notional_per_coin,
            instrument=inst)
        rejection = broker_checks(bctx)
        if rejection is not None or sizing is None or mark is None:
            return self._reject(e, rejection or Rejection.BROKER_UNAVAILABLE)
        holder = self._held_elsewhere(sym.symbol)
        if holder is not None:
            # One exchange account in one-way mode holds one position per symbol: ours would merge with theirs.
            return self._reject(e, Rejection.COIN_HELD_BY_OTHER_LEADER, holder=holder)

        copy = Copy(coin=e.coin, symbol=sym.symbol, episode_id=e.episode_id, side=e.side,
                    status=CopyStatus.SUBMITTING, qty_initial=sizing.qty, qty_open=Decimal(0),
                    leader_entry_px=leader_px, tp_price=sizing.tp_price, margin=sizing.margin,
                    latency_detect_ms=e.detected_at_ms - e.leader_time_ms, leader=self.leader)

        gated = not self._s.copy_observe_only and not self._s.allow_execute
        if self._s.copy_observe_only or gated:
            copy.status, copy.qty_open, copy.entry_price = CopyStatus.OBSERVED_ONLY, sizing.qty, mark
            copy.opened_at = self._clock()
            copy.slippage_bps = e.side * (mark / leader_px - 1) * 10_000
            self._store.insert_copy(copy)
            # The execution gate (botup.md v3 §5.3): EXECUTE without ALLOW_EXECUTE__<LABEL> is recorded, never sent.
            self._event(e, Decision.OBSERVED_ONLY, rejection=str(Rejection.EXECUTION_NOT_ALLOWED) if gated else None,
                        detail=("execution gate: " if gated else "") + f"qty={sizing.qty} tp={sizing.tp_price}")
            self._n.info("OBSERVED_ONLY", leader=self.leader, coin=e.coin, episode=e.episode_id, side=e.side,
                         qty=str(sizing.qty), mark=str(mark), leader_px=str(leader_px), tp=str(sizing.tp_price),
                         gated=gated)
            return Decision.OBSERVED_ONLY

        client_id = open_client_id(e.coin, e.episode_id, self.leader)
        copy = self._store.insert_copy(copy)
        if not self._store.insert_order(copy_id=copy.id, client_id=client_id, kind=OrderKind.OPEN,
                                        requested_qty=sizing.qty):
            return self._reject(e, Rejection.DUPLICATE_EPISODE)

        result = await self._submit_open(copy, sizing, client_id)
        return await self._apply_open(e, copy, sizing, client_id, result, inst)

    async def _submit_open(self, copy: Copy, sizing: Sizing, client_id: str) -> OrderResult:
        try:
            await self._run(self._broker.ensure_isolated_leverage, copy.symbol, self._s.leverage)
            res: OrderResult = await self._run(
                self._broker.market_open, copy.symbol, copy.side, sizing.qty, client_id=client_id,
                max_slippage=self._s.max_order_slippage, take_profit=sizing.tp_price)
            return res
        except Exception as exc:  # noqa: BLE001 - outcome unknown: ask the exchange before deciding anything
            recovered = await self._safe(self._broker.order_by_client_id, client_id)
            if recovered is not None:
                return recovered
            return OrderResult(False, client_id, error=f"{type(exc).__name__}: {exc}")

    async def _apply_open(self, e: LeaderEvent, copy: Copy, sizing: Sizing, client_id: str, res: OrderResult,
                          inst: InstrumentInfo) -> Decision:
        if not res.success or res.avg_price is None or res.filled_qty <= 0:
            copy.status, copy.close_reason, copy.closed_at = CopyStatus.FAILED, res.error, self._clock()
            self._store.update_copy(copy)
            self._store.finish_order(client_id, OrderStatus.FAILED, error=res.error)
            self._event(e, Decision.FAILED, detail=res.error)
            self._n.warning("OPEN_FAILED", leader=self.leader, coin=e.coin, episode=e.episode_id, error=res.error)
            return Decision.FAILED

        filled_at = res.filled_at_ms or self._clock()
        copy.status = CopyStatus.OPEN
        copy.entry_price, copy.qty_initial, copy.qty_open = res.avg_price, res.filled_qty, res.filled_qty
        copy.margin = res.filled_qty * res.avg_price / self._s.leverage
        copy.fees += res.fee
        copy.realized_pnl -= res.fee  # realized_pnl is net of every fee paid, the opening one included
        copy.opened_at = filled_at
        copy.slippage_bps = e.side * (res.avg_price / copy.leader_entry_px - 1) * 10_000
        copy.latency_order_ms = filled_at - e.detected_at_ms
        tp = take_profit_price(res.avg_price, e.side, self._s.take_profit_roi, self._s.leverage, inst.tick_size)
        copy.tp_price = sizing.tp_price
        if abs(tp - sizing.tp_price) >= inst.tick_size:
            try:
                await self._run(self._broker.set_take_profit, copy.symbol, tp)
                copy.tp_price = tp
            except Exception as exc:  # noqa: BLE001
                self._n.warning("TP_UPDATE_FAILED", coin=e.coin, symbol=copy.symbol, error=str(exc))
        self._store.update_copy(copy)
        self._store.finish_order(client_id, OrderStatus.FILLED, filled_qty=res.filled_qty, avg_price=res.avg_price,
                                 fee=res.fee, filled_at_ms=filled_at)
        self._event(e, Decision.COPIED, detail=f"qty={res.filled_qty} px={res.avg_price}")
        self._n.info("COPY_OPENED", leader=self.leader, coin=e.coin, symbol=copy.symbol, episode=e.episode_id,
                     side=e.side, qty=str(res.filled_qty), entry=str(res.avg_price), tp=str(copy.tp_price),
                     slippage_bps=str(round(copy.slippage_bps, 2)), latency_ms=copy.latency_order_ms)
        return Decision.COPIED

    def _event(self, e: LeaderEvent, decision: Decision, *, rejection: str | None = None,
               detail: str | None = None) -> None:
        self._store.insert_event(e, decision, rejection=rejection, detail=detail, leader=self.leader)

    def _reject(self, e: LeaderEvent, why: Rejection, *, holder: str | None = None) -> Decision:
        self._event(e, Decision.REJECTED, rejection=str(why), detail=f"held by {holder}" if holder else None)
        level = "WARNING" if why in (Rejection.SYMBOL_NOT_LISTED, Rejection.BROKER_UNAVAILABLE) else "INFO"
        self._n.emit(level, "OPEN_REJECTED", leader=self.leader, coin=e.coin, episode=e.episode_id, reason=str(why),
                     **({"held_by": holder} if holder else {}))
        return Decision.REJECTED

    # -- REDUCE / CLOSE / FLIP ---------------------------------------------------------------------------------

    async def _exit(self, e: LeaderEvent) -> Decision:
        copy = self._store.get_copy(e.coin, e.episode_id, leader=self.leader)
        if e.type == EventType.REDUCE and not self._s.follow_reduces:
            self._event(e, Decision.IGNORED_REDUCE)
            return Decision.IGNORED_REDUCE
        if copy is None or copy.closed_at is not None or copy.status not in (CopyStatus.OPEN,
                                                                             CopyStatus.OBSERVED_ONLY):
            detail = None if copy is None else f"copy is {copy.status}"
            self._event(e, Decision.NO_MATCHING_COPY, detail=detail)
            self._n.info("NO_MATCHING_COPY", leader=self.leader, coin=e.coin, episode=e.episode_id, type=str(e.type),
                         detail=detail)
            return Decision.NO_MATCHING_COPY
        inst = self.instruments.get(copy.symbol)
        if inst is None:
            inst = InstrumentInfo(copy.symbol, Decimal("1e-12"), Decimal(0), Decimal("1e-12"), self._s.leverage)
        qty = copy.qty_open
        if e.type == EventType.REDUCE and e.fraction is not None:
            qty = reduce_qty(copy.qty_open, e.fraction, inst)
        if qty <= 0:
            self._event(e, Decision.FOLLOWED, detail="reduce below one qty step: nothing to send")
            return Decision.FOLLOWED
        closing = qty >= copy.qty_open
        reason = {EventType.FLIP: "FLIP", EventType.CLOSE: "CLOSE"}.get(e.type, "REDUCE")
        sym = self._mapper.map(e.coin)
        exit_px = e.leader_px * (sym.price_multiplier if sym else 1)

        if copy.status == CopyStatus.OBSERVED_ONLY:
            self._book_exit(copy, qty, exit_px, Decimal(0), closing, reason, CopyStatus.OBSERVED_ONLY)
            self._event(e, Decision.OBSERVED_ONLY, detail=f"{reason} qty={qty}")
            return Decision.OBSERVED_ONLY

        kind = OrderKind.CLOSE if closing else OrderKind.REDUCE
        base = f"{self.leader}:{e.coin}:{e.episode_id}"
        client_id = f"{base}:close" if closing else f"{base}:reduce:{e.tid}"
        res = await self._send_exit(copy, qty, client_id, kind)
        if res is None:  # already applied earlier (idempotent replay of the same event)
            self._event(e, Decision.FOLLOWED, detail="already applied")
            return Decision.FOLLOWED
        if not res.success or res.avg_price is None:
            self._event(e, Decision.FAILED, detail=res.error)
            self._n.critical("EXIT_FAILED", leader=self.leader, coin=e.coin, symbol=copy.symbol, episode=e.episode_id,
                             type=str(e.type), error=res.error)
            return Decision.FAILED
        self._book_exit(copy, res.filled_qty, res.avg_price, res.fee, closing, reason, CopyStatus.CLOSED_LEADER)
        if copy.closed_at is not None:
            await self._safe(self._broker.cancel_take_profit, copy.symbol)
        self._event(e, Decision.FOLLOWED, detail=f"{reason} qty={res.filled_qty} px={res.avg_price}")
        self._n.info("COPY_EXIT", leader=self.leader, coin=e.coin, symbol=copy.symbol, episode=e.episode_id,
                     reason=reason,
                     qty=str(res.filled_qty), px=str(res.avg_price), closed=copy.closed_at is not None,
                     pnl=str(copy.realized_pnl))
        return Decision.FOLLOWED

    async def _send_exit(self, copy: Copy, qty: Decimal, client_id: str, kind: OrderKind) -> OrderResult | None:
        if not self._store.insert_order(copy_id=copy.id, client_id=client_id, kind=kind, requested_qty=qty):
            prior = self._store.get_order(client_id)
            if prior is not None and prior["status"] == str(OrderStatus.FILLED):
                return None
        try:
            res: OrderResult = await self._run(self._broker.market_reduce, copy.symbol, copy.side, qty,
                                               client_id=client_id)
        except Exception as exc:  # noqa: BLE001
            recovered = await self._safe(self._broker.order_by_client_id, client_id)
            res = recovered or OrderResult(False, client_id, error=f"{type(exc).__name__}: {exc}")
        if res.success:
            self._store.finish_order(client_id, OrderStatus.FILLED, filled_qty=res.filled_qty,
                                     avg_price=res.avg_price, fee=res.fee, filled_at_ms=res.filled_at_ms)
        else:
            self._store.finish_order(client_id, OrderStatus.FAILED, error=res.error)
        return res

    def _book_exit(self, copy: Copy, qty: Decimal, px: Decimal, fee: Decimal, closing: bool, reason: str,
                   closed_status: CopyStatus) -> None:
        prev = copy.exit_qty_total
        copy.exit_price_avg = px if prev == 0 or copy.exit_price_avg is None else \
            (copy.exit_price_avg * prev + px * qty) / (prev + qty)
        copy.exit_qty_total = prev + qty
        if copy.entry_price is not None:
            copy.realized_pnl += copy.side * (px - copy.entry_price) * qty
        copy.realized_pnl -= fee
        copy.fees += fee
        copy.qty_open = max(copy.qty_open - qty, Decimal(0))
        if closing or copy.qty_open == 0:
            copy.qty_open = Decimal(0)
            copy.status, copy.closed_at, copy.close_reason = closed_status, self._clock(), reason
        self._store.update_copy(copy)

    async def force_close(self, copy: Copy, *, reason: str, status: CopyStatus) -> bool:
        """Reduce-only close of everything we hold for this copy (used by the reconciler / kill --close-all)."""
        client_id = f"{copy.leader}:{copy.coin}:{copy.episode_id}:{reason.lower()}"
        res = await self._send_exit(copy, copy.qty_open, client_id, OrderKind.CLOSE)
        if res is None:
            return True
        if not res.success or res.avg_price is None:
            self._n.critical("FORCE_CLOSE_FAILED", leader=copy.leader, coin=copy.coin, symbol=copy.symbol,
                             reason=reason, error=res.error)
            return False
        self._book_exit(copy, res.filled_qty, res.avg_price, res.fee, True, reason, status)
        await self._safe(self._broker.cancel_take_profit, copy.symbol)
        return True

    def book_exchange_close(self, copy: Copy, *, qty: Decimal, px: Decimal, pnl: Decimal | None,
                            status: CopyStatus, reason: str) -> None:
        """An exit the exchange did on its own (TP / liquidation / manual), found by the reconciler. The
        exchange's own realized PnL (fees included) wins over our price-based estimate when it is known."""
        before = copy.realized_pnl
        self._book_exit(copy, qty, px, Decimal(0), True, reason, status)
        if pnl is not None:
            copy.realized_pnl = before + pnl
            self._store.update_copy(copy)

    # -- crash recovery ----------------------------------------------------------------------------------------

    async def recover(self) -> None:
        """At start-up: settle anything that was in flight when the process stopped."""
        for copy in self._store.copies_with_status(CopyStatus.SUBMITTING, leader=self.leader):
            client_id = open_client_id(copy.coin, copy.episode_id, self.leader)
            res = await self._safe(self._broker.order_by_client_id, client_id)
            inst = self.instruments.get(copy.symbol)
            if res is not None and res.success and res.avg_price is not None and inst is not None:
                fake = LeaderEvent(EventType.OPEN, copy.coin, copy.episode_id, copy.side, copy.leader_entry_px,
                                   self._clock(), self._clock(), 0)
                sizing = Sizing(copy.margin or Decimal(0), Decimal(0), copy.qty_initial, copy.tp_price or Decimal(0),
                                False)
                await self._apply_open(fake, copy, sizing, client_id, res, inst)
                pos = await self._safe(self._broker.position, copy.symbol)
                if pos is not None and pos.take_profit is None and copy.tp_price is not None:
                    await self._safe(self._broker.set_take_profit, copy.symbol, copy.tp_price)
                self._n.warning("RECOVERED_OPEN", coin=copy.coin, symbol=copy.symbol, episode=copy.episode_id)
            else:
                copy.status, copy.closed_at = CopyStatus.FAILED, self._clock()
                copy.close_reason = "not found after restart"
                self._store.update_copy(copy)
                self._store.finish_order(client_id, OrderStatus.FAILED, error="not found after restart")
                self._n.warning("SUBMITTING_NOT_FOUND", coin=copy.coin, episode=copy.episode_id)
        for row in self._store.orders_with_status(OrderStatus.SENT):
            if not row["client_id"].startswith(f"{self.leader}:"):
                continue  # another lane's order: that lane's engine (and broker) settles it
            res = await self._safe(self._broker.order_by_client_id, row["client_id"])
            if res is not None and res.success:
                self._store.finish_order(row["client_id"], OrderStatus.FILLED, filled_qty=res.filled_qty,
                                         avg_price=res.avg_price, fee=res.fee, filled_at_ms=res.filled_at_ms)
                self._n.warning("RECOVERED_EXIT_ORDER", client_id=row["client_id"])
            else:
                self._store.finish_order(row["client_id"], OrderStatus.UNKNOWN, error="not found after restart")

    # -- helpers ---------------------------------------------------------------------------------------------

    def _risk_equity(self) -> Decimal | None:
        return self._risk.last_equity

    async def _safe(self, fn: Callable[..., T], *args: Any) -> T | None:
        try:
            result: T = await self._run(fn, *args)
            return result
        except Exception as exc:  # noqa: BLE001 - every broker read is optional; callers handle None
            self._n.warning("BROKER_CALL_FAILED", call=getattr(fn, "__name__", "?"), error=str(exc))
            return None
