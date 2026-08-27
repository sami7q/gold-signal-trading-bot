from __future__ import annotations

from fastapi import APIRouter

from app.api import archive, dashboard, health, logs, settings, signals, simulation, trades

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(dashboard.router)
api_router.include_router(trades.router)
api_router.include_router(signals.router)
api_router.include_router(settings.router)
api_router.include_router(simulation.router)
api_router.include_router(logs.router)
api_router.include_router(archive.router)
