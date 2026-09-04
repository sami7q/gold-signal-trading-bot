"""Reusable FastAPI dependencies for the multi-account feature (2026-09).

`get_current_user`: any authenticated request. `get_current_account`: an
authenticated request that must also specify (via the `X-Account-Id`
header) one of *that same user's* MT5 accounts (or a shared one -- see
MT5Account.is_shared) -- 404, never 403, on a mismatch, so a request never
confirms whether an id belongs to someone else (see
AccountService.get_accessible's docstring for the same reasoning).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlmodel import Session

from app.accounts.service import AccountService
from app.auth.service import SESSION_COOKIE_NAME, AuthService
from app.database.models.mt5_account import MT5Account
from app.database.models.user import User
from app.database.session import get_session

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated."
)


def get_current_user(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> User:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token:
        raise _UNAUTHENTICATED
    user = AuthService(session).resolve_session(raw_token)
    if user is None:
        raise _UNAUTHENTICATED
    return user


def get_current_account(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
    x_account_id: Annotated[int | None, Header(alias="X-Account-Id")] = None,
) -> MT5Account:
    if x_account_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Account-Id header is required.",
        )
    account = AccountService(session).get_accessible(user.id, x_account_id)  # type: ignore[arg-type]
    if account is None:
        # Deliberately 404, not 403 -- never confirms whether x_account_id
        # belongs to another user or doesn't exist at all.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    return account
