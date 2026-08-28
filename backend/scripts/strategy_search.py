"""Historical strategy search (NOT written back to the archive DB, NOT a
change to the live bot): sweeps a grid of (take-profit pips, stop-loss cap,
entry universe, day-window, daily/2-day pip target) over the REAL corrected
archive + real MT5 M5 bars, to find which combination historically would
have reached +50/+100 pips per day-or-2-days most often.

Re-simulates every signal's outcome per grid cell (the take-profit and the
capped stop-loss both change per cell, so the WIN/LOSS crossing has to be
recomputed against the real bars each time -- reusing the already-loaded
bars, not re-fetching per cell).

Every number this prints is a genuine backtest result over ~105-167
historical days -- there is no held-out/future data to validate against,
so ANY result here carries real curve-fitting risk on a small sample.
Nothing here is a promise about future performance.

Usage (from backend/): python scripts/strategy_search.py
"""

from __future__ import annotations

import sys
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.database.models.archived_signal import ArchivedSignal  # noqa: E402
from app.database.session import get_engine  # noqa: E402

_PIP_SIZE = Decimal("0.1")
_CONTRACT = Decimal("100")
_MAX_FILL_GAP = timedelta(days=3)


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


def _is_invalid_sl(r: ArchivedSignal) -> bool:
    """Computed live, not a hardcoded id set -- ArchivedSignal.id reassigns
    on every `--reset` rebuild. See hybrid_stop_search.py for the full
    rationale ($100 gap threshold)."""
    if r.simulated_entry_price is None or r.channel_stop_loss is None:
        return False
    return (r.channel_stop_loss - r.simulated_entry_price) > Decimal(100)


def _load_signals() -> list[ArchivedSignal]:
    settings = get_settings()
    channel_id = str(int(settings.telegram_allowed_channel_id))
    engine = get_engine()
    with Session(engine) as session:
        rows = list(
            session.exec(
                select(ArchivedSignal).where(
                    ArchivedSignal.channel_id == channel_id,
                    ArchivedSignal.classification == "READY",
                )
            )
        )
    rows = [r for r in rows if r.simulated_entry_price is not None and not _is_invalid_sl(r)]
    rows.sort(key=lambda r: r.signal_posted_at)
    return rows


def _simulate_pips(
    bars: list[Bar],
    bar_times: list[datetime],
    ready_at: datetime,
    entry: Decimal,
    channel_sl: Decimal,
    tp_pips: Decimal,
    sl_cap_pips: Decimal | None,
) -> Decimal | None:
    """Returns the outcome in PIPS (positive win, negative loss), or None if
    unresolved/no price data."""
    take_profit = entry + tp_pips * _PIP_SIZE
    if sl_cap_pips is None:
        stop_loss = channel_sl
    else:
        capped = entry - sl_cap_pips * _PIP_SIZE
        stop_loss = max(channel_sl, capped)  # never risk more than the cap

    start_index = bisect_left(bar_times, ready_at)
    if start_index >= len(bars) or bars[start_index].time - ready_at > _MAX_FILL_GAP:
        return None

    for bar in bars[start_index:]:
        hit_sl = bar.low <= stop_loss
        hit_tp = bar.high >= take_profit
        if hit_sl and hit_tp:
            return (stop_loss - entry) / _PIP_SIZE  # conservative
        if hit_sl:
            return (stop_loss - entry) / _PIP_SIZE
        if hit_tp:
            return (take_profit - entry) / _PIP_SIZE
    return None  # unresolved within available history


