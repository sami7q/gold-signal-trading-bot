from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field

from app.core.constants import AuditSeverity
from app.database.base import Base
from app.database.types import UTCDateTime


class AuditEvent(Base, table=True):
    __tablename__ = "audit_events"

    id: int | None = Field(default=None, primary_key=True)

    event_type: str = Field(index=True)
    severity: AuditSeverity = Field(default=AuditSeverity.INFO, index=True)
    message: str

    #: Stored as a JSON string (portable across SQLite and PostgreSQL without
    #: requiring the JSONB extension type at the schema level).
    metadata_json: str = "{}"

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), index=True, sa_type=UTCDateTime
    )
