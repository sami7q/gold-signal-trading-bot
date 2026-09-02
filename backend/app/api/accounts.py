"""MT5 account CRUD, scoped to the authenticated user (multi-account
feature, 2026-09). Every lookup by id goes through
`AccountService.get_owned`, which is the actual isolation boundary --
see its docstring."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from app.accounts.schemas import (
    MT5AccountCreate,
    MT5AccountRead,
    MT5AccountUpdate,
    SetLiveRequest,
)
from app.accounts.service import AccountArchiveError, AccountService, SetLiveConfirmationError
from app.core.auth_deps import get_current_user
from app.database.models.user import User
from app.database.session import get_session

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def _load_owned_or_404(
    session: Session, user: User, account_id: int
):  # noqa: ANN201
    account = AccountService(session).get_owned(user.id, account_id)  # type: ignore[arg-type]
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    return account


@router.get("", response_model=list[MT5AccountRead])
def list_accounts(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> list[MT5AccountRead]:
    accounts = AccountService(session).list_for_user(user.id)  # type: ignore[arg-type]
    return [MT5AccountRead.model_validate(a) for a in accounts]


@router.post("", response_model=MT5AccountRead, status_code=status.HTTP_201_CREATED)
def create_account(
    payload: MT5AccountCreate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MT5AccountRead:
    account = AccountService(session).create(user.id, payload)  # type: ignore[arg-type]
    return MT5AccountRead.model_validate(account)


@router.get("/{account_id}", response_model=MT5AccountRead)
def get_account(
    account_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MT5AccountRead:
    account = _load_owned_or_404(session, user, account_id)
    return MT5AccountRead.model_validate(account)


@router.patch("/{account_id}", response_model=MT5AccountRead)
def update_account(
    account_id: int,
    payload: MT5AccountUpdate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MT5AccountRead:
    account = _load_owned_or_404(session, user, account_id)
    account = AccountService(session).update(account, payload)
    return MT5AccountRead.model_validate(account)


@router.patch("/{account_id}/archive", response_model=MT5AccountRead)
def archive_account(
    account_id: int,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MT5AccountRead:
    account = _load_owned_or_404(session, user, account_id)
    try:
        account = AccountService(session).archive(account)
    except AccountArchiveError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return MT5AccountRead.model_validate(account)


@router.post("/{account_id}/set-live", response_model=MT5AccountRead)
def set_live(
    account_id: int,
    payload: SetLiveRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MT5AccountRead:
    account = _load_owned_or_404(session, user, account_id)
    try:
        account = AccountService(session).set_live(account, confirm_phrase=payload.confirm_phrase)
    except SetLiveConfirmationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return MT5AccountRead.model_validate(account)
