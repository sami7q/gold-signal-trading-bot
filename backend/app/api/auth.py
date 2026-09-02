"""Login/logout/me (multi-account feature, 2026-09). Pre-seeded users only
-- there is deliberately no signup endpoint anywhere in this router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session

from app.accounts.schemas import MT5AccountRead
from app.accounts.service import AccountService
from app.auth.schemas import LoginRequest, MeResponse, UserRead
from app.auth.service import SESSION_COOKIE_NAME, AuthService
from app.core.auth_deps import get_current_user
from app.core.config import get_settings
from app.database.models.user import User
from app.database.session import get_session

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, raw_token: str, *, remember_me: bool) -> None:
    settings = get_settings()
    max_age = 60 * 60 * 24 * 30 if remember_me else 60 * 60 * 12
    response.set_cookie(
        SESSION_COOKIE_NAME,
        raw_token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,  # type: ignore[arg-type]
        path="/",
    )


def _me_response(session: Session, user: User) -> MeResponse:
    accounts = AccountService(session).list_for_user(user.id)  # type: ignore[arg-type]
    return MeResponse(
        user=UserRead.model_validate(user),
        accounts=[MT5AccountRead.from_account(a) for a in accounts],
    )


@router.post("/login", response_model=MeResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> MeResponse:
    result = AuthService(session).login(
        payload.username,
        payload.password,
        remember_me=payload.remember_me,
        user_agent=request.headers.get("user-agent"),
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="اسم المستخدم أو كلمة المرور غير صحيحة.",
        )
    user, raw_token = result
    _set_session_cookie(response, raw_token, remember_me=payload.remember_me)
    return _me_response(session, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token:
        AuthService(session).logout(raw_token)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


@router.get("/me", response_model=MeResponse)
def me(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MeResponse:
    return _me_response(session, user)
