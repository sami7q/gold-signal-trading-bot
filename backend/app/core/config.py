"""Application configuration.

Everything that changes between environments (dev / test / VPS) lives here,
loaded from environment variables / `.env`. Real integrations default to
disabled — see PROJECT_SPEC.md section 26.
"""

from __future__ import annotations

import os
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
    #: How far the actual fill may drift from the price this bot quoted a
    #: moment earlier before the broker refuses the order (MT5's own
    #: `deviation`, which this is converted into broker points for --
    #: expressed in pips here so it means the same thing to an operator as
    #: every other pip setting in the app). 2026-09-05 operator request:
    #: a market that moved a dollar between reading the quote and the
    #: server filling it should still be traded, not lost to a requote --
    #: 10 pips is $1.00 on a 2-digit-Gold broker. Set to 0 for the
    #: pre-2026-09-05 behavior (MT5's default: no drift tolerated at all,
    #: which silently turned fast markets into FAILED trades).
    mt5_max_slippage_pips: Decimal = Decimal("10")

    #: Fernet key (base64, `Fernet.generate_key()`) used to encrypt every
    #: MT5Account row's password at rest -- see app/core/credentials.py.
    #: Generated once, never committed. The live MT5Broker connection above
    #: still reads mt5_account/mt5_password/mt5_server directly (unchanged,
    #: Phase 1 decision to avoid touching the code path already trading on
    #: the VPS) -- this key only guards the multi-account feature's DB rows.
    account_credentials_encryption_key: str = ""

    #: How often (seconds) the background loop re-checks every OPEN trade
    #: against the broker. A real broker (MT5) closes positions on its own
    #: server the instant SL/TP is hit -- without this poll, that closure
    #: would only ever be discovered the next time the process restarts
    #: (reconciliation also runs once at startup, but that alone leaves a
    #: closed trade looking OPEN for however long the process keeps running).
    position_poll_seconds: int = 20

    #: How often (seconds) the background loop checks for signals stuck
    #: waiting for a channel Stop Loss past their completion timeout
    #: (TradingSettings.signal_completion_timeout_seconds). Kept short --
    #: unlike position polling, this directly gates execution latency: a
    #: signal that qualifies for the fallback Stop Loss (or expires, if
    #: the fallback is disabled) sits idle for up to this long past its
    #: timeout before the sweep notices.
    signal_expiry_poll_seconds: int = 5

    dashboard_cors_origins: str = "http://localhost:3000"

    #: Session cookie flags (multi-account feature, 2026-09). Production
    #: default is correct for the real deployment (Vercel dashboard <->
    #: Cloudflare Tunnel backend, both HTTPS but different domains, which
    #: is a cross-site request -- SameSite=None + Secure=True is required
    #: for the browser to send the cookie at all). Override both to False
    #: only for plain-http local dev if needed.
    cookie_secure: bool = True
    cookie_samesite: str = "none"

    #: /docs, /redoc, /openapi.json. Enabled by default for local dev; set
    #: to false for a deployed instance so the full API surface/schema
    #: isn't publicly enumerable (PROJECT_SPEC.md section 27 — a deployed
    #: dashboard/API must not be wide open).
    api_docs_enabled: bool = True

    #: How often (seconds) a `backend/scripts/worker.py` process polls for
    #: unexecuted signals + runs its own account-scoped reconciliation +
    #: writes its heartbeat (concurrent-execution "Phase 2" update). Not
    #: used by the hub, which has its own longer-established poll settings
    #: above (position_poll_seconds/signal_expiry_poll_seconds).
    worker_poll_seconds: int = 10

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.dashboard_cors_origins.split(",") if o.strip()]

    @property
    def is_live(self) -> bool:
        return self.trading_mode == TradingMode.LIVE


@lru_cache
def get_settings() -> Settings:
    """A `backend/scripts/worker.py` process reads a *different* `.env`
    file than the hub (its own MT5 credentials for its own account, no
    Telegram) -- point it there via the `GOLDBOT_ENV_FILE` environment
    variable rather than the class default (`../.env`, still what the hub
    and every test use unchanged). Concurrent-execution "Phase 2" update.
    """
    env_file_override = os.environ.get("GOLDBOT_ENV_FILE")
    if env_file_override:
        return Settings(_env_file=env_file_override)  # type: ignore[call-arg]
    return Settings()
