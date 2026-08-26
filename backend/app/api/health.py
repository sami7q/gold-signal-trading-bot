from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.runtime import get_active_signal_source, get_broker
from app.integrations.broker.base import Broker
from app.integrations.signal_source.base import SignalSource

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {"status": "ok"}


@router.get("/signal-source")
async def signal_source_health(
    source: Annotated[SignalSource, Depends(get_active_signal_source)],
) -> dict:
    health = await source.health()
    return {
        "source": source.source_name,
        "healthy": health.healthy,
        "detail": health.detail,
        "enabled": health.enabled,
        "connected": health.connected,
        "authenticated": health.authenticated,
        "allowed_channel_configured": health.allowed_channel_configured,
        "last_message_at": health.last_message_at,
        "last_error": health.last_error,
    }


@router.get("/broker")
def broker_health(broker: Annotated[Broker, Depends(get_broker)]) -> dict:
    health = broker.health()
    return {"healthy": health.healthy, "detail": health.detail, "broker": broker.name}
