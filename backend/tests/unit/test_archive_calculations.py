"""compute_archive_summary -- pure aggregation over ArchivedSignal rows,
kept entirely separate from live trade statistics."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.archive.calculations import compute_archive_summary
from app.database.models.archived_signal import ArchivedSignal

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _row(
    *, classification: str, outcome: str | None = None, profit: Decimal | None = None
) -> ArchivedSignal:
    return ArchivedSignal(
        channel_id="chan",
        message_id=f"msg-{id(object())}",
        symbol="XAUUSD",
        side="BUY",
        signal_type="MARKET",
        raw_entry_text="BUY GOLD",
        signal_posted_at=_NOW,
        classification=classification,
        outcome=outcome,
        profit_estimate=profit,
    )


def test_win_rate_only_counts_decided_outcomes():
    rows = [
        _row(classification="READY", outcome="WIN", profit=Decimal("1000")),
        _row(classification="READY", outcome="LOSS", profit=Decimal("-500")),
        _row(classification="READY", outcome="UNRESOLVED"),
        _row(classification="READY", outcome="NO_PRICE_DATA"),
    ]

    summary = compute_archive_summary(rows)

    assert summary.wins == 1
    assert summary.losses == 1
    assert summary.unresolved == 1
    assert summary.no_price_data == 1
    assert summary.win_rate == Decimal("50")  # 1 of 2 *decided* outcomes, not 1 of 4


def test_net_profit_sums_only_ready_rows_with_a_profit_estimate():
    rows = [
        _row(classification="READY", outcome="WIN", profit=Decimal("1000")),
        _row(classification="READY", outcome="LOSS", profit=Decimal("-300")),
        _row(classification="NON_EXECUTABLE_TYPE"),  # never simulated, no profit field
    ]

    summary = compute_archive_summary(rows)

    assert summary.net_profit_estimate == Decimal("700")
    assert summary.gross_profit_estimate == Decimal("1000")
    assert summary.gross_loss_estimate == Decimal("-300")


def test_classification_buckets_are_counted_independently_of_outcome():
    rows = [
        _row(classification="READY", outcome="WIN", profit=Decimal("1000")),
        _row(classification="NON_EXECUTABLE_TYPE"),
        _row(classification="NON_EXECUTABLE_TYPE"),
        _row(classification="NO_SL"),
        _row(classification="AMBIGUOUS_OR_OTHER"),
    ]

    summary = compute_archive_summary(rows)

    assert summary.total_signals == 5
    assert summary.ready_signals == 1
    assert summary.non_executable_signals == 2
    assert summary.no_sl_signals == 1
    assert summary.ambiguous_or_other_signals == 1


def test_no_rows_is_a_clean_zeroed_summary():
    summary = compute_archive_summary([])
    assert summary.total_signals == 0
    assert summary.win_rate == Decimal("0")
    assert summary.net_profit_estimate == Decimal("0")
