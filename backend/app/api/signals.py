from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.constants import MessageRole, OrderType, Side, SignalStatus
from app.core.decimals import CleanDecimal
from app.database.models.incoming_message import IncomingMessage
from app.database.models.signal import Signal
from app.database.models.signal_message import SignalMessage
from app.database.models.trade import Trade
from app.database.session import get_session

router = APIRouter(prefix="/api/signals", tags=["signals"])


class SignalSourceMessageRead(BaseModel):
    role: MessageRole
    raw_text: str
    normalized_text: str
    sent_at: datetime


class SignalRead(BaseModel):
    id: int
    symbol: str
    side: Side
    signal_type: OrderType
    entry_min: CleanDecimal | None
    entry_max: CleanDecimal | None
    channel_stop_loss: CleanDecimal | None
    status: SignalStatus
    source: str
    channel_id: str
    first_message_at: datetime
    completed_at: datetime | None
    expires_at: datetime | None
    rejection_reason: str | None
    created_at: datetime
    updated_at: datetime
    linked_trade_id: int | None = None
    #: Every raw Telegram (or mock) message that fed this signal — the
    #: primary debugging aid for the parser (PROJECT_SPEC.md section 15.4).
    messages: list[SignalSourceMessageRead] = []

    model_config = {"from_attributes": True}


def _load_trade_ids(session: Session, signal_ids: list[int]) -> dict[int, int]:
    trade_by_signal: dict[int, int] = {}
    if not signal_ids:
        return trade_by_signal
    for t in session.exec(select(Trade).where(Trade.signal_id.in_(signal_ids))):  # type: ignore[attr-defined]
        if t.id is not None:
            trade_by_signal[t.signal_id] = t.id
    return trade_by_signal


def _load_messages(
    session: Session, signal_ids: list[int]
) -> dict[int, list[SignalSourceMessageRead]]:
    messages_by_signal: dict[int, list[SignalSourceMessageRead]] = {}
    if not signal_ids:
        return messages_by_signal

    statement = (
        select(SignalMessage, IncomingMessage)
        .join(IncomingMessage, SignalMessage.message_id == IncomingMessage.id)  # type: ignore[arg-type]
        .where(SignalMessage.signal_id.in_(signal_ids))  # type: ignore[attr-defined]
        .order_by(IncomingMessage.sent_at)  # type: ignore[arg-type]
    )
    for link, message in session.exec(statement):
        messages_by_signal.setdefault(link.signal_id, []).append(
            SignalSourceMessageRead(
                role=link.role,
                raw_text=message.raw_text,
                normalized_text=message.normalized_text,
                sent_at=message.sent_at,
            )
        )
    return messages_by_signal


def _to_read(
    signal: Signal, *, trade_id: int | None, messages: list[SignalSourceMessageRead]
) -> SignalRead:
    return SignalRead.model_validate(
        {**signal.model_dump(), "linked_trade_id": trade_id, "messages": messages}
    )


@router.get("", response_model=list[SignalRead])
def list_signals(
    session: Annotated[Session, Depends(get_session)],
    status_filter: SignalStatus | None = None,
    limit: int = 200,
) -> list[SignalRead]:
    statement = select(Signal)
    if status_filter is not None:
        statement = statement.where(Signal.status == status_filter)
    statement = statement.order_by(Signal.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
    signals = list(session.exec(statement))
    signal_ids = [s.id for s in signals if s.id is not None]

    trade_by_signal = _load_trade_ids(session, signal_ids)
    messages_by_signal = _load_messages(session, signal_ids)

    return [
        _to_read(
            s,
            trade_id=trade_by_signal.get(s.id) if s.id is not None else None,
            messages=messages_by_signal.get(s.id, []) if s.id is not None else [],
        )
        for s in signals
    ]


@router.get("/{signal_id}", response_model=SignalRead)
def get_signal(signal_id: int, session: Annotated[Session, Depends(get_session)]) -> SignalRead:
    signal = session.get(Signal, signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    trade = session.exec(select(Trade).where(Trade.signal_id == signal_id)).first()
    messages = _load_messages(session, [signal_id]).get(signal_id, [])
    return _to_read(signal, trade_id=trade.id if trade else None, messages=messages)
