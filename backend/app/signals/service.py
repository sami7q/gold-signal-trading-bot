"""Signal ingestion pipeline: dedupe -> parse -> assemble -> state machine.

This module is deliberately trading-unaware: it never imports anything from
`app.trading`. The orchestration that decides "a signal just became READY,
now ask the trading engine" lives one layer up, in
`app.trading.service.TradingService`, so the dependency only ever points one
way (trading depends on signals, never the reverse) per PROJECT_SPEC.md
section 5.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlmodel import Session, select

from app.audit.service import AuditService
from app.core.constants import (
    CANONICAL_GOLD_SYMBOL,
    AuditEventType,
    AuditSeverity,
    MessageClassification,
    SignalStatus,
    kind_to_order_type,
    kind_to_side,
)
from app.database.models.incoming_message import IncomingMessage
from app.database.models.signal import Signal
from app.database.models.signal_message import SignalMessage
from app.database.models.trade import Trade
from app.signals.models import IncomingMessageData, ParsedMessage
from app.signals.parser import parse_message
from app.signals.state_machine import EXECUTION_ELIGIBLE_STATUSES, OPEN_STATUSES, SignalStateMachine


@dataclass(slots=True)
class IngestResult:
    incoming_message: IncomingMessage
    duplicate: bool = False
    signal: Signal | None = None
    became_ready: bool = False
    parsed: ParsedMessage | None = None
    #: True when this message carried details (entry/SL) that could not be
    #: uniquely attached to one signal — see `_handle_details`. No signal
    #: was changed and no trade can result from this message.
    ambiguous: bool = False


class SignalService:
    def __init__(
        self,
        session: Session,
        audit: AuditService | None = None,
        *,
        signal_completion_timeout_seconds: int = 120,
    ) -> None:
        self._session = session
        self._audit = audit or AuditService(session)
        self._default_timeout_seconds = signal_completion_timeout_seconds

    # -- duplicate protection -------------------------------------------------

    def _find_existing_message(self, data: IncomingMessageData) -> IncomingMessage | None:
        statement = select(IncomingMessage).where(
            IncomingMessage.source == data.source,
            IncomingMessage.channel_id == data.channel_id,
            IncomingMessage.message_id == data.message_id,
        )
        return self._session.exec(statement).first()

    # -- signal grouping --------------------------------------------------

    def _find_open_signals(self, source: str, channel_id: str, symbol: str) -> list[Signal]:
        """Every signal still waiting for details for this source/channel/symbol.

        Callers must never pick "the most recent" one when this returns more
        than one — see `_resolve_signal_for_details` and PROJECT_SPEC.md
        section 32, rule 13 ("any ambiguity that could create a trade must
        default to no trade").
        """
        statement = (
            select(Signal)
            .where(
                Signal.source == source,
                Signal.channel_id == channel_id,
                Signal.symbol == symbol,
                Signal.status.in_(OPEN_STATUSES),  # type: ignore[attr-defined]
            )
            .order_by(Signal.created_at.asc())  # type: ignore[attr-defined]
        )
        return list(self._session.exec(statement))

    def _resolve_via_reply(
        self, data: IncomingMessageData, candidates: list[Signal]
    ) -> Signal | None:
        """Resolve which of `candidates` this message belongs to using
        `reply_to_message_id`. Returns None (never guesses) unless the reply
        points at a message linked to exactly one of the candidates."""
        if not data.reply_to_message_id:
            return None

        statement = select(IncomingMessage).where(
            IncomingMessage.source == data.source,
            IncomingMessage.channel_id == data.channel_id,
            IncomingMessage.message_id == data.reply_to_message_id,
        )
        replied_to = self._session.exec(statement).first()
        if replied_to is None or replied_to.id is None:
            return None  # invalid/unknown reply reference

        candidate_ids = {s.id for s in candidates}
        link_statement = select(SignalMessage).where(SignalMessage.message_id == replied_to.id)
        linked_signal_ids = {link.signal_id for link in self._session.exec(link_statement)}
        resolved_ids = linked_signal_ids & candidate_ids
        if len(resolved_ids) != 1:
            return None

        resolved_id = next(iter(resolved_ids))
        return next(s for s in candidates if s.id == resolved_id)

    def _resolve_signal_for_details(
        self, data: IncomingMessageData, candidates: list[Signal]
    ) -> Signal | None:
        """Exactly one candidate -> use it. Otherwise, only a unique
        reply-based resolution counts; anything else is ambiguous (None)."""
        if len(candidates) == 1:
            return candidates[0]
        return self._resolve_via_reply(data, candidates)

    def _find_latest_signal(self, source: str, channel_id: str, symbol: str) -> Signal | None:
        statement = (
            select(Signal)
            .where(
                Signal.source == source, Signal.channel_id == channel_id, Signal.symbol == symbol
            )
            .order_by(Signal.created_at.desc())  # type: ignore[attr-defined]
        )
        return self._session.exec(statement).first()

    def _link_message(self, signal: Signal, message: IncomingMessage, role) -> None:
        link = SignalMessage(signal_id=signal.id, message_id=message.id, role=role)
        self._session.add(link)
        self._session.commit()

    # -- ingestion ----------------------------------------------------------

    def ingest_message(self, data: IncomingMessageData) -> IngestResult:
        existing = self._find_existing_message(data)
        if existing is not None:
            self._audit.log(
                AuditEventType.SIGNAL_DUPLICATE,
                f"Duplicate message ignored: {data.source}/{data.channel_id}/{data.message_id}",
                severity=AuditSeverity.WARNING,
                metadata={"message_id": data.message_id, "channel_id": data.channel_id},
            )
            return IngestResult(incoming_message=existing, duplicate=True)

        parsed = parse_message(data.text)

        db_message = IncomingMessage(
            source=data.source,
            channel_id=data.channel_id,
            message_id=data.message_id,
            reply_to_message_id=data.reply_to_message_id,
            raw_text=data.text,
            normalized_text=parsed.normalized_text,
            sent_at=data.sent_at,
            received_at=data.received_at,
            classification=parsed.classification,
        )
        self._session.add(db_message)
        self._session.commit()
        self._session.refresh(db_message)

        self._audit.log(
            AuditEventType.SIGNAL_RECEIVED,
            f"Message received: {parsed.classification.value}",
            metadata={"message_id": db_message.id, "classification": parsed.classification.value},
        )

        result = IngestResult(incoming_message=db_message, parsed=parsed)

        if parsed.classification == MessageClassification.ENTRY_SIGNAL and parsed.kind is not None:
            self._handle_entry(data, db_message, parsed, result)
        elif parsed.classification == MessageClassification.SIGNAL_DETAILS:
            self._handle_details(data, db_message, parsed, result)
        elif parsed.classification in (
            MessageClassification.TARGET_UPDATE,
            MessageClassification.RESULT_UPDATE,
        ):
            self._handle_informational(data, db_message, parsed)
        # IGNORED messages are stored (for the Signals debugging page) but
        # otherwise produce no further action.

        return result

    def _handle_entry(
        self,
        data: IncomingMessageData,
        db_message: IncomingMessage,
        parsed: ParsedMessage,
        result: IngestResult,
    ) -> None:
        assert parsed.kind is not None
        has_sl = parsed.stop_loss is not None
        status = SignalStateMachine.initial_status(has_stop_loss=has_sl)
        now = datetime.now(UTC)

        signal = Signal(
            symbol=CANONICAL_GOLD_SYMBOL,
            side=kind_to_side(parsed.kind),
            signal_type=kind_to_order_type(parsed.kind),
            entry_min=parsed.entry_min,
            entry_max=parsed.entry_max,
            channel_stop_loss=parsed.stop_loss,
            status=status,
            source=data.source,
            channel_id=data.channel_id,
            first_message_at=data.sent_at,
            completed_at=now if status == SignalStatus.READY else None,
            expires_at=(
                None
                if status == SignalStatus.READY
                else data.sent_at + timedelta(seconds=self._default_timeout_seconds)
            ),
        )
        self._session.add(signal)
        self._session.commit()
        self._session.refresh(signal)

        self._link_message(signal, db_message, parsed.role)

        if status == SignalStatus.READY:
            self._audit.log(
                AuditEventType.SIGNAL_READY,
                f"Signal {signal.id} ready (SL included in entry message)",
                metadata={"signal_id": signal.id},
            )
            result.became_ready = True

        result.signal = signal

    def _handle_details(
        self,
        data: IncomingMessageData,
        db_message: IncomingMessage,
        parsed: ParsedMessage,
        result: IngestResult,
    ) -> None:
        candidates = self._find_open_signals(data.source, data.channel_id, CANONICAL_GOLD_SYMBOL)
        if not candidates:
            # No open signal to attach details to; keep the message stored
            # (already persisted above) but there is nothing else to do.
            return

        signal = self._resolve_signal_for_details(data, candidates)
        if signal is None:
            candidate_ids = [s.id for s in candidates]
            self._audit.log(
                AuditEventType.AMBIGUOUS_SIGNAL_DETAILS,
                f"Cannot uniquely attach signal details: {len(candidates)} signals are "
                f"waiting for {data.source}/{data.channel_id} and reply_to_message_id "
                "did not resolve one uniquely. Left unattached — no trade can result "
                "from this message.",
                severity=AuditSeverity.WARNING,
                metadata={
                    "candidate_signal_ids": candidate_ids,
                    "message_id": db_message.id,
                    "reply_to_message_id": data.reply_to_message_id,
                },
            )
            result.ambiguous = True
            return

        if parsed.entry_min is not None:
            signal.entry_min = parsed.entry_min
            signal.entry_max = parsed.entry_max
        if parsed.stop_loss is not None:
            signal.channel_stop_loss = parsed.stop_loss

        previous_status = signal.status
        new_status = SignalStateMachine.apply_details(
            previous_status, has_stop_loss=signal.channel_stop_loss is not None
        )
        signal.status = new_status
        signal.updated_at = datetime.now(UTC)
        if new_status == SignalStatus.READY and previous_status != SignalStatus.READY:
            signal.completed_at = datetime.now(UTC)

        self._session.add(signal)
        self._session.commit()
        self._session.refresh(signal)

        self._link_message(signal, db_message, parsed.role)

        result.signal = signal
        if new_status == SignalStatus.READY and previous_status != SignalStatus.READY:
            self._audit.log(
                AuditEventType.SIGNAL_READY,
                f"Signal {signal.id} ready",
                metadata={"signal_id": signal.id},
            )
            result.became_ready = True

    def _handle_informational(
        self, data: IncomingMessageData, db_message: IncomingMessage, parsed: ParsedMessage
    ) -> None:
        # Informational messages never drive execution, so a missed link is
        # low-stakes — but they must still never be *guessed* onto the wrong
        # signal when more than one is currently waiting.
        open_candidates = self._find_open_signals(
            data.source, data.channel_id, CANONICAL_GOLD_SYMBOL
        )
        signal: Signal | None
        if len(open_candidates) == 1:
            signal = open_candidates[0]
        elif len(open_candidates) == 0:
            signal = self._find_latest_signal(data.source, data.channel_id, CANONICAL_GOLD_SYMBOL)
        else:
            signal = None  # multiple signals waiting -- do not guess which one this belongs to

        if signal is not None:
            self._link_message(signal, db_message, parsed.role)

    # -- execution eligibility (concurrent-execution "Phase 2" update) --------

    def find_unexecuted_for_account(self, account_id: int) -> list[Signal]:
        """Every execution-eligible signal (see `EXECUTION_ELIGIBLE_STATUSES`)
        for which `account_id` doesn't have a `Trade` row yet -- what
        `backend/scripts/worker.py` (and, in principle, the hub too) polls
        instead of `WHERE Signal.status == READY`.

        `Signal.status` is a single shared column (Signal itself is
        account-agnostic, one row per channel message, by design) -- the
        moment *any* account executes it, `status` flips away from READY
        forever, so a second account's process filtering on that column
        would never see it again even though *it* hasn't executed yet.
        `Trade` rows, by contrast, are already properly account-scoped
        (`account_id` + the `account_id:signal_id:version` execution_key,
        see TradingEngine.build_execution_key) -- "does *this* account have
        a Trade for this signal" is the only question that's actually
        correct to ask per-account.
        """
        executed_signal_ids = select(Trade.signal_id).where(Trade.account_id == account_id)
        statement = (
            select(Signal)
            .where(
                Signal.status.in_(EXECUTION_ELIGIBLE_STATUSES),  # type: ignore[attr-defined]
                Signal.id.not_in(executed_signal_ids),  # type: ignore[union-attr]
            )
            .order_by(Signal.created_at.asc())  # type: ignore[attr-defined]
        )
        return list(self._session.exec(statement))

    # -- expiry sweep ---------------------------------------------------------

    def _is_stale(self, signal: Signal, *, now: datetime) -> bool:
        timeout = signal.expires_at
        if timeout is not None:
            return now >= timeout
        return SignalStateMachine.is_expired(
            signal.status,
            first_message_at=signal.first_message_at,
            timeout_seconds=self._default_timeout_seconds,
            now=now,
        )

    def find_stale_signals(self, *, now: datetime | None = None) -> list[Signal]:
        """Read-only: which open (RECEIVED/CLASSIFIED/WAITING_FOR_DETAILS)
        signals have passed their completion timeout, without changing
        anything. `TradingService.sweep_expired_signals` uses this to decide,
        per signal, whether to expire it outright or apply an auto Stop Loss
        fallback -- a decision that needs settings/broker access this
        trading-unaware module deliberately does not have."""
        now = now or datetime.now(UTC)
        statement = select(Signal).where(Signal.status.in_(OPEN_STATUSES))  # type: ignore[attr-defined]
        candidates = self._session.exec(statement)
        return [signal for signal in candidates if self._is_stale(signal, now=now)]

    def expire_one(self, signal: Signal, *, now: datetime | None = None) -> Signal:
        now = now or datetime.now(UTC)
        signal.status = SignalStateMachine.expire(signal.status)
        signal.updated_at = now
        self._session.add(signal)
        self._session.commit()
        self._session.refresh(signal)
        self._audit.log(
            AuditEventType.SIGNAL_EXPIRED,
            f"Signal {signal.id} expired waiting for Stop Loss",
            severity=AuditSeverity.WARNING,
            metadata={"signal_id": signal.id},
        )
        return signal

    def expire_stale_signals(self, *, now: datetime | None = None) -> list[Signal]:
        now = now or datetime.now(UTC)
        return [self.expire_one(signal, now=now) for signal in self.find_stale_signals(now=now)]

    def apply_stop_loss_and_ready(
        self, signal: Signal, *, stop_loss: Decimal, now: datetime | None = None
    ) -> Signal:
        """Backs the auto Stop Loss fallback (TradingService): attaches a
        broker-computed Stop Loss price to a signal that timed out with none,
        and advances it to READY exactly as if the channel had sent that
        price itself. Never called for a signal that already has a Stop
        Loss -- `TradingService` only reaches this via `find_stale_signals`,
        which by construction only returns still-open signals."""
        now = now or datetime.now(UTC)
        signal.channel_stop_loss = stop_loss
        signal.status = SignalStateMachine.apply_details(signal.status, has_stop_loss=True)
        signal.updated_at = now
        if signal.status == SignalStatus.READY and signal.completed_at is None:
            signal.completed_at = now
        self._session.add(signal)
        self._session.commit()
        self._session.refresh(signal)
        return signal
