"""State machine transitions from PROJECT_SPEC.md section 17.2, exercised
through `SignalService` (the only thing that persists state transitions)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.audit.service import AuditService
from app.core.constants import SignalStatus
from app.signals.models import IncomingMessageData
from app.signals.service import SignalService


def _msg(text: str, message_id: str, sent_at: datetime | None = None) -> IncomingMessageData:
    return IncomingMessageData(
        source="mock",
        channel_id="chan-1",
        message_id=message_id,
        text=text,
        sent_at=sent_at or datetime.now(UTC),
        received_at=datetime.now(UTC),
    )


def test_buy_alone_goes_to_waiting_for_details(session):
    service = SignalService(session, AuditService(session), signal_completion_timeout_seconds=120)
    result = service.ingest_message(_msg("BUY GOLD", "m1"))
    assert result.signal is not None
    assert result.signal.status == SignalStatus.WAITING_FOR_DETAILS
    assert result.became_ready is False


def test_buy_then_sl_becomes_ready(session):
    service = SignalService(session, AuditService(session), signal_completion_timeout_seconds=120)
    service.ingest_message(_msg("BUY GOLD", "m1"))
    result = service.ingest_message(_msg("4640 - 4637\nSL 4623", "m2"))
    assert result.signal is not None
    assert result.signal.status == SignalStatus.READY
    assert result.became_ready is True
    assert result.signal.channel_stop_loss == 4623


def test_buy_without_sl_expires_after_timeout(session):
    long_ago = datetime.now(UTC) - timedelta(seconds=200)
    service = SignalService(session, AuditService(session), signal_completion_timeout_seconds=120)
    result = service.ingest_message(_msg("BUY GOLD", "m1", sent_at=long_ago))
    signal_id = result.signal.id

    expired = service.expire_stale_signals()
    assert any(s.id == signal_id for s in expired)

    session.refresh(result.signal)
    assert result.signal.status == SignalStatus.EXPIRED


def test_duplicate_message_is_flagged_and_ignored(session):
    service = SignalService(session, AuditService(session), signal_completion_timeout_seconds=120)
    first = service.ingest_message(_msg("BUY GOLD", "m1"))
    second = service.ingest_message(_msg("BUY GOLD", "m1"))

    assert first.duplicate is False
    assert second.duplicate is True
    assert second.signal is None
