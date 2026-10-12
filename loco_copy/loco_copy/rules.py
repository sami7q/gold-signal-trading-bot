"""Ordered safety checks for an OPEN (botup.md §6). First failure wins.

Split in two so nothing touches the exchange for an event that is going to be rejected anyway:
`pre_broker_checks` (1-7) needs no broker data; `broker_checks` (8-14) needs the mark price, account and health.
Exits (REDUCE / CLOSE / FLIP) never come here -- exits are never blocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from loco_copy.constants import Rejection
from loco_copy.models import Account, InstrumentInfo
from loco_copy.sizing import Sizing
from loco_copy.symbols import SymbolMap


@dataclass(frozen=True, slots=True)
class PreContext:
    copy_enabled: bool
    risk_block: Rejection | None  # KILL_SWITCH / DAILY_LOSS_LIMIT / LEADER_BREAKER / LATENCY_BREAKER
    main_perp: bool
    synced: bool
    side: int
    allow_long: bool
    allow_short: bool
    coin: str
    allowlist: frozenset[str]
    blocklist: frozenset[str]
    symbol: SymbolMap | None
    instrument: InstrumentInfo | None
    now_ms: int
    leader_time_ms: int
    max_signal_age_ms: int | None  # None = off (cap20_cross)


@dataclass(frozen=True, slots=True)
class BrokerContext:
    side: int
    leader_px: Decimal  # already converted into the exchange's price units
    mark: Decimal | None
    max_entry_drift: Decimal | None
    tp_roi: Decimal | None
    leverage: int
    copy_exists_for_episode: bool
    open_copy_on_coin: bool
    broker_healthy: bool
    open_copies: int
    max_open_positions: int | None
    account: Account | None
    risk_per_trade: Decimal
    sizing: Sizing | None
    max_notional: Decimal | None
    instrument: InstrumentInfo


def pre_broker_checks(c: PreContext) -> Rejection | None:
    if not c.copy_enabled:
        return Rejection.DISABLED  # 1
    if c.risk_block is not None:
        return c.risk_block  # 2
    if not c.main_perp:
        return Rejection.UNSUPPORTED_MARKET  # 3
    if not c.synced:
        return Rejection.PRE_EXISTING_POSITION
    if (c.side > 0 and not c.allow_long) or (c.side < 0 and not c.allow_short):
        return Rejection.SIDE_DISABLED  # 4
    if c.coin in c.blocklist or (c.allowlist and c.coin not in c.allowlist):
        return Rejection.COIN_BLOCKED  # 5
    if c.symbol is None or c.instrument is None:
        return Rejection.SYMBOL_NOT_LISTED  # 6
    if c.max_signal_age_ms is not None and c.now_ms - c.leader_time_ms > c.max_signal_age_ms:
        return Rejection.SIGNAL_TOO_OLD  # 7
    return None


def broker_checks(c: BrokerContext) -> Rejection | None:
    if c.mark is None:
        return Rejection.BROKER_UNAVAILABLE  # can't price the entry at all
    if c.max_entry_drift is not None and abs(c.mark / c.leader_px - 1) > c.max_entry_drift:
        return Rejection.PRICE_DRIFTED  # 8
    if c.tp_roi is not None:
        tp_from_leader = c.leader_px * (1 + c.side * c.tp_roi / c.leverage)
        if (c.mark >= tp_from_leader) if c.side > 0 else (c.mark <= tp_from_leader):
            return Rejection.PRICE_DRIFTED  # already past where our take profit would be
    if c.copy_exists_for_episode:
        return Rejection.DUPLICATE_EPISODE  # 9
    if c.open_copy_on_coin:
        return Rejection.COIN_ALREADY_OPEN
    if not c.broker_healthy or c.account is None:
        return Rejection.BROKER_UNAVAILABLE  # 10
    if c.max_open_positions is not None and c.open_copies >= c.max_open_positions:
        return Rejection.MAX_OPEN_POSITIONS  # 11
    if c.account.available < c.risk_per_trade * c.account.equity or c.account.equity <= 0:
        return Rejection.INSUFFICIENT_MARGIN  # 12
    if c.sizing is None:
        return Rejection.QTY_TOO_SMALL
    if c.max_notional is not None and c.sizing.notional > c.max_notional:
        return Rejection.NOTIONAL_CAP  # 13 (sizing already caps; this is a last-resort guard)
    if c.sizing.qty < c.instrument.min_qty or c.sizing.qty <= 0 or c.sizing.notional < c.instrument.min_notional:
        return Rejection.QTY_TOO_SMALL  # 14
    return None
