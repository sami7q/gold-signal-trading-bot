"""Application configuration.

Everything that changes between environments (dev / test / VPS) lives here,
loaded from environment variables / `.env`. Real integrations default to
disabled — see PROJECT_SPEC.md section 26.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.constants import TradingMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    database_url: str = "sqlite:///./goldbot.db"

    trading_mode: TradingMode = TradingMode.SIMULATION
    auto_trading: bool = False

    paper_starting_balance: Decimal = Decimal("10000")
    paper_symbol: str = "XAUUSD"
    paper_point: Decimal = Decimal("0.01")
    paper_digits: int = 2
    paper_contract_size: Decimal = Decimal("100")
    paper_volume_min: Decimal = Decimal("0.01")
    paper_volume_max: Decimal = Decimal("50")
    paper_volume_step: Decimal = Decimal("0.01")
    paper_spread_points: int = 20

    signal_completion_timeout_seconds: int = 120

    telegram_enabled: bool = False
    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    telegram_session_path: str = ""
    telegram_allowed_channel_id: str = ""
    #: Real Telegram messages still flow through parser → state machine when
    #: this is true, but never reach the trading engine — see
    #: docs/09-telegram-integration.md. Defaults true: the first real
    #: connection to a channel must be observed before it's trusted to open
    #: even paper trades automatically (PROJECT_SPEC.md Phase 11 section 17).
    telegram_observe_only: bool = True
    #: How often (seconds) the connection-health monitor polls
    #: `client.is_connected()` to detect and log disconnects/reconnects.
    telegram_health_poll_seconds: int = 15

    mt5_enabled: bool = False
    mt5_account: str = ""
    mt5_password: str = ""
    mt5_server: str = ""
    mt5_terminal_path: str = ""
    mt5_symbol: str = "XAUUSD"

    #: How often (seconds) the background loop re-checks every OPEN trade
    #: against the broker. A real broker (MT5) closes positions on its own
    #: server the instant SL/TP is hit -- without this poll, that closure
    #: would only ever be discovered the next time the process restarts
    #: (reconciliation also runs once at startup, but that alone leaves a
    #: closed trade looking OPEN for however long the process keeps running).
    position_poll_seconds: int = 20

    dashboard_cors_origins: str = "http://localhost:3000"

    #: /docs, /redoc, /openapi.json. Enabled by default for local dev; set
    #: to false for a deployed instance so the full API surface/schema
    #: isn't publicly enumerable (PROJECT_SPEC.md section 27 — a deployed
    #: dashboard/API must not be wide open).
    api_docs_enabled: bool = True

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.dashboard_cors_origins.split(",") if o.strip()]

    @property
    def is_live(self) -> bool:
        return self.trading_mode == TradingMode.LIVE


@lru_cache
def get_settings() -> Settings:
    return Settings()
