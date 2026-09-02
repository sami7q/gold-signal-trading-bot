from __future__ import annotations

from pydantic import BaseModel, Field

#: Reused verbatim from app/settings/schemas.py -- same operator-facing
#: "type this exact phrase" pattern for a rare, deliberate, high-stakes
#: action, so there's exactly one confirmation phrase to remember.
from app.settings.schemas import LIVE_CONFIRMATION_PHRASE

__all__ = ["LIVE_CONFIRMATION_PHRASE", "MT5AccountCreate", "MT5AccountRead", "MT5AccountUpdate"]


class MT5AccountRead(BaseModel):
    id: int
    label: str
    mt5_login: str
    mt5_server: str
    symbol: str
    is_live: bool
    is_active: bool
    #: Never the ciphertext or plaintext -- just whether one has been set.
    has_password: bool = True

    model_config = {"from_attributes": True}


class MT5AccountCreate(BaseModel):
    label: str = Field(min_length=1)
    mt5_login: str = Field(min_length=1)
    mt5_server: str = Field(min_length=1)
    mt5_password: str = Field(min_length=1)
    symbol: str = "XAUUSD"


class MT5AccountUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1)
    mt5_login: str | None = Field(default=None, min_length=1)
    mt5_server: str | None = Field(default=None, min_length=1)
    #: Omit to leave the stored password untouched; provide to rotate it.
    mt5_password: str | None = Field(default=None, min_length=1)
    symbol: str | None = None


class SetLiveRequest(BaseModel):
    #: Required to equal LIVE_CONFIRMATION_PHRASE -- flipping which account
    #: is live demotes whichever one (possibly the other user's) currently
    #: is, mid-trading, on a real VPS. Never a casual one-click action.
    confirm_phrase: str
