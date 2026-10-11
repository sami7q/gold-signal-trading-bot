"""All settings, validated once at start-up (botup.md §9). Safe defaults: nothing is ever traded until
COPY_ENABLED=true and COPY_OBSERVE_ONLY=false are both set on purpose.

API keys are never read from here -- they live in the OS keyring (`loco-copy set-key`).
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from loco_copy.constants import LIVE_CONFIRM_PHRASE

#: The project folder (the one holding pyproject.toml), independent of the current working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCO_ADDRESS = "0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # `MAX_NOTIONAL_PER_COIN=` means "not set", not an invalid number
        extra="ignore",
    )

    leader_address: str = LOCO_ADDRESS

    copy_enabled: bool = False
    copy_observe_only: bool = True

    broker: Literal["paper", "bybit"] = "paper"
    broker_env: Literal["demo", "live"] = "demo"
    live_confirm: str = ""

    risk_per_trade: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)
    leverage: int = Field(default=20, ge=1, le=100)
    take_profit_roi: Decimal = Field(default=Decimal("0.50"), gt=0)
    protective_sl_pct: Decimal | None = None
    follow_adds: bool = False
    follow_reduces: bool = True

    max_open_positions: int = Field(default=10, ge=1)
    max_notional_per_coin: Decimal | None = Field(default=None, gt=0)
    max_entry_drift: Decimal = Field(default=Decimal("0.01"), gt=0)
    max_signal_age_ms: int = Field(default=10_000, gt=0)
    #: Market-order slippage protection sent to the exchange (fraction; 0.005 = 0.5 %).
    max_order_slippage: Decimal = Field(default=Decimal("0.005"), gt=0)

    allow_long: bool = True
    allow_short: bool = True
    coin_allowlist: str = ""  # comma-separated; empty = every coin allowed
    coin_blocklist: str = ""

    daily_loss_limit_pct: Decimal = Field(default=Decimal("0.15"), gt=0, lt=1)
    leader_dd_breaker_pct: Decimal = Field(default=Decimal("0.20"), gt=0, lt=1)
    leader_snapshot_seconds: int = Field(default=20, ge=5)
    reconcile_seconds: int = Field(default=30, ge=5)
    #: Loco flat but our copy still open for longer than this -> auto-close (the one automatic correction).
    missed_exit_seconds: int = Field(default=60, ge=10)
    #: No WS frame (pongs count) for longer than this -> latency breaker pauses new opens.
    stale_feed_seconds: int = Field(default=60, ge=10)

    paper_start_equity: Decimal = Field(default=Decimal("10000"), gt=0)
    paper_fee_rate: Decimal = Field(default=Decimal("0.00055"), ge=0)  # Bybit taker
    paper_slippage_bps: Decimal = Field(default=Decimal("2"), ge=0)
    #: Isolated liquidation happens at a (1/leverage - this) adverse move -- ~4.5 % at 20x (same as the research).
    maintenance_margin_rate: Decimal = Field(default=Decimal("0.005"), ge=0)

    alert_telegram_token: str = ""
    alert_telegram_chat_id: str = ""

    #: Dashboard API (`loco-copy api`): localhost only -- the Cloudflare tunnel is the only way in.
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    dashboard_origins: str = "https://boot-tau-indol.vercel.app,http://localhost:3000"
    #: The dashboard (Vercel) and the API (tunnel) are different sites: the session cookie must be
    #: SameSite=None + Secure for the browser to send it at all.
    api_cookie_secure: bool = True
    session_days: int = Field(default=30, ge=1)

    data_dir: Path = PROJECT_ROOT / "data"
    hl_info_url: str = "https://api.hyperliquid.xyz/info"
    hl_ws_url: str = "wss://api.hyperliquid.xyz/ws"

    @field_validator("leader_address")
    @classmethod
    def _address(cls, v: str) -> str:
        v = v.strip().lower()
        if not (v.startswith("0x") and len(v) == 42):
            raise ValueError("LEADER_ADDRESS must be a 0x-prefixed 20-byte address")
        return v

    @model_validator(mode="after")
    def _safety(self) -> Settings:
        if self.follow_adds:
            raise ValueError("FOLLOW_ADDS=true is not supported: adds are ignored by design (owner decision).")
        if self.protective_sl_pct is not None:
            raise ValueError("PROTECTIVE_SL_PCT is not implemented yet -- leave it empty (exit with Loco).")
        if self.broker == "bybit" and self.broker_env == "live" and self.live_confirm != LIVE_CONFIRM_PHRASE:
            raise ValueError(f'BROKER_ENV=live requires LIVE_CONFIRM="{LIVE_CONFIRM_PHRASE}" -- refusing to start.')
        if Decimal(1) / self.leverage <= self.maintenance_margin_rate:
            raise ValueError("LEVERAGE too high for MAINTENANCE_MARGIN_RATE: liquidation would be immediate.")
        return self

    @property
    def allowlist(self) -> frozenset[str]:
        return frozenset(c.strip() for c in self.coin_allowlist.split(",") if c.strip())

    @property
    def blocklist(self) -> frozenset[str]:
        return frozenset(c.strip() for c in self.coin_blocklist.split(",") if c.strip())

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.dashboard_origins.split(",") if o.strip()]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "loco.db"

    @property
    def stop_file(self) -> Path:
        return self.data_dir / "STOP"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def keyring_service(self) -> str:
        return f"loco-copy-bybit-{self.broker_env}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def with_mode_override(base: Settings, override: dict[str, Any] | None) -> Settings:
    """The dashboard can switch watch / observe / execute at runtime (stored in the DB, read by the bot every
    few seconds). It can never change the broker or go live -- those stay in .env behind LIVE_CONFIRM."""
    if not override:
        return base
    return base.model_copy(update={"copy_enabled": bool(override.get("copy_enabled")),
                                   "copy_observe_only": bool(override.get("observe_only", True))})
