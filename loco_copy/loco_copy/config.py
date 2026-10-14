"""All settings, validated once at start-up (botup.md §9). Safe defaults: nothing is ever traded until
COPY_ENABLED=true and COPY_OBSERVE_ONLY=false are both set on purpose.

API keys are never read from here -- they live in the OS keyring (`loco-copy set-key`).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from dotenv import dotenv_values
from pydantic import AliasChoices, Field, PrivateAttr, TypeAdapter, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from loco_copy.constants import LIVE_CONFIRM_PHRASE

#: The project folder (the one holding pyproject.toml), independent of the current working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCO_ADDRESS = "0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1"
#: botup.md §V6 (owner, 2026-10-14): the v5 trio 8bae + 8fc7 + 69b0, each sized on the whole account (weight 1).
DEFAULT_PORTFOLIO_WALLETS = ("8bae:0x8bae3527e5a33fa0cf184f37bc112d071463ab6d:1,"
                             "8fc7:0x8fc7c0442e582bca195978c5a4fdec2e7c5bb0f7:1,"
                             "69b0:0x69b05701f8175c276ecd0138387a197948e240bb:1")
#: botup.md §V10: wallets that only the extra simulator groups copy (the research section's best mixes)
DEFAULT_PORTFOLIO_EXTRA_WALLETS = ("2ac4:0x2ac4880624dae9e823874d65e43bfeba97fc61b5:1,"
                                   "2396:0x2396bc99feedc162af809314257571bb7ff07a36:1")
#: name=label+label+...:realistic year multiple:half-edge multiple (Monte Carlo median) -- research_2026-10-14
DEFAULT_PORTFOLIO_GROUPS = "mix2ac4=8bae+8fc7+69b0+2ac4:29.01:5.79;mix2396=8bae+8fc7+69b0+2396:29.24:5.69"
#: The v2 single leader. Its lane keeps every v2 name: state keys, `loco:` client ids, keyring service.
DEFAULT_LABEL = "loco"
LABEL_RE = re.compile(r"^[a-z0-9]{2,12}$")
OVERRIDE_KEY_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*?)__([A-Za-z0-9]+)$")

#: Other names a setting is known by in the docs (botup.md v5: COPY_RATIO_K).
SETTING_ALIASES = {"copy_ratio_k": "size_k"}

#: Settings that exist once for the whole process and can never be overridden per lane (botup.md v3 §4).
GLOBAL_ONLY = frozenset({
    "leaders", "leader_address", "lane", "lane_overrides", "broker", "broker_env", "live_confirm", "data_dir",
    "shared_equity", "account_kill_dd_pct", "virtual_start_equity", "virtual_fee_rate", "fill_poll_seconds",
    "api_host", "api_port", "dashboard_origins", "api_cookie_secure", "session_days", "alert_telegram_token",
    "alert_telegram_chat_id", "hl_info_url", "hl_ws_url", "stale_feed_seconds",
    "portfolio_enabled", "portfolio_wallets", "portfolio_leverage", "portfolio_start_equities", "portfolio_cap_pct",
    "portfolio_fee_base", "portfolio_slippage", "portfolio_min_order_usd", "portfolio_max_total_x",
    "portfolio_dd_pause_pct", "portfolio_dd_pause_days", "portfolio_copy_hip3", "portfolio_stale_seconds",
    "portfolio_poll_seconds", "portfolio_expected", "portfolio_extra_wallets", "portfolio_groups",
    "portfolio_trio_research", "advisor_enabled", "advisor_model", "advisor_refresh_hours",
    "advisor_max_daily_calls",
})


#: v3 (first_fill) settings that are not part of cap20_cross (botup.md v4 §3: "OFF for cap20_cross -- they would make
#: the demo deviate from the backtest"). Empty in .env = this default under first_fill, off (None) under cap20_cross.
FIRST_FILL_DEFAULTS: dict[str, Any] = {
    "take_profit_roi": Decimal("0.50"), "max_open_positions": 10, "max_entry_drift": Decimal("0.01"),
    "max_signal_age_ms": 10_000, "daily_loss_limit_pct": Decimal("0.15"), "leader_dd_breaker_pct": Decimal("0.20"),
}


@dataclass(frozen=True, slots=True)
class Leader:
    """One copied leader = one lane: its own tracker, mode, risk state and copies."""

    label: str
    address: str


def check_address(v: str) -> str:
    v = v.strip().lower()
    if not (v.startswith("0x") and len(v) == 42 and all(c in "0123456789abcdef" for c in v[2:])):
        raise ValueError(f"{v!r} is not a 0x-prefixed 20-byte address")
    return v


def parse_leaders(raw: str, fallback_address: str) -> list[Leader]:
    """`LEADERS=loco:0x...,3ea:0x...`. Empty means the v2 single leader: `loco:<LEADER_ADDRESS>`."""
    if not raw.strip():
        return [Leader(DEFAULT_LABEL, fallback_address)]
    out = []
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        label, sep, address = part.partition(":")
        label = label.strip().lower()
        if not sep or not LABEL_RE.match(label):
            raise ValueError(f"LEADERS: {part!r} must be label:0xaddress with a label of 2-12 a-z/0-9")
        out.append(Leader(label, check_address(address)))
    if len({x.label for x in out}) != len(out) or len({x.address for x in out}) != len(out):
        raise ValueError("LEADERS: every label and every address must be unique")
    if not out:
        raise ValueError("LEADERS is set but lists no leader")
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # `MAX_NOTIONAL_PER_COIN=` means "not set", not an invalid number
        extra="ignore",
    )

    leader_address: str = LOCO_ADDRESS
    #: v3 multi-leader: `label:address` pairs. Empty = the single v2 leader above, as lane `loco`.
    leaders: str = ""
    #: The lane these settings are for (set by `for_lane`; the process-wide object is the default lane's).
    lane: str = DEFAULT_LABEL
    #: `<SETTING>__<LABEL>` overrides, read by `get_settings()` from the .env file and the environment.
    lane_overrides: dict[str, dict[str, str]] = Field(default_factory=dict)

    copy_enabled: bool = False
    copy_observe_only: bool = True
    #: Execution gate (botup.md v3 §5.3): EXECUTE sends orders only for lanes where this is true. Otherwise the
    #: open is recorded as observed. Set per lane: ALLOW_EXECUTE__LOCO=true.
    allow_execute: bool = False

    #: "first_fill" (v2/v3: first fill only, adds ignored, take profit) or "cap20_cross" (botup.md v4: every fill
    #: copied with ratio R = K x E0 / W0, adds stop at the per-trade margin cap, exits only with the leader).
    strategy: Literal["first_fill", "cap20_cross"] = "first_fill"
    margin_mode: Literal["ISOLATED", "CROSS"] = "ISOLATED"
    #: empty = 0.8 x LEVERAGE. botup.md v5 writes it COPY_RATIO_K: both names are read.
    size_k: Decimal | None = Field(default=None, gt=0, validation_alias=AliasChoices("size_k", "copy_ratio_k"))
    per_trade_margin_cap_pct: Decimal = Field(default=Decimal("20"), gt=0, le=100)
    copy_xyz_dex: bool = False
    copy_spot: bool = False
    #: REST poll of the leaders' fills next to the WS (a gap filler); with several leaders they take turns.
    fill_poll_seconds: int = Field(default=2, ge=1)
    leader_equity_refresh_seconds: int = Field(default=300, ge=30)
    max_total_notional_x_equity: Decimal = Field(default=Decimal("6.0"), gt=0)
    #: No good Hyperliquid contact for this long -> new trades and adds are blocked (exits never are).
    feed_outage_block_seconds: int = Field(default=120, ge=10)
    #: Fallback when an instrument carries no minNotionalValue.
    min_order_usdt: Decimal = Field(default=Decimal("5"), gt=0)
    #: v5: every lane trades in one account and is sized on that whole account's equity (alloc 1.0).
    shared_equity: bool = False
    #: v5 account kill switch: equity below (1 - this) x its peak blocks opens / adds on every lane.
    account_kill_dd_pct: Decimal | None = Field(default=None, gt=0, lt=1)
    #: The observe ledger (botup.md v5 §V5.5): a virtual cross account, filled at Bybit's live bid / ask + fee.
    virtual_start_equity: Decimal = Field(default=Decimal("10000"), gt=0)
    virtual_fee_rate: Decimal = Field(default=Decimal("0.00055"), ge=0)

    # -- the trio simulator on Hyperliquid (botup.md §V6). Simulation only: it never sends an order. Weight w of a
    #    wallet: R = K x w x E / W, CAP = cap% x L x w x E (w = 1: sized on the whole account, as in v5).
    portfolio_enabled: bool = True
    portfolio_wallets: str = DEFAULT_PORTFOLIO_WALLETS
    portfolio_leverage: int = Field(default=3, ge=1, le=20)
    #: one pair of ledgers (base / real) per starting balance, all on the same fills
    portfolio_start_equities: str = "10000,50000,100000"
    portfolio_cap_pct: Decimal = Field(default=Decimal("20"), gt=0, le=100)
    #: Hyperliquid taker fee (the "base" scenario); the "real" one adds PORTFOLIO_SLIPPAGE (0.045 + 0.05 = 0.095 %)
    portfolio_fee_base: Decimal = Field(default=Decimal("0.00045"), ge=0)
    portfolio_slippage: Decimal = Field(default=Decimal("0.0005"), ge=0)
    portfolio_min_order_usd: Decimal = Field(default=Decimal("10"), gt=0)
    portfolio_max_total_x: Decimal = Field(default=Decimal("6"), gt=0)
    #: equity this far (%) below its peak -> no new trades for PORTFOLIO_DD_PAUSE_DAYS (exits continue); empty = off
    portfolio_dd_pause_pct: Decimal | None = Field(default=Decimal("25"), gt=0, lt=100)
    portfolio_dd_pause_days: Decimal = Field(default=Decimal("7"), gt=0)
    #: HIP-3 markets (xyz: stocks / commodities, ...) are copied too, priced on Hyperliquid
    portfolio_copy_hip3: bool = True
    #: a wallet fill we see later than this opens / adds nothing in the "real" scenario (exits still follow)
    portfolio_stale_seconds: int = Field(default=120, ge=10)
    #: REST gap poll: every wallet once per this many seconds (in turn), next to the WebSocket
    portfolio_poll_seconds: int = Field(default=60, ge=10)
    #: reference lines on the dashboard chart, label:yearly growth;label:growth (botup.md §V5.0 / §V5.4: realistic year
    #: $10k -> $316,355 = x31.6; Monte Carlo with half the leaders' edge: $64,341 = x6.4)
    portfolio_expected: str = "الاختبار الواقعي لسنة (+3064%):30.64;متحفظ — نصف أفضلية المتداولين (+543%):5.43"
    #: botup.md §V10 -- more simulator sections next to the trio, on the same fills (only new trades, as the trio).
    #: Each group gets its own base / real ledgers for every PORTFOLIO_START_EQUITIES balance. Empty = trio only.
    portfolio_extra_wallets: str = DEFAULT_PORTFOLIO_EXTRA_WALLETS
    portfolio_groups: str = DEFAULT_PORTFOLIO_GROUPS
    #: the trio on the same research basis as the groups (hunt2, $50k cap per trade): realistic year:half edge
    portfolio_trio_research: str = "24.45:5.33"

    # -- AI trade advisor (botup.md §V8): numbers for every open simulator trade + Claude's written analysis.
    #    The Anthropic key lives in the OS keyring (`loco-copy set-key anthropic`), never here.
    advisor_enabled: bool = True
    #: empty = the newest Sonnet model the key can use (asked from the Anthropic API at the first analysis)
    advisor_model: str = ""
    advisor_refresh_hours: float = Field(default=4, gt=0)
    #: cost guard: at most this many Claude analyses per UTC day (each ~2-4k tokens)
    advisor_max_daily_calls: int = Field(default=40, ge=0)

    broker: Literal["paper", "bybit"] = "paper"
    broker_env: Literal["demo", "live"] = "demo"
    live_confirm: str = ""

    risk_per_trade: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)
    leverage: int = Field(default=20, ge=1, le=100)
    take_profit_roi: Decimal | None = Field(default=None, gt=0)  # first_fill: 0.50 if empty; cap20: must be empty
    protective_sl_pct: Decimal | None = None
    follow_adds: bool = False
    follow_reduces: bool = True

    max_open_positions: int | None = Field(default=None, ge=1)
    max_notional_per_coin: Decimal | None = Field(default=None, gt=0)
    max_entry_drift: Decimal | None = Field(default=None, gt=0)
    max_signal_age_ms: int | None = Field(default=None, gt=0)
    #: Market-order slippage protection sent to the exchange (fraction; 0.005 = 0.5 %).
    max_order_slippage: Decimal = Field(default=Decimal("0.005"), gt=0)

    allow_long: bool = True
    allow_short: bool = True
    coin_allowlist: str = ""  # comma-separated; empty = every coin allowed
    coin_blocklist: str = ""

    daily_loss_limit_pct: Decimal | None = Field(default=None, gt=0, lt=1)
    leader_dd_breaker_pct: Decimal | None = Field(default=None, gt=0, lt=1)
    leader_snapshot_seconds: int = Field(default=20, ge=5)
    reconcile_seconds: int = Field(default=30, ge=5)
    #: Loco flat but our copy still open for longer than this -> auto-close (the one automatic correction).
    missed_exit_seconds: int = Field(default=60, ge=10)
    #: No WS frame (pongs count) for longer than this -> latency breaker pauses new opens.
    stale_feed_seconds: int = Field(default=60, ge=10)

    paper_start_equity: Decimal = Field(default=Decimal("10000"), gt=0)
    paper_fee_rate: Decimal = Field(default=Decimal("0.00055"), ge=0)  # Bybit taker
    paper_slippage_bps: Decimal = Field(default=Decimal("4"), ge=0)  # botup.md v4 §5 paper.py: 4 bps default
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

    #: FIRST_FILL_DEFAULTS names that were filled in (not set by the owner) -- a lane switching strategy may drop them.
    _defaulted: frozenset[str] = PrivateAttr(default=frozenset())

    @field_validator("account_kill_dd_pct", mode="before")
    @classmethod
    def _dd_as_fraction(cls, v: Any) -> Any:
        """ACCOUNT_KILL_DD_PCT=25 and =0.25 both mean 25 % (botup.md v5 writes it both ways)."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        d = Decimal(str(v).strip())
        return d / 100 if d >= 1 else d

    @field_validator("leader_address")
    @classmethod
    def _address(cls, v: str) -> str:
        try:
            return check_address(v)
        except ValueError as exc:
            raise ValueError(f"LEADER_ADDRESS: {exc}") from exc

    @model_validator(mode="after")
    def _safety(self) -> Settings:
        parse_leaders(self.leaders, self.leader_address)  # refuse a bad LEADERS at start-up
        update, self._defaulted = strategy_defaults(self, frozenset())
        self.__dict__.update(update)
        check_strategy(self)
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
    def k(self) -> Decimal:
        """Size multiplier K: SIZE_K, or 0.8 x LEVERAGE (4.0 at 5x, 2.4 at 3x)."""
        return self.size_k if self.size_k is not None else Decimal("0.8") * self.leverage

    @property
    def is_cap20(self) -> bool:
        return self.strategy == "cap20_cross"

    @property
    def shared_keyring_service(self) -> str:
        """The v2 key name. A lane without keys of its own trades on this account (shared-account layout)."""
        return f"loco-copy-bybit-{self.broker_env}"

    @property
    def keyring_service(self) -> str:
        base = self.shared_keyring_service
        return base if self.lane == DEFAULT_LABEL else f"{base}-{self.lane}"

    # -- lanes (botup.md v3) ------------------------------------------------------------------------------------

    @property
    def leader_list(self) -> list[Leader]:
        return parse_leaders(self.leaders, self.leader_address)

    @property
    def labels(self) -> list[str]:
        return [x.label for x in self.leader_list]

    def for_lane(self, label: str) -> Settings:
        """This process's settings as seen by one lane: its leader, plus its `<SETTING>__<LABEL>` overrides.
        A lane other than `loco` starts in WATCH unless COPY_ENABLED__<LABEL> says otherwise."""
        leader = next((x for x in self.leader_list if x.label == label), None)
        if leader is None:
            raise KeyError(f"unknown leader lane {label!r} (LEADERS has {self.labels})")
        update: dict[str, Any] = {"lane": label, "leader_address": leader.address}
        if label != DEFAULT_LABEL:
            update |= {"copy_enabled": False, "copy_observe_only": True}
        fields = type(self).model_fields
        for name, raw in self.lane_overrides.get(label, {}).items():
            meta = fields[name].metadata
            ann = fields[name].annotation
            form: Any = Annotated[(ann, *meta)] if meta else ann  # the field's type with its constraints
            adapter: TypeAdapter[Any] = TypeAdapter(form)
            try:
                update[name] = adapter.validate_python(raw)
            except ValueError as exc:
                raise ValueError(f"{name.upper()}__{label.upper()}={raw!r}: {exc}") from exc
        lane = self.model_copy(update=update)
        explicit = set(self.lane_overrides.get(label, {}))
        filled, defaulted = strategy_defaults(lane, self._defaulted - explicit)
        lane = lane.model_copy(update=filled)
        lane._defaulted = defaulted  # noqa: SLF001 - same class
        if Decimal(1) / lane.leverage <= lane.maintenance_margin_rate:
            raise ValueError(f"LEVERAGE__{label.upper()} too high: liquidation would be immediate.")
        try:
            check_strategy(lane)
        except ValueError as exc:
            raise ValueError(f"leader {label}: {exc}") from exc
        return lane


