from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.core.auth_deps import get_current_account
from app.core.constants import CANONICAL_GOLD_SYMBOL, Side
from app.core.decimals import CleanDecimal
from app.core.runtime import get_broker
from app.database.models.mt5_account import MT5Account
from app.database.session import get_session
from app.integrations.broker.base import Broker
from app.settings.schemas import TradingSettingsRead, TradingSettingsUpdate
from app.settings.service import LiveModeConfirmationError, SettingsService
from app.trading import pips

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=TradingSettingsRead)
def get_settings_endpoint(
    session: Annotated[Session, Depends(get_session)],
    account: Annotated[MT5Account, Depends(get_current_account)],
) -> TradingSettingsRead:
    settings = SettingsService(session).get_for_account(account.id)  # type: ignore[arg-type]
    return TradingSettingsRead.model_validate(settings)


@router.patch("", response_model=TradingSettingsRead)
def update_settings_endpoint(
    patch: TradingSettingsUpdate,
    session: Annotated[Session, Depends(get_session)],
    account: Annotated[MT5Account, Depends(get_current_account)],
) -> TradingSettingsRead:
    try:
        settings = SettingsService(session).update(account.id, patch)  # type: ignore[arg-type]
    except LiveModeConfirmationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TradingSettingsRead.model_validate(settings)


class PipPreview(BaseModel):
    symbol: str
    broker: str
    broker_healthy: bool
    digits: int | None
    point: CleanDecimal | None
    pip_size: CleanDecimal | None
    target_pips: CleanDecimal
    price_distance: CleanDecimal | None
    example_entry: CleanDecimal | None
    calculated_take_profit: CleanDecimal | None


@router.get("/pip-preview", response_model=PipPreview)
def get_pip_preview(
    session: Annotated[Session, Depends(get_session)],
    broker: Annotated[Broker, Depends(get_broker)],
    account: Annotated[MT5Account, Depends(get_current_account)],
    target_pips: Decimal | None = None,
) -> PipPreview:
    """Read-only preview of what the configured (or a supplied) pip target
    actually means in price terms for the connected broker's Gold symbol.

    Deliberately reuses `app.trading.pips` — the exact module the trading
    engine itself calls before opening a trade — instead of recomputing the
    math in the dashboard, so the preview can never drift from what would
    actually happen (PROJECT_SPEC.md section 10).
    """
    settings = SettingsService(session).get_for_account(account.id)  # type: ignore[arg-type]
    pips_value = target_pips if target_pips is not None else settings.target_pips
    if pips_value <= 0:
        raise HTTPException(status_code=422, detail="target_pips must be greater than zero")

    try:
        health = broker.health()
    except Exception:  # noqa: BLE001
        health = None
    healthy = bool(health and health.healthy)

    symbol_info = None
    if healthy:
        try:
            symbol_info = broker.get_symbol(CANONICAL_GOLD_SYMBOL)
        except Exception:  # noqa: BLE001
            symbol_info = None

    if symbol_info is None:
        return PipPreview(
            symbol=CANONICAL_GOLD_SYMBOL,
            broker=broker.name,
            broker_healthy=healthy,
            digits=None,
            point=None,
            pip_size=None,
            target_pips=pips_value,
            price_distance=None,
            example_entry=None,
            calculated_take_profit=None,
        )

    pip_size = pips.pip_size(symbol_info)
    price_distance = pips.pip_distance_to_price(symbol_info, pips_value)

    example_entry: Decimal | None = None
    calculated_tp: Decimal | None = None
    try:
        quote = broker.get_quote(CANONICAL_GOLD_SYMBOL)
        example_entry = quote.ask
        calculated_tp = pips.compute_take_profit(
            entry_price=quote.ask, pips=pips_value, symbol_info=symbol_info, side=Side.BUY
        )
    except Exception:  # noqa: BLE001
        pass

    return PipPreview(
        symbol=symbol_info.symbol,
        broker=broker.name,
        broker_healthy=healthy,
        digits=symbol_info.digits,
        point=symbol_info.point,
        pip_size=pip_size,
        target_pips=pips_value,
        price_distance=price_distance,
        example_entry=example_entry,
        calculated_take_profit=calculated_tp,
    )
