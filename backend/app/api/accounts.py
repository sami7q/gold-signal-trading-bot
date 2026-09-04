"""MT5 account CRUD, scoped to the authenticated user (multi-account
feature, 2026-09). Every lookup by id goes through
`AccountService.get_accessible`, which is the actual isolation boundary --
see its docstring. A shared account (MT5Account.is_shared, 2026-09-04
request) passes that boundary for every operator by design: the two of
them run one jointly-owned account and both need full control of it.

No "set live" endpoint as of the concurrent-execution "Phase 2" update --
which account is connected is now a fact about which OS process is
running (the hub, or a backend/scripts/worker.py instance), never
something an API call could toggle. `MT5AccountRead.is_live` reflects a
real heartbeat instead (see app/accounts/schemas.py)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.accounts.schemas import MT5AccountCreate, MT5AccountRead, MT5AccountUpdate
from app.accounts.service import AccountArchiveError, AccountService, DuplicateAccountError
from app.core.auth_deps import get_current_user
from app.database.models.user import User
from app.database.session import get_session

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _load_accessible_or_404(
    session: Session, user: User, account_id: int
):  # noqa: ANN201
    account = AccountService(session).get_accessible(user.id, account_id)  # type: ignore[arg-type]
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    return account


@router.get("", response_model=list[MT5AccountRead])
def list_accounts(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> list[MT5AccountRead]:
    accounts = AccountService(session).list_for_user(user.id)  # type: ignore[arg-type]
    return [MT5AccountRead.from_account(a) for a in accounts]


@router.post("", response_model=MT5AccountRead, status_code=status.HTTP_201_CREATED)
def create_account(
    payload: MT5AccountCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MT5AccountRead:
    try:
        account = AccountService(session).create(user.id, payload)  # type: ignore[arg-type]
    except DuplicateAccountError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return MT5AccountRead.from_account(account)


@router.get("/{account_id}", response_model=MT5AccountRead)
def get_account(
    account_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MT5AccountRead:
    account = _load_accessible_or_404(session, user, account_id)
    return MT5AccountRead.from_account(account)


@router.patch("/{account_id}", response_model=MT5AccountRead)
def update_account(
    account_id: int,
    payload: MT5AccountUpdate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MT5AccountRead:
    account = _load_accessible_or_404(session, user, account_id)
    try:
        account = AccountService(session).update(account, payload)
    except DuplicateAccountError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return MT5AccountRead.from_account(account)


@router.patch("/{account_id}/archive", response_model=MT5AccountRead)
def archive_account(
    account_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MT5AccountRead:
    account = _load_accessible_or_404(session, user, account_id)
    try:
        account = AccountService(session).archive(account)
    except AccountArchiveError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return MT5AccountRead.from_account(account)