def strategy_defaults(s: Settings, defaulted: frozenset[str]) -> tuple[dict[str, Any], frozenset[str]]:
    """The v3-only settings for this strategy: cap20_cross turns them all off and refuses a take profit set by the
    owner; first_fill gets its v3 defaults where empty. Returns (updates, names now holding a filled-in default)."""
    if s.strategy == "cap20_cross":
        if s.take_profit_roi is not None and "take_profit_roi" not in defaulted:
            raise ValueError("STRATEGY=cap20_cross has no take profit (exits only with the leader): "
                             "leave TAKE_PROFIT_ROI empty.")
        return {name: None for name in FIRST_FILL_DEFAULTS}, frozenset()
    filled = {k: v for k, v in FIRST_FILL_DEFAULTS.items() if getattr(s, k) is None}
    return filled, defaulted | frozenset(filled)


def strategy_warnings(s: Settings) -> list[str]:
    """Allowed but outside the spec: said once at start-up (botup.md v4 §3)."""
    if s.strategy == "cap20_cross" and s.leverage not in (3, 5):
        return [f"LEVERAGE={s.leverage}: the cap20 spec was tested at 3 or 5 only"]
    return []


def check_strategy(s: Settings) -> None:
    """botup.md v4 §3: what each strategy requires. FOLLOW_ADDS stays refused outside cap20_cross."""
    if s.strategy == "cap20_cross":
        if s.take_profit_roi is not None:
            raise ValueError("STRATEGY=cap20_cross has no take profit: leave TAKE_PROFIT_ROI empty.")
        if s.margin_mode != "CROSS":
            raise ValueError("STRATEGY=cap20_cross needs MARGIN_MODE=CROSS (it refuses ISOLATED).")
        if not s.follow_adds:
            raise ValueError("STRATEGY=cap20_cross needs FOLLOW_ADDS=true: it copies every add up to the cap.")
        if s.protective_sl_pct is not None:
            raise ValueError("STRATEGY=cap20_cross has no stop loss: leave PROTECTIVE_SL_PCT empty.")
        if s.copy_xyz_dex or s.copy_spot:
            raise ValueError("COPY_XYZ_DEX / COPY_SPOT: those markets are not on Bybit -- keep them false.")
    elif s.follow_adds:
        raise ValueError("FOLLOW_ADDS=true needs STRATEGY=cap20_cross (first_fill ignores adds by design).")
    elif s.take_profit_roi is None:
        raise ValueError("STRATEGY=first_fill needs a take profit (TAKE_PROFIT_ROI).")


