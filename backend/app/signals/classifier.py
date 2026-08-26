"""Maps parsed message content to a `MessageRole` for `signal_messages.role`."""

from __future__ import annotations

from decimal import Decimal

from app.core.constants import MessageClassification, MessageRole, SignalKind


def determine_role(
    *,
    kind: SignalKind | None,
    stop_loss: Decimal | None,
    entry_min: Decimal | None,
    target_prices: list[Decimal],
    classification: MessageClassification,
) -> MessageRole:
    if stop_loss is not None:
        return MessageRole.STOP_LOSS
    if entry_min is not None:
        return MessageRole.DETAILS
    if kind is not None:
        return MessageRole.ENTRY
    if classification is MessageClassification.TARGET_UPDATE:
        return MessageRole.TARGETS if target_prices else MessageRole.UPDATE
    if classification is MessageClassification.RESULT_UPDATE:
        return MessageRole.RESULT
    return MessageRole.OTHER
