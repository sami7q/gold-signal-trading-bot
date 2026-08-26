from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field

from app.core.constants import MessageClassification
from app.database.base import Base
from app.database.types import UTCDateTime


class IncomingMessage(Base, table=True):
    __tablename__ = "incoming_messages"
    __table_args__ = (
        UniqueConstraint("source", "channel_id", "message_id", name="uq_incoming_message_identity"),
    )

    id: int | None = Field(default=None, primary_key=True)

    source: str = Field(index=True)
    channel_id: str = Field(index=True)
    message_id: str = Field(index=True)
    reply_to_message_id: str | None = None

    raw_text: str
    normalized_text: str

    sent_at: datetime = Field(sa_type=UTCDateTime)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)

    classification: MessageClassification = Field(default=MessageClassification.IGNORED)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), sa_type=UTCDateTime)
