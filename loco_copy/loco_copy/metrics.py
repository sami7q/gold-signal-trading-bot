"""Go / no-go numbers (botup.md §11) from what is stored: latency, slippage, capture, missed exits, liquidations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from loco_copy.constants import CopyStatus, Decision, EventType, Rejection
from loco_copy.store import Store


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


@dataclass(frozen=True, slots=True)
class Metrics:
    leader_opens: int
    copied_opens: int
    not_listed: int
    capture_rate: float | None
    detect_p50_ms: float | None
    detect_p95_ms: float | None
    order_p50_ms: float | None
    slippage_median_bps: float | None
    missed_exits: int
    liquidations: int
    take_profits: int
    leader_exits: int
    realized_pnl: Decimal
    open_copies: int
    rejections: dict[str, int]


def compute(store: Store, since_ms: int) -> Metrics:
    events = store.events_since(since_ms)
    opens = [e for e in events if e["type"] == EventType.OPEN]
    copied = [e for e in opens if e["decision"] in (Decision.COPIED, Decision.OBSERVED_ONLY)]
    not_listed = sum(e["rejection"] == Rejection.SYMBOL_NOT_LISTED for e in opens)
    eligible = len(opens) - not_listed
    rejections: dict[str, int] = {}
    for e in opens:
        if e["rejection"]:
            rejections[e["rejection"]] = rejections.get(e["rejection"], 0) + 1
    detect = [float(e["detected_at_ms"] - e["leader_time_ms"]) for e in opens]
    copies = store.copies(since_ms=since_ms)
    order_lat = [float(c.latency_order_ms) for c in copies if c.latency_order_ms is not None]
    slip = [float(c.slippage_bps) for c in copies if c.slippage_bps is not None]
    return Metrics(
        leader_opens=len(opens), copied_opens=len(copied), not_listed=not_listed,
        capture_rate=(len(copied) / eligible) if eligible else None,
        detect_p50_ms=percentile(detect, 0.5), detect_p95_ms=percentile(detect, 0.95),
        order_p50_ms=percentile(order_lat, 0.5), slippage_median_bps=percentile(slip, 0.5),
        missed_exits=sum(c.status == CopyStatus.CLOSED_RECONCILED for c in copies),
        liquidations=sum(c.status == CopyStatus.CLOSED_LIQ for c in copies),
        take_profits=sum(c.status == CopyStatus.CLOSED_TP for c in copies),
        leader_exits=sum(c.status == CopyStatus.CLOSED_LEADER for c in copies),
        # Observe-only copies carry a virtual PnL; mixing it in would misstate what the account made.
        realized_pnl=sum((c.realized_pnl for c in copies if c.status != CopyStatus.OBSERVED_ONLY), Decimal(0)),
        open_copies=sum(c.status in (CopyStatus.OPEN, CopyStatus.SUBMITTING) for c in copies),
        rejections=rejections)
