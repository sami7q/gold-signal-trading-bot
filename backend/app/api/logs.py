from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session

from app.audit.service import AuditService
from app.core.constants import AuditSeverity
from app.database.models.audit_event import AuditEvent
from app.database.session import get_session

router = APIRouter(prefix="/api/logs", tags=["logs"])


class AuditEventRead(BaseModel):
    id: int
    event_type: str
    severity: AuditSeverity
    message: str
    metadata_json: str
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[AuditEventRead])
def list_logs(
    session: Annotated[Session, Depends(get_session)], limit: int = 100
) -> list[AuditEvent]:
    return AuditService(session).recent(limit=limit)
