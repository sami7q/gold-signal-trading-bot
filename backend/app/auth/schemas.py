from __future__ import annotations

from pydantic import BaseModel

from app.accounts.schemas import MT5AccountRead


class LoginRequest(BaseModel):
    username: str
    password: str
    remember_me: bool = False


class UserRead(BaseModel):
    id: int
    username: str
    display_name: str

    model_config = {"from_attributes": True}


class MeResponse(BaseModel):
    user: UserRead
    accounts: list[MT5AccountRead]
