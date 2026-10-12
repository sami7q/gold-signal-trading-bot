"""The v5 observe phase (botup.md §V5.5) in numbers, from what is stored: per leader fill the latency
(seen - leader) and the would-be price at Bybit's bid / ask (slippage vs the leader's price), the virtual
"trio" ledger per lane, and the daily line. Used by the bot (daily Telegram line) and the dashboard."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loco_copy.constants import CopyStatus, EventType
from loco_copy.metrics import percentile
from loco_copy.store import Store

DAY_MS = 86_400_000
GROWING = (EventType.OPEN, EventType.ADD)
#: A gap this large means the two prices are in different units (a 1000x symbol): not a slippage.
MAX_SANE_SLIP_BPS = 5000


def event_slippage_bps(row: Any) -> float | None:
    """Cost of our (would-be) price vs the leader's, in bps of the leader's price; positive = worse for us."""
    our, leader = row["our_px"], row["leader_px"]
    if not our or not leader or Decimal(leader) <= 0:
        return None
    order_side = row["side"] if row["type"] in GROWING else -row["side"]
    bps = float(order_side * (Decimal(our) / Decimal(leader) - 1) * 10_000)
    return bps if abs(bps) < MAX_SANE_SLIP_BPS else None


def lane_ledger(store: Store, leader: str, since_ms: int = 0) -> dict[str, Any]:
    """One lane's virtual (observed cap20) trades."""
    copies = [c for c in store.copies(since_ms=since_ms, leader=leader)
              if c.strategy == "cap20_cross" and c.status == CopyStatus.OBSERVED_ONLY]
    closed = [c for c in copies if c.closed_at is not None]
    wins = sum(c.realized_pnl > 0 for c in closed)
    return {"leader": leader, "trades": len(copies), "open": len(copies) - len(closed), "closed": len(closed),
            "win_rate": wins / len(closed) if closed else None,
            "realized_pnl": str(sum((c.realized_pnl for c in copies), Decimal(0))),
            "fees": str(sum((c.fees for c in copies), Decimal(0))),
            "capped": sum(c.capped for c in copies), "adds": sum(c.adds_done for c in copies),
            "liquidated": sum(c.close_reason == "LIQUIDATED" for c in closed)}


def observe_stats(store: Store, leaders: list[str], since_ms: int) -> dict[str, Any]:
    """Latency and slippage of every leader event of these lanes since `since_ms`, plus per-lane counts."""
    lat: list[float] = []
    slip: list[float] = []
    lanes: dict[str, dict[str, int]] = {x: {"events": 0, "opens": 0, "adds": 0, "exits": 0, "rejected": 0}
                                        for x in leaders}
    for r in store.events_since(since_ms):
        lane = lanes.get(r["leader"])
        if lane is None:
            continue
        lane["events"] += 1
        lane["opens" if r["type"] == EventType.OPEN else "adds" if r["type"] == EventType.ADD else "exits"] += 1
        lane["rejected"] += r["decision"] == "REJECTED"
        lat.append(float(r["detected_at_ms"] - r["leader_time_ms"]))
        s = event_slippage_bps(r)
        if s is not None:
            slip.append(s)
    return {"since_ms": since_ms, "events": len(lat), "latency_p50_ms": percentile(lat, 0.5),
            "latency_p95_ms": percentile(lat, 0.95), "slippage_median_bps": percentile(slip, 0.5),
            "slippage_samples": len(slip), "lanes": lanes}


def daily_line(store: Store, leaders: list[str], virtual: dict[str, Any] | None, now_ms: int) -> str:
    """The daily observe line (Telegram + dashboard log)."""
    st = observe_stats(store, leaders, now_ms - DAY_MS)
    day = datetime.fromtimestamp(now_ms / 1000, UTC).strftime("%Y-%m-%d")
    parts = [f"Trio {day}"]
    new = {x: sum(1 for c in store.copies(since_ms=now_ms - DAY_MS, leader=x) if c.strategy == "cap20_cross")
           for x in leaders}
    parts.append("new trades " + ", ".join(f"{x}={n}" for x, n in new.items()))
    if virtual:
        eq, start = Decimal(virtual["equity"]), Decimal(virtual["start_equity"])
        parts.append(f"virtual equity ${eq:,.2f} ({(eq / start - 1) * 100:+.2f}%)")
        parts.append(f"exposure {Decimal(virtual['notional']) / eq if eq > 0 else 0:.2f}x")
    p50, p95, sl = st["latency_p50_ms"], st["latency_p95_ms"], st["slippage_median_bps"]
    parts.append(f"latency p50/p95 {p50 / 1000:.1f}s/{p95 / 1000:.1f}s" if p50 is not None and p95 is not None
                 else "latency n/a")
    parts.append(f"slippage median {sl / 100:.3f}%" if sl is not None else "slippage n/a")
    return " | ".join(parts)
