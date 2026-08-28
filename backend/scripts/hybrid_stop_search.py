"""Historical-only research (writes nothing back to the DB / live bot):
designs and backtests HYBRID stop-loss policies that combine the channel's
own stated Stop Loss with a systematic rule of our own, instead of using
either one purely.

Motivated by a real pattern found in the corrected 214-signal archive:
win rate and average profit both DEGRADE sharply once the channel's own
stated Stop Loss distance exceeds roughly 250-300 pips (bucketed analysis),
while the 70-200 pip range performs reasonably well. A wider channel Stop
Loss is not "more room to breathe" -- in this data it is a signal of lower
setup quality.

Every policy re-simulates ALL 214 signals against the real MT5 M5 bars
(entry price is unchanged -- only which Stop Loss level is used changes),
using the exact same conservative both-hit-in-one-bar -> LOSS rule as the
main archive.

Usage (from backend/): python scripts/hybrid_stop_search.py
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
_TP_PIPS = Decimal(100)  # our system's own target -- unchanged by this search


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
    """A BUY signal's Stop Loss on the wrong side of its own simulated entry
    by more than $100 is not a plausible fill-delay gap (the largest
    legitimate gap found across the whole archive is $62.45) -- it means
    the parser attached the wrong number as this signal's Stop Loss.
    Computed live (not a hardcoded id set) because ArchivedSignal.id
    reassigns on every `--reset` rebuild."""
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


def _simulate(
    bars: list[Bar],
    bar_times: list[datetime],
    ready_at: datetime,
    entry: Decimal,
    stop_loss: Decimal,
) -> tuple[Decimal, str] | None:
    """Returns (pips, outcome_label) where outcome_label is "WIN" (hit the
    take-profit level first) or "LOSS" (hit the Stop Loss level first,
    including the conservative both-in-one-bar case) -- matching the
    archive's own outcome definition exactly. NOT the same as "dollars >
    0": a LOSS can still carry a small positive dollar amount when the
    simulated entry itself already gapped past the stop by fill time (a
    real, previously-documented phenomenon) -- that must still count as a
    LOSS for win-rate purposes, or this script's win rate silently drifts
    from the definition used everywhere else in this project."""
    take_profit = entry + _TP_PIPS * _PIP_SIZE
    start_index = bisect_left(bar_times, ready_at)
    if start_index >= len(bars) or bars[start_index].time - ready_at > _MAX_FILL_GAP:
        return None
    for bar in bars[start_index:]:
        hit_sl = bar.low <= stop_loss
        hit_tp = bar.high >= take_profit
        if hit_sl:  # both-hit-in-one-bar and sl-only both resolve as the SL
            return ((stop_loss - entry) / _PIP_SIZE, "LOSS")
        if hit_tp:
            return ((take_profit - entry) / _PIP_SIZE, "WIN")
    return None


# --- candidate hybrid policies -------------------------------------------
# Each takes the channel's own SL distance in pips (always positive) and
# returns the pip distance to actually use. `None` means "skip this trade
# entirely" (the setup is judged too poor quality to take at all).

def policy_pure_channel(channel_pips: Decimal) -> Decimal | None:
    return channel_pips


def policy_flat_cap(cap: Decimal):
    def _p(channel_pips: Decimal) -> Decimal | None:
        return min(channel_pips, cap)
    return _p


def policy_trust_below_cap_above(trust_ceiling: Decimal, cap_above: Decimal):
    """Trust the channel's own number as-is up to `trust_ceiling`; beyond
    that, tighten to `cap_above` instead of trusting an increasingly wide
    number."""
    def _p(channel_pips: Decimal) -> Decimal | None:
        if channel_pips <= trust_ceiling:
            return channel_pips
        return cap_above
    return _p


def policy_skip_above(skip_threshold: Decimal):
    """Skip the trade entirely once the channel's stop is wider than the
    threshold -- treats an excessively wide stop as a low-quality setup,
    not just a risk to cap."""
    def _p(channel_pips: Decimal) -> Decimal | None:
        return None if channel_pips > skip_threshold else channel_pips
    return _p


def policy_skip_above_and_cap(skip_threshold: Decimal, trust_ceiling: Decimal, cap_above: Decimal):
    def _p(channel_pips: Decimal) -> Decimal | None:
        if channel_pips > skip_threshold:
            return None
        if channel_pips <= trust_ceiling:
            return channel_pips
        return cap_above
    return _p


def _evaluate_policy(signals, bars, bar_times, policy, name: str) -> dict:
    wins = losses = skipped = no_data = 0
    net = Decimal(0)
    gross_profit = Decimal(0)
    gross_loss = Decimal(0)

    for s in signals:
        entry = s.simulated_entry_price
        channel_pips = (entry - s.channel_stop_loss) / _PIP_SIZE
        chosen_pips = policy(channel_pips)
        if chosen_pips is None:
            skipped += 1
            continue
        stop_loss = entry - chosen_pips * _PIP_SIZE
        ready_at = s.sl_posted_at or s.signal_posted_at
        result = _simulate(bars, bar_times, ready_at, entry, stop_loss)
        if result is None:
            no_data += 1
            continue
        result_pips, label = result
        dollars = result_pips * _PIP_SIZE * _CONTRACT
        net += dollars
        # win/loss COUNTS follow the outcome label (did price hit the TP or
        # the SL level first) -- the same definition used everywhere else
        # in this project. gross_profit/gross_loss follow the dollar SIGN
        # instead (matching app/archive/calculations.py's convention): a
        # LOSS-labeled trade can still carry a small positive dollar amount
        # when the simulated entry itself already gapped past the stop by
        # fill time -- that stays counted as a loss, but its dollars still
        # belong in gross_profit, not gross_loss.
        if label == "WIN":
            wins += 1
        else:
            losses += 1
        if dollars > 0:
            gross_profit += dollars
        else:
            gross_loss += dollars

    decided = wins + losses
    win_rate = (Decimal(wins) / Decimal(decided) * 100) if decided else Decimal(0)
    taken = decided + no_data
    return {
        "name": name,
        "taken": taken,
        "skipped": skipped,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net": net,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "expectancy": (net / Decimal(decided)) if decided else Decimal(0),
    }


def main() -> None:
    signals = _load_signals()
    since = min(s.signal_posted_at for s in signals) - timedelta(days=1)
    bars = _load_bars(since)
    bar_times = [b.time for b in bars]

    policies = [
        ("baseline: pure channel SL (no hybrid)", policy_pure_channel),
        ("flat cap 100", policy_flat_cap(Decimal(100))),
        ("flat cap 150", policy_flat_cap(Decimal(150))),
        ("flat cap 200", policy_flat_cap(Decimal(200))),
        ("trust<=150, cap 100 above", policy_trust_below_cap_above(Decimal(150), Decimal(100))),
        ("trust<=200, cap 100 above", policy_trust_below_cap_above(Decimal(200), Decimal(100))),
        ("trust<=200, cap 150 above", policy_trust_below_cap_above(Decimal(200), Decimal(150))),
        ("trust<=250, cap 100 above", policy_trust_below_cap_above(Decimal(250), Decimal(100))),
        ("trust<=250, cap 150 above", policy_trust_below_cap_above(Decimal(250), Decimal(150))),
        ("trust<=300, cap 150 above", policy_trust_below_cap_above(Decimal(300), Decimal(150))),
        ("skip if channel SL>250", policy_skip_above(Decimal(250))),
        ("skip if channel SL>300", policy_skip_above(Decimal(300))),
        ("skip if channel SL>350", policy_skip_above(Decimal(350))),
        (
            "skip>300, trust<=200, cap150 above",
            policy_skip_above_and_cap(Decimal(300), Decimal(200), Decimal(150)),
        ),
        (
            "skip>250, trust<=150, cap100 above",
            policy_skip_above_and_cap(Decimal(250), Decimal(150), Decimal(100)),
        ),
    ]

    results = [_evaluate_policy(signals, bars, bar_times, p, name) for name, p in policies]
    results.sort(key=lambda r: -r["net"])

    print(f"\n{'='*118}")
    print("HYBRID STOP-LOSS POLICIES -- ranked by net profit (all vs the same 214-signal archive)")
    print(f"{'='*118}")
    header = (
        f"{'policy':<38} {'taken':>5} {'skip':>4} {'W':>4} {'L':>4} "
        f"{'win%':>7} {'net':>12} {'expectancy':>10}"
    )
    print(header)
    for r in results:
        print(
            f"{r['name']:<38} {r['taken']:>5} {r['skipped']:>4} {r['wins']:>4} {r['losses']:>4} "
            f"{r['win_rate']:>6.2f}% {r['net']:>12.2f} {r['expectancy']:>10.2f}"
        )


if __name__ == "__main__":
    main()
