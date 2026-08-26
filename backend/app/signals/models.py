"""Domain value objects used by the parser / state machine / service.

These are plain dataclasses, deliberately separate from
`app.database.models` — the parser must be usable (and unit-testable)
without a database session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.core.constants import MessageClassification, MessageRole, SignalKind


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    raw_text: str
    normalized_text: str
    classification: MessageClassification
    role: MessageRole
    is_gold: bool
    kind: SignalKind | None = None
    entry_min: Decimal | None = None
    entry_max: Decimal | None = None
    stop_loss: Decimal | None = None
    target_prices: list[Decimal] = field(default_factory=list)

    @property
    def is_executable_kind(self) -> bool:
        from app.core.constants import EXECUTABLE_KINDS

        return self.kind is not None and self.kind in EXECUTABLE_KINDS


@dataclass(frozen=True, slots=True)
class IncomingMessageData:
    """Normalized message emitted by any `SignalSource` implementation."""

    source: str
    channel_id: str
    message_id: str
    text: str
    sent_at: datetime
    received_at: datetime
    reply_to_message_id: str | None = None
