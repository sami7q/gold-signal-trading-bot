"""Final comprehensive grid search: sweeps take-profit target AND the
channel-SL skip-threshold together, over both the full BUY MARKET archive
and the first-trade-of-day subset, at lot 3.3, to find the settings that
maximize win rate and/or net profit. Historical-only research; writes
nothing to the DB or live bot.

Usage (from backend/): python scripts/final_optimal_search.py
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
from app.database.models.archived_signal import ArchivedSignal  # noqa: E402
from app.database.session import get_engine  # noqa: E402

_PIP_SIZE = Decimal("0.1")
_CONTRACT = Decimal("100")
_MAX_FILL_GAP = timedelta(days=3)
_LOT = Decimal("3.3")


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


def _first_of_day(signals: list[ArchivedSignal]) -> list[ArchivedSignal]:
    by_day: dict = {}
    for s in signals:
        by_day.setdefault(s.signal_posted_at.date(), []).append(s)
    out = []
    for items in by_day.values():
        items.sort(key=lambda s: s.signal_posted_at)
        out.append(items[0])
    out.sort(key=lambda s: s.signal_posted_at)
    return out


def _simulate(bars, bar_times, ready_at, entry, stop_loss, tp_pips):
    take_profit = entry + tp_pips * _PIP_SIZE
    start_index = bisect_left(bar_times, ready_at)
    if start_index >= len(bars) or bars[start_index].time - ready_at > _MAX_FILL_GAP:
        return None
    for bar in bars[start_index:]:
        hit_sl = bar.low <= stop_loss
        hit_tp = bar.high >= take_profit
        if hit_sl:
            return ((stop_loss - entry) / _PIP_SIZE, "LOSS")
        if hit_tp:
            return ((take_profit - entry) / _PIP_SIZE, "WIN")
    return None


def _evaluate(signals, bars, bar_times, tp_pips, skip_threshold):
    wins = losses = skipped = no_data = 0
    net = Decimal(0)
    for s in signals:
        entry = s.simulated_entry_price
        channel_pips = (entry - s.channel_stop_loss) / _PIP_SIZE
        if skip_threshold is not None and channel_pips > skip_threshold:
            skipped += 1
            continue
        ready_at = s.sl_posted_at or s.signal_posted_at
        result = _simulate(bars, bar_times, ready_at, entry, s.channel_stop_loss, tp_pips)
        if result is None:
            no_data += 1
            continue
        result_pips, label = result
        dollars = result_pips * _PIP_SIZE * _CONTRACT * _LOT
        net += dollars
        if label == "WIN":
            wins += 1
        else:
            losses += 1
    decided = wins + losses
    win_rate = (Decimal(wins) / Decimal(decided) * 100) if decided else Decimal(0)
    return {
        "tp_pips": tp_pips,
        "skip_threshold": skip_threshold,
        "taken": decided + no_data,
        "skipped": skipped,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net": net,
    }


def main() -> None:
    signals = _load_signals()
    fod = _first_of_day(signals)
    since = min(s.signal_posted_at for s in signals) - timedelta(days=1)
    bars = _load_bars(since)
    bar_times = [b.time for b in bars]

    tp_options = [Decimal(x) for x in (15, 20, 25, 30, 40, 50, 70, 100)]
    skip_options = [None] + [
        Decimal(x) for x in (400, 300, 250, 200, 170, 150, 120, 100, 80)
    ]

    results_full = []
    results_fod = []
    for tp in tp_options:
        for skip in skip_options:
            results_full.append(_evaluate(signals, bars, bar_times, tp, skip))
            results_fod.append(_evaluate(fod, bars, bar_times, tp, skip))

    def report(results, label):
        print(f"\n{'='*100}")
        print(f"{label} -- TOP 15 BY NET PROFIT (min 20 trades taken)")
        print(f"{'='*100}")
        valid = [r for r in results if (r["wins"] + r["losses"]) >= 20]
        by_net = sorted(valid, key=lambda r: -r["net"])[:15]
        header = f"{'TP':>4} {'SLcap':>6} {'taken':>6} {'W':>4} {'L':>4} {'win%':>7} {'net':>14}"
        print(header)
        for r in by_net:
            sl_str = str(r["skip_threshold"]) if r["skip_threshold"] is not None else "none"
            wl = r["wins"] + r["losses"]
            row1 = f"{r['tp_pips']!s:>4} {sl_str:>6} {wl:>6} {r['wins']:>4} {r['losses']:>4}"
            row2 = f"{r['win_rate']:>6.2f}% {r['net']:>14.2f}"
            print(f"{row1} {row2}")

        print(f"\n{label} -- TOP 15 BY WIN RATE (min 20 trades, net > 0)")
        by_wr = sorted([r for r in valid if r["net"] > 0], key=lambda r: -r["win_rate"])[:15]
        print(header)
        for r in by_wr:
            sl_str = str(r["skip_threshold"]) if r["skip_threshold"] is not None else "none"
            wl = r["wins"] + r["losses"]
            row1 = f"{r['tp_pips']!s:>4} {sl_str:>6} {wl:>6} {r['wins']:>4} {r['losses']:>4}"
            row2 = f"{r['win_rate']:>6.2f}% {r['net']:>14.2f}"
            print(f"{row1} {row2}")

    report(results_full, "FULL BUY-MARKET ARCHIVE (214 signals)")
    report(results_fod, "FIRST-TRADE-OF-DAY (148 signals)")


if __name__ == "__main__":
    main()
