"""Read-only endpoints over the historical channel archive
(`scripts/build_channel_archive.py` populates it; this module never writes
to it -- building/rebuilding the archive is a deliberate, explicit operator
action, not something the API triggers)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.archive.calculations import ArchiveSummary, compute_archive_summary
from app.core.auth_deps import get_current_user
from app.core.decimals import CleanDecimal
from app.database.models.archived_signal import ArchivedSignal
from app.database.models.user import User
from app.database.session import get_session

router = APIRouter(prefix="/api/archive", tags=["archive"])


class ArchivedSignalRead(BaseModel):
    id: int
    channel_id: str
    message_id: str
    symbol: str
    side: str
    signal_type: str
    entry_min: CleanDecimal | None
    entry_max: CleanDecimal | None
    channel_stop_loss: CleanDecimal | None
    raw_entry_text: str
    raw_sl_text: str | None
    signal_posted_at: datetime
    sl_posted_at: datetime | None
    classification: str
    simulated_entry_price: CleanDecimal | None
    simulated_take_profit_price: CleanDecimal | None
    outcome: str | None
    outcome_price: CleanDecimal | None
    outcome_at: datetime | None
    profit_estimate: CleanDecimal | None

    model_config = {"from_attributes": True}


def _all_rows(session: Session) -> list[ArchivedSignal]:
    return list(session.exec(select(ArchivedSignal)))


@router.get("/summary", response_model=ArchiveSummary)
def get_archive_summary(
    session: Annotated[Session, Depends(get_session)],
    _user: Annotated[User, Depends(get_current_user)],
) -> ArchiveSummary:
    return compute_archive_summary(_all_rows(session))


@router.get("", response_model=list[ArchivedSignalRead])
def list_archived_signals(
    session: Annotated[Session, Depends(get_session)],
    _user: Annotated[User, Depends(get_current_user)],
    classification: str | None = None,
    outcome: str | None = None,
    limit: int = 500,
) -> list[ArchivedSignal]:
    statement = select(ArchivedSignal)
    if classification is not None:
        statement = statement.where(ArchivedSignal.classification == classification)
    if outcome is not None:
        statement = statement.where(ArchivedSignal.outcome == outcome)
    statement = statement.order_by(ArchivedSignal.signal_posted_at.desc()).limit(limit)  # type: ignore[attr-defined]
    return list(session.exec(statement))
