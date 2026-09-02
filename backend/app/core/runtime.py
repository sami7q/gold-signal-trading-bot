"""Process-wide singletons for stateful adapters.

`PaperBroker` and `MockSignalSource` hold in-memory state (simulated
positions, the current price, the registered message handler) that must
survive across HTTP requests — each request gets its own DB session, but
there is exactly one broker and one mock source per running process.
`lru_cache` gives us that singleton behavior for free and composes with
FastAPI's `Depends`, matching `get_settings()`.
"""

from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.integrations.broker.base import Broker
from app.integrations.broker.paper import PaperBroker
from app.integrations.signal_source.base import SignalSource
from app.integrations.signal_source.mock import MockSignalSource


@lru_cache
def get_paper_broker() -> PaperBroker:
    settings = get_settings()
    return PaperBroker(
        starting_balance=settings.paper_starting_balance,
        symbol=settings.paper_symbol,
        point=settings.paper_point,
        digits=settings.paper_digits,
        contract_size=settings.paper_contract_size,
        volume_min=settings.paper_volume_min,
        volume_max=settings.paper_volume_max,
        volume_step=settings.paper_volume_step,
        spread_points=settings.paper_spread_points,
    )


_active_broker: Broker | None = None


def set_active_broker(broker: Broker) -> None:
    """Called by `app.lifecycle` once MT5 successfully connects."""
    global _active_broker
    _active_broker = broker


def get_broker() -> Broker:
    """The active broker for the current trading mode.

    MT5 is only ever active once `MT5_ENABLED=true` *and* `connect()` has
    already succeeded during startup (see `app/lifecycle.py`) — until then,
    every mode, including DEMO/LIVE, falls back to the paper broker so the
    app never silently trades against a half-configured MT5 connection.
    """
    return _active_broker or get_paper_broker()


_resolved_account_id: int | None = None


def set_resolved_account_id(account_id: int | None) -> None:
    """Called once at process startup (the hub in `app/lifecycle.py`, or a
    `backend/scripts/worker.py` instance) after resolving which
    `MT5Account` row matches this process's own `.env` credentials
    (concurrent-execution "Phase 2" update -- see
    `AccountService.resolve_own_process_account`). Replaces the old
    single-global-`is_live` lookup: identity is now a fact about *this
    process*, decided once, not a DB flag re-queried on every tick."""
    global _resolved_account_id
    _resolved_account_id = account_id


def get_resolved_account_id() -> int | None:
    return _resolved_account_id


@lru_cache
def get_mock_signal_source() -> MockSignalSource:
    return MockSignalSource()


_active_signal_source: SignalSource | None = None


def set_active_signal_source(source: SignalSource) -> None:
    """Called by `app.lifecycle` once a real source (Telegram) starts
    successfully, so `/api/health/signal-source` and the dashboard status
    strip reflect it instead of the mock source."""
    global _active_signal_source
    _active_signal_source = source


def get_active_signal_source() -> SignalSource:
    """The signal source `/api/health/signal-source` should report on.

    Falls back to the mock source until a real one has actually connected —
    mirrors `get_broker()`'s fallback-to-paper behavior exactly, for the
    same reason: never claim a real integration is active before it's
    genuinely confirmed to be.
    """
    return _active_signal_source or get_mock_signal_source()


def reset_runtime_singletons() -> None:
    """Test-only: drop cached singletons so each test gets a fresh broker/source."""
    global _active_broker, _active_signal_source, _resolved_account_id
    get_paper_broker.cache_clear()
    get_mock_signal_source.cache_clear()
    _active_broker = None
    _active_signal_source = None
    _resolved_account_id = None
