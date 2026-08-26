"""HIGH-1 fix: signal/Stop-Loss disambiguation when multiple signals from
the same source/channel/symbol are simultaneously WAITING_FOR_DETAILS.

PROJECT_SPEC.md section 32, rule 13: any ambiguity that could create a
trade must default to no trade. These tests lock in that a details/SL
message is only ever attached when it can be resolved to exactly one
signal — never guessed onto "the newest one".
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import select

from app.audit.service import AuditService
from app.core.constants import AuditEventType, SignalStatus
from app.database.models.audit_event import AuditEvent
from app.signals.models import IncomingMessageData
from app.signals.service import SignalService


def _msg(text: str, mid: str, *, channel: str = "chan-1", reply_to: str | None = None):
    now = datetime.now(UTC)
    return IncomingMessageData(
        source="mock",
        channel_id=channel,
        message_id=mid,
        text=text,
        sent_at=now,
        received_at=now,
        reply_to_message_id=reply_to,
    )


def _service(session) -> SignalService:
    return SignalService(session, AuditService(session), signal_completion_timeout_seconds=120)


def test_one_waiting_signal_attaches_normally(session):
    svc = _service(session)
    svc.ingest_message(_msg("BUY GOLD", "a1"))
    result = svc.ingest_message(_msg("4640 - 4637\nSL 4623", "a2"))

    assert result.ambiguous is False
    assert result.became_ready is True
    assert result.signal.status == SignalStatus.READY
    assert result.signal.channel_stop_loss == 4623


def test_two_waiting_signals_without_reply_is_ambiguous_no_trade(session):
    svc = _service(session)
    a = svc.ingest_message(_msg("BUY GOLD", "a1"))
    b = svc.ingest_message(_msg("BUY GOLD", "b1"))

    result = svc.ingest_message(_msg("SL 4623", "sl-generic"))

    assert result.ambiguous is True
    assert result.signal is None
    assert result.became_ready is False

    session.refresh(a.signal)
    session.refresh(b.signal)
    assert a.signal.status == SignalStatus.WAITING_FOR_DETAILS
    assert b.signal.status == SignalStatus.WAITING_FOR_DETAILS
    assert a.signal.channel_stop_loss is None
    assert b.signal.channel_stop_loss is None

    events = list(session.exec(select(AuditEvent)))
    assert any(e.event_type == AuditEventType.AMBIGUOUS_SIGNAL_DETAILS.value for e in events)


def test_two_waiting_signals_reply_resolves_first_signal(session):
    svc = _service(session)
    a = svc.ingest_message(_msg("BUY GOLD", "a1"))
    b = svc.ingest_message(_msg("BUY GOLD", "b1"))

    result = svc.ingest_message(_msg("SL 4623", "sl-for-a", reply_to="a1"))

    assert result.ambiguous is False
    assert result.signal.id == a.signal.id
    assert result.became_ready is True

    session.refresh(a.signal)
    session.refresh(b.signal)
    assert a.signal.status == SignalStatus.READY
    assert a.signal.channel_stop_loss == 4623
    assert b.signal.status == SignalStatus.WAITING_FOR_DETAILS
    assert b.signal.channel_stop_loss is None


def test_two_waiting_signals_reply_resolves_second_signal(session):
    svc = _service(session)
    a = svc.ingest_message(_msg("BUY GOLD", "a1"))
    b = svc.ingest_message(_msg("BUY GOLD", "b1"))

    result = svc.ingest_message(_msg("SL 4600", "sl-for-b", reply_to="b1"))

    assert result.ambiguous is False
    assert result.signal.id == b.signal.id
    assert result.became_ready is True

    session.refresh(a.signal)
    session.refresh(b.signal)
    assert b.signal.status == SignalStatus.READY
    assert b.signal.channel_stop_loss == 4600
    assert a.signal.status == SignalStatus.WAITING_FOR_DETAILS
    assert a.signal.channel_stop_loss is None


def test_invalid_reply_reference_is_treated_as_ambiguous(session):
    svc = _service(session)
    a = svc.ingest_message(_msg("BUY GOLD", "a1"))
    b = svc.ingest_message(_msg("BUY GOLD", "b1"))

    result = svc.ingest_message(_msg("SL 4623", "sl-bad-reply", reply_to="does-not-exist"))

    assert result.ambiguous is True
    assert result.signal is None

    session.refresh(a.signal)
    session.refresh(b.signal)
    assert a.signal.status == SignalStatus.WAITING_FOR_DETAILS
    assert b.signal.status == SignalStatus.WAITING_FOR_DETAILS


def test_duplicate_details_message_still_detected(session):
    svc = _service(session)
    svc.ingest_message(_msg("BUY GOLD", "a1"))
    first = svc.ingest_message(_msg("SL 4623", "sl-1"))
    second = svc.ingest_message(_msg("SL 4623", "sl-1"))

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.ambiguous is False


def test_target_only_message_while_multiple_signals_wait_changes_nothing(session):
    svc = _service(session)
    a = svc.ingest_message(_msg("BUY GOLD", "a1"))
    b = svc.ingest_message(_msg("BUY GOLD", "b1"))

    result = svc.ingest_message(_msg("4644\n4650\n4663", "targets-1"))

    assert result.duplicate is False
    assert result.ambiguous is False  # informational messages don't use the ambiguity flag
    assert result.signal is None  # never guessed onto either signal

    session.refresh(a.signal)
    session.refresh(b.signal)
    assert a.signal.status == SignalStatus.WAITING_FOR_DETAILS
    assert b.signal.status == SignalStatus.WAITING_FOR_DETAILS


def test_reply_to_a_signal_not_in_the_candidate_set_is_ambiguous(session):
    """Replying to a message from a signal that's already EXECUTED (no
    longer "waiting") must not be treated as a valid resolution either."""
    svc = _service(session)
    # Signal 1: completes on its own (single-candidate path), becomes EXECUTED-ready.
    svc.ingest_message(_msg("BUY GOLD", "done-a1", channel="chan-2"))
    svc.ingest_message(_msg("SL 4600", "done-a2", channel="chan-2"))

    # Two NEW signals now waiting on the same channel.
    a = svc.ingest_message(_msg("BUY GOLD", "a1", channel="chan-2"))
    b = svc.ingest_message(_msg("BUY GOLD", "b1", channel="chan-2"))

    # Reply references the already-resolved signal's message, not one of
    # the two currently-waiting candidates.
    result = svc.ingest_message(
        _msg("SL 4623", "sl-stale-reply", channel="chan-2", reply_to="done-a1")
    )

    assert result.ambiguous is True
    session.refresh(a.signal)
    session.refresh(b.signal)
    assert a.signal.status == SignalStatus.WAITING_FOR_DETAILS
    assert b.signal.status == SignalStatus.WAITING_FOR_DETAILS
