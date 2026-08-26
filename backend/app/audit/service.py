"""Central audit trail writer (PROJECT_SPEC.md section 13.6 / 21 / 23).

Every safety-relevant transition (signal lifecycle, trade lifecycle, settings
changes, service start/stop) must go through this module instead of a bare
logger call, so the dashboard's Logs page and the `audit_events` table always
agree with what actually happened.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from app.core.constants import AuditEventType, AuditSeverity
from app.core.logging import get_logger
from app.database.models.audit_event import AuditEvent

logger = get_logger(__name__)


class AuditService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def log(
        self,
        event_type: AuditEventType | str,
        message: str,
        *,
        severity: AuditSeverity = AuditSeverity.INFO,
        metadata: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> AuditEvent:
        event = AuditEvent(
            event_type=str(event_type),
            severity=severity,
            message=message,
            metadata_json=json.dumps(metadata or {}, default=str),
        )
        self._session.add(event)
        if commit:
            self._session.commit()
            self._session.refresh(event)

        log_fn = {
            AuditSeverity.INFO: logger.info,
            AuditSeverity.WARNING: logger.warning,
            AuditSeverity.ERROR: logger.error,
            AuditSeverity.CRITICAL: logger.critical,
        }[severity]
        log_fn(message, extra={"event_type": str(event_type), **(metadata or {})})

        return event

    def recent(self, limit: int = 50) -> list[AuditEvent]:
        statement = (
            select(AuditEvent)
            .order_by(AuditEvent.created_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return list(self._session.exec(statement))
