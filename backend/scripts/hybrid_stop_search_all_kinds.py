"""Same 'skip if channel Stop Loss is too wide' hybrid filter as
hybrid_stop_search.py, generalized to ALL signal kinds (BUY/SELL,
MARKET/LIMIT) instead of just BUY MARKET. Historical-only research; writes
nothing to the DB or live bot.

Usage (from backend/): python scripts/hybrid_stop_search_all_kinds.py
"""

from __future__ import annotations

import sys
from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.constants import Side  # noqa: E402
from app.database.models.archived_signal import ArchivedSignal  # noqa: E402
from app.database.session import get_engine  # noqa: E402

_PIP_SIZE = Decimal("0.1")
_CONTRACT = Decimal("100")
_MAX_FILL_GAP = timedelta(days=3)
_MAX_LIMIT_WAIT = timedelta(days=14)
_TP_PIPS = Decimal(100)


@dataclass(frozen=True, slots=True)
class Bar:
    time: datetime
    high: Decimal
    low: Decimal


def _load_bars(since: datetime) -> list[Bar]:
    import MetaTrader5 as mt5

    from app.core.constants import CANONICAL_GOLD_SYMBOL

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")
    bars: list[Bar] = []
    chunk_start = since
    now = datetime.now(UTC)
    while chunk_start < now:
        chunk_end = min(chunk_start + timedelta(days=30), now)
        rates = mt5.copy_rates_range(
            CANONICAL_GOLD_SYMBOL, mt5.TIMEFRAME_M5, chunk_start, chunk_end
        )
        if rates is not None:
            for r in rates:
                bar_time = datetime.fromtimestamp(int(r["time"]), tz=UTC)
                if chunk_start <= bar_time <= chunk_end:
                    bars.append(
                        Bar(time=bar_time, high=Decimal(str(r["high"])), low=Decimal(str(r["low"])))
                    )
        chunk_start = chunk_end
    mt5.shutdown()
    bars.sort(key=lambda b: b.time)
    print(f"Loaded {len(bars)} M5 bars since {since}.")
    return bars


def _sl_distance_pips(s: ArchivedSignal) -> Decimal:
    entry = s.simulated_entry_price
    if s.side == Side.BUY:
        return (entry - s.channel_stop_loss) / _PIP_SIZE
    return (s.channel_stop_loss - entry) / _PIP_SIZE


def _load_signals() -> list[ArchivedSignal]:
    settings = get_settings()
    channel_id = str(int(settings.telegram_allowed_channel_id))
    engine = get_engine()
    with Session(engine) as session:
        rows = list(
            session.exec(
                select(ArchivedSignal).where(
                    ArchivedSignal.channel_id == channel_id,
                    ArchivedSignal.classification.in_(["READY", "NON_EXECUTABLE_TYPE"]),  # type: ignore[attr-defined]
                )
            )
        )
    # A Stop Loss on the wrong side of entry by more than $100 (1000 pips)
    # is a parser data-quality issue, not a real setup -- computed live
    # (not a hardcoded id set), since ArchivedSignal.id reassigns on every
    # `--reset` rebuild.
    rows = [
        r
        for r in rows
        if r.simulated_entry_price is not None and _sl_distance_pips(r) > Decimal(-1000)
    ]
    rows.sort(key=lambda r: r.signal_posted_at)
    return rows


def _simulate_with_stop(
    bars: list[Bar],
    bar_times: list[datetime],
    ready_at: datetime,
    entry: Decimal,
    stop_loss: Decimal,
    side: Side,
) -> tuple[Decimal, str] | None:
    if side == Side.BUY:
        take_profit = entry + _TP_PIPS * _PIP_SIZE
    else:
        take_profit = entry - _TP_PIPS * _PIP_SIZE

    start_index = bisect_left(bar_times, ready_at)
    if start_index >= len(bars) or bars[start_index].time - ready_at > _MAX_FILL_GAP:
        return None

    for bar in bars[start_index:]:
        if side == Side.BUY:
            hit_sl = bar.low <= stop_loss
            hit_tp = bar.high >= take_profit
        else:
            hit_sl = bar.high >= stop_loss
            hit_tp = bar.low <= take_profit
        if hit_sl:
            if side == Side.BUY:
                pips = (stop_loss - entry) / _PIP_SIZE
            else:
                pips = (entry - stop_loss) / _PIP_SIZE
            return (pips, "LOSS")
        if hit_tp:
            if side == Side.BUY:
                pips = (take_profit - entry) / _PIP_SIZE
            else:
                pips = (entry - take_profit) / _PIP_SIZE
            return (pips, "WIN")
    return None


def _evaluate(signals, bars, bar_times, skip_threshold: Decimal | None) -> dict:
    wins = losses = skipped = no_data = 0
    net = Decimal(0)
    by_kind_net: dict = {}

    for s in signals:
        dist = _sl_distance_pips(s)
        if skip_threshold is not None and dist > skip_threshold:
            skipped += 1
            continue
        ready_at = s.sl_posted_at or s.signal_posted_at
        result = _simulate_with_stop(
            bars, bar_times, ready_at, s.simulated_entry_price, s.channel_stop_loss, s.side
        )
        if result is None:
            no_data += 1
            continue
        result_pips, label = result
        dollars = result_pips * _PIP_SIZE * _CONTRACT
        net += dollars
        key = (s.side, s.signal_type)
        by_kind_net[key] = by_kind_net.get(key, Decimal(0)) + dollars
        if label == "WIN":
            wins += 1
        else:
            losses += 1

    decided = wins + losses
    win_rate = (Decimal(wins) / Decimal(decided) * 100) if decided else Decimal(0)
    return {
        "skip_threshold": skip_threshold,
        "taken": decided + no_data,
        "skipped": skipped,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net": net,
        "by_kind_net": by_kind_net,
    }


def main() -> None:
    signals = _load_signals()
    since = min(s.signal_posted_at for s in signals) - timedelta(days=1)
    bars = _load_bars(since)
    bar_times = [b.time for b in bars]

    print(f"\nTotal signals (all kinds, clean): {len(signals)}")
    print(f"{'threshold':>10} {'taken':>6} {'skip':>5} {'W':>4} {'L':>4} {'win%':>7} {'net':>12}")
    for threshold in [None, 400, 350, 300, 250, 200, 180, 170, 160, 150, 140, 130, 120, 100, 80]:
        t = Decimal(threshold) if threshold is not None else None
        r = _evaluate(signals, bars, bar_times, t)
        label = "none" if t is None else str(threshold)
        print(
            f"{label:>10} {r['taken']:>6} {r['skipped']:>5} {r['wins']:>4} {r['losses']:>4} "
            f"{r['win_rate']:>6.2f}% {r['net']:>12.2f}"
        )

    print("\nBreakdown by kind at threshold=150:")
    r = _evaluate(signals, bars, bar_times, Decimal(150))
    for k, v in sorted(r["by_kind_net"].items()):
        print(f"  {k}: net=${v:.2f}")


if __name__ == "__main__":
    main()
