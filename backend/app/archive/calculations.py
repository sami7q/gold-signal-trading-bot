"""Pure statistics over `ArchivedSignal` rows -- the historical, retrospective
channel analysis (PROJECT_SPEC.md-style: never mixed with live trade
statistics in app/analytics/calculations.py, and never a stored/mutable
number, same philosophy as the live stats).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.core.decimals import CleanDecimal
from app.database.models.archived_signal import ArchivedSignal

_ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class ArchiveSummary:
    total_signals: int
    ready_signals: int
    non_executable_signals: int
    no_sl_signals: int
    ambiguous_or_other_signals: int
    wins: int
    losses: int
    unresolved: int
    no_price_data: int
    win_rate: CleanDecimal
    net_profit_estimate: CleanDecimal
    gross_profit_estimate: CleanDecimal
    gross_loss_estimate: CleanDecimal


def compute_archive_summary(rows: list[ArchivedSignal]) -> ArchiveSummary:
    ready = [r for r in rows if r.classification == "READY"]
    wins = [r for r in ready if r.outcome == "WIN"]
    losses = [r for r in ready if r.outcome == "LOSS"]
    unresolved = [r for r in ready if r.outcome == "UNRESOLVED"]
    no_price_data = [r for r in ready if r.outcome == "NO_PRICE_DATA"]

    decided = len(wins) + len(losses)
    win_rate = (Decimal(len(wins)) / Decimal(decided) * 100) if decided else _ZERO

    profits = [r.profit_estimate for r in ready if r.profit_estimate is not None]
    net_profit = sum(profits, _ZERO)
    gross_profit = sum((p for p in profits if p > 0), _ZERO)
    gross_loss = sum((p for p in profits if p < 0), _ZERO)

    return ArchiveSummary(
        total_signals=len(rows),
        ready_signals=len(ready),
        non_executable_signals=len([r for r in rows if r.classification == "NON_EXECUTABLE_TYPE"]),
        no_sl_signals=len([r for r in rows if r.classification == "NO_SL"]),
        ambiguous_or_other_signals=len(
            [r for r in rows if r.classification == "AMBIGUOUS_OR_OTHER"]
        ),
        wins=len(wins),
        losses=len(losses),
        unresolved=len(unresolved),
        no_price_data=len(no_price_data),
        win_rate=win_rate,
        net_profit_estimate=net_profit,
        gross_profit_estimate=gross_profit,
        gross_loss_estimate=gross_loss,
    )