def _evaluate(
    signals: list[ArchivedSignal],
    bars: list[Bar],
    bar_times: list[datetime],
    *,
    tp_pips: Decimal,
    sl_cap_pips: Decimal | None,
    first_of_day_only: bool,
    window_days: int,
    day_target_pips: Decimal,
) -> dict:
    # 1. per-signal pip outcome (memoized per (tp, sl_cap) combo)
    results: dict[int, Decimal] = {}
    for s in signals:
        ready_at = s.sl_posted_at or s.signal_posted_at
        pips = _simulate_pips(
            bars,
            bar_times,
            ready_at,
            s.simulated_entry_price,
            s.channel_stop_loss,
            tp_pips,
            sl_cap_pips,
        )
        if pips is not None:
            results[s.id] = pips

    usable = [s for s in signals if s.id in results]

    by_day: dict = defaultdict(list)
    for s in usable:
        by_day[(s.sl_posted_at or s.signal_posted_at).date()].append(s)

    if first_of_day_only:
        for day in by_day:
            by_day[day].sort(key=lambda s: s.sl_posted_at or s.signal_posted_at)
            by_day[day] = by_day[day][:1]

    days_sorted = sorted(by_day.keys())

    # group into non-overlapping windows of `window_days` consecutive calendar days
    windows: list[list] = []
    i = 0
    while i < len(days_sorted):
        window_signals = []
        for d in days_sorted[i : i + window_days]:
            window_signals.extend(by_day[d])
        windows.append(sorted(window_signals, key=lambda s: s.sl_posted_at or s.signal_posted_at))
        i += window_days

    hit_target = 0
    final_pips_sum = Decimal(0)
    for w_signals in windows:
        cumulative = Decimal(0)
        peak = Decimal(0)
        for s in w_signals:
            if cumulative >= day_target_pips:
                break
            cumulative += results[s.id]
            peak = max(peak, cumulative)
        final_pips_sum += cumulative
        if peak >= day_target_pips:
            hit_target += 1

    n_windows = len(windows)
    return {
        "n_windows": n_windows,
        "hit_rate": (Decimal(hit_target) / Decimal(n_windows) * 100) if n_windows else Decimal(0),
        "avg_final_pips": (final_pips_sum / Decimal(n_windows)) if n_windows else Decimal(0),
        "signals_used": len(usable),
        "signals_total": len(signals),
    }


def main() -> None:
    signals = _load_signals()
    since = min(s.signal_posted_at for s in signals) - timedelta(days=1)
    bars = _load_bars(since)
    bar_times = [b.time for b in bars]

    tp_options = [Decimal(50), Decimal(100)]
    sl_cap_options: list[Decimal | None] = [
        None,
        Decimal(30),
        Decimal(50),
        Decimal(70),
        Decimal(100),
    ]
    first_of_day_options = [True, False]
    window_options = [1, 2]
    day_target_options = [Decimal(50), Decimal(100)]

    grid_results = []
    for tp in tp_options:
        for sl_cap in sl_cap_options:
            for fod in first_of_day_options:
                for window in window_options:
                    for day_target in day_target_options:
                        r = _evaluate(
                            signals,
                            bars,
                            bar_times,
                            tp_pips=tp,
                            sl_cap_pips=sl_cap,
                            first_of_day_only=fod,
                            window_days=window,
                            day_target_pips=day_target,
                        )
                        grid_results.append(
                            {
                                "tp_pips": tp,
                                "sl_cap": sl_cap,
                                "first_of_day_only": fod,
                                "window_days": window,
                                "day_target_pips": day_target,
                                **r,
                            }
                        )

    grid_results.sort(key=lambda x: -x["hit_rate"])
    print(f"\n{'='*100}")
    print("TOP 25 combinations by hit-rate (reaching the day/window pip target):")
    print(f"{'='*100}")
    cols = ["TP", "SLcap", "1stOnly", "Window", "DayTgt", "HitRate", "AvgPips", "Windows", "Sigs"]
    line1 = f"{cols[0]:>4} {cols[1]:>6} {cols[2]:>8} {cols[3]:>7} {cols[4]:>7}"
    line2 = f"{cols[5]:>8} {cols[6]:>9} {cols[7]:>8} {cols[8]:>6}"
    print(f"{line1} {line2}")
    for r in grid_results[:25]:
        sl_cap_str = str(r["sl_cap"]) if r["sl_cap"] is not None else "none"
        sigs = f"{r['signals_used']}/{r['signals_total']}"
        print(
            f"{r['tp_pips']!s:>4} {sl_cap_str:>6} {str(r['first_of_day_only']):>8} "
            f"{r['window_days']!s:>7} {r['day_target_pips']!s:>7} {r['hit_rate']:>7.2f}% "
            f"{r['avg_final_pips']:>9.2f} {r['n_windows']:>8} {sigs:>6}"
        )


if __name__ == "__main__":
    main()
