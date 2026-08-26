from __future__ import annotations

from sqlmodel import Field

from app.core.constants import MessageRole
from app.database.base import Base


class SignalMessage(Base, table=True):
    __tablename__ = "signal_messages"

    id: int | None = Field(default=None, primary_key=True)
    signal_id: int = Field(foreign_key="signals.id", index=True)
    message_id: int = Field(foreign_key="incoming_messages.id", index=True)
    role: MessageRole
