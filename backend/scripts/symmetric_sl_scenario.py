"""One-off hypothetical scenario (NOT written back to the archive DB):
"what if every BUY MARKET signal's Stop Loss was symmetric to the target --
also exactly 100 pips / $10 away from entry -- instead of the channel's own
(often much farther) stated Stop Loss?"

Re-simulates each already-archived READY (BUY MARKET) signal's outcome
using the SAME real MT5 M5 bars and the SAME simulated entry price already
computed by build_channel_archive.py, but replaces the channel's Stop Loss
with `entry_price - $10`. Everything else (fill logic, conservative
both-hit-in-one-bar -> LOSS rule, max fill gap) is identical.

This is explicitly a hypothetical comparison, not a correction to the real
archive: the live bot's real Stop Loss is (and must remain) the channel's
own stated number -- confirmed against app/trading/engine.py -- because
that is what actually gets sent to the broker. This script exists only to
answer "how would the numbers look under a symmetric-risk assumption".

Usage (from backend/): python scripts/symmetric_sl_scenario.py [--first-of-day-only]
"""

from __future__ import annotations

import argparse
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

_CONTRACT_SIZE = Decimal("100")
_TARGET_PIPS = Decimal("100")
_PIP_SIZE = Decimal("0.1")
_TP_DISTANCE = _PIP_SIZE * _TARGET_PIPS  # $10.00
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
                if not (chunk_start <= bar_time <= chunk_end):
                    continue
                bars.append(
                    Bar(time=bar_time, high=Decimal(str(r["high"])), low=Decimal(str(r["low"])))
                )
        chunk_start = chunk_end
    mt5.shutdown()
    bars.sort(key=lambda b: b.time)
    print(f"Loaded {len(bars)} M5 bars since {since}.")
    return bars


def _resimulate(
    bars: list[Bar], bar_times: list[datetime], ready_at: datetime, entry_price: Decimal
) -> tuple[str, Decimal | None, datetime | None]:
    take_profit = entry_price + _TP_DISTANCE
    symmetric_stop = entry_price - _TP_DISTANCE  # <-- the only thing that changes

    start_index = bisect_left(bar_times, ready_at)
    if start_index >= len(bars) or bars[start_index].time - ready_at > _MAX_FILL_GAP:
        return ("NO_PRICE_DATA", None, None)

    for bar in bars[start_index:]:
        hit_sl = bar.low <= symmetric_stop
        hit_tp = bar.high >= take_profit
        if hit_sl and hit_tp:
            return ("LOSS", symmetric_stop, bar.time)  # conservative, same rule as the main archive
        if hit_sl:
            return ("LOSS", symmetric_stop, bar.time)
        if hit_tp:
            return ("WIN", take_profit, bar.time)
    return ("UNRESOLVED", None, None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--first-of-day-only",
        action="store_true",
        help="Only re-simulate the first READY BUY-MARKET signal of each calendar day.",
    )
    args = parser.parse_args()

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

    def _is_invalid_sl(r: ArchivedSignal) -> bool:
        if r.simulated_entry_price is None or r.channel_stop_loss is None:
            return False
        return (r.channel_stop_loss - r.simulated_entry_price) > Decimal(100)

    rows = [r for r in rows if r.simulated_entry_price is not None and not _is_invalid_sl(r)]
    rows.sort(key=lambda r: r.signal_posted_at)

    if args.first_of_day_only:
        by_day: dict = {}
        for r in rows:
            day = r.signal_posted_at.date()
            by_day.setdefault(day, []).append(r)
        rows = [sorted(items, key=lambda r: r.signal_posted_at)[0] for items in by_day.values()]
        rows.sort(key=lambda r: r.signal_posted_at)

    print(f"Re-simulating {len(rows)} signal(s) under a SYMMETRIC 100-pip Stop Loss...")

    since = min(r.signal_posted_at for r in rows) - timedelta(days=1)
    bars = _load_bars(since)
    bar_times = [b.time for b in bars]

    wins = losses = no_data = unresolved = 0
    net = Decimal(0)
    gross_profit = Decimal(0)
    gross_loss = Decimal(0)

    for r in rows:
        ready_at = r.sl_posted_at or r.signal_posted_at
        outcome, _outcome_price, _outcome_at = _resimulate(
            bars, bar_times, ready_at, r.simulated_entry_price
        )
        if outcome == "WIN":
            wins += 1
            net += _TP_DISTANCE * _CONTRACT_SIZE
            gross_profit += _TP_DISTANCE * _CONTRACT_SIZE
        elif outcome == "LOSS":
            losses += 1
            net -= _TP_DISTANCE * _CONTRACT_SIZE
            gross_loss -= _TP_DISTANCE * _CONTRACT_SIZE
        elif outcome == "NO_PRICE_DATA":
            no_data += 1
        else:
            unresolved += 1

    decided = wins + losses
    win_rate = (Decimal(wins) / Decimal(decided) * 100) if decided else Decimal(0)

    print("\n=== SYMMETRIC 100-PIP STOP LOSS SCENARIO ===")
    print(f"Total re-simulated: {len(rows)}")
    print(f"WIN: {wins}  LOSS: {losses}  UNRESOLVED: {unresolved}  NO_PRICE_DATA: {no_data}")
    print(f"Win rate (decided only): {win_rate:.4f}%")
    print(f"Gross profit: ${gross_profit}  Gross loss: ${gross_loss}  Net: ${net}")


if __name__ == "__main__":
    main()