def read_lane_overrides(labels: list[str], env_file: Path | None) -> dict[str, dict[str, str]]:
    """`RISK_PER_TRADE__3EA=0.05` -> {"3ea": {"risk_per_trade": "0.05"}}, from the .env file then the
    environment (which wins). A key that looks like an override but names an unknown setting or lane, or a
    global-only setting, stops start-up: a silent typo here would trade with the wrong size."""
    raw: dict[str, str] = {}
    if env_file is not None and env_file.exists():
        raw |= {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    raw |= dict(os.environ)
    fields = Settings.model_fields
    out: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        m = OVERRIDE_KEY_RE.match(key)
        if not m:
            continue
        name, label = m.group(1).lower(), m.group(2).lower()
        name = SETTING_ALIASES.get(name, name)
        if name not in fields and label not in labels:
            continue  # someone else's variable
        if name not in fields or label not in labels:
            raise ValueError(f"{key}: unknown {'setting' if name not in fields else 'leader label'} "
                             f"(leaders: {', '.join(labels)})")
        if name in GLOBAL_ONLY:
            raise ValueError(f"{key}: {name.upper()} is global and cannot be set per leader")
        if value.strip():
            out.setdefault(label, {})[name] = value.strip()
    return out


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    env_file = s.model_config.get("env_file")
    overrides = read_lane_overrides(s.labels, Path(str(env_file)) if env_file else None)
    s = s.model_copy(update={"lane_overrides": overrides})
    for label in s.labels:
        s.for_lane(label)  # every lane's overrides are validated before anything starts
    return s


def with_mode_override(base: Settings, override: dict[str, Any] | None) -> Settings:
    """The dashboard can switch watch / observe / execute at runtime (stored in the DB, read by the bot every
    few seconds). It can never change the broker or go live -- those stay in .env behind LIVE_CONFIRM."""
    if not override:
        return base
    return base.model_copy(update={"copy_enabled": bool(override.get("copy_enabled")),
                                   "copy_observe_only": bool(override.get("observe_only", True))})
