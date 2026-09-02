"""Application startup / shutdown (PROJECT_SPEC.md section 9 reliability goals).

Both real integrations stay inert unless their `*_ENABLED` flag is set —
that flag is the operator's explicit instruction (PROJECT_SPEC.md section 32,
rule 3/4), this module just honors it. A failed Telegram/MT5 connection at
boot is logged and never crashes the app or silently enables trading; the
broker simply falls back to the Paper Broker (see `app/core/runtime.py`).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlmodel import Session

from app.accounts.service import AccountService
from app.audit.service import AuditService
from app.core.config import Settings, get_settings
from app.core.constants import AuditEventType, AuditSeverity, ErrorCode, TradingMode
from app.core.logging import configure_logging, get_logger
from app.core.runtime import (
    get_broker,
    get_mock_signal_source,
    get_resolved_account_id,
    set_active_broker,
    set_active_signal_source,
    set_resolved_account_id,
)
from app.database.session import create_db_and_tables, get_engine
from app.integrations.broker.mt5 import MT5Broker, MT5ConnectionError
from app.integrations.signal_source.telegram import TelegramSignalSource
from app.settings.service import SettingsService
from app.signals.models import IncomingMessageData
from app.trading.reconciliation import ReconciliationService
from app.trading.service import TradingService

logger = get_logger(__name__)


def assert_paper_broker_active_if_telegram_live(settings: Settings) -> None:
    """Phase 11 safety assertion: if Telegram is on and trading is still in
    SIMULATION mode, the active broker must be the Paper Broker — no code
    path should have been able to make MT5Broker active here. This is a
    hard stop, not a logged warning: if it's ever false, the alternative is
    a real broker silently receiving orders from what's supposed to be a
    read-only integration phase.

    Extracted from `lifespan` so it's independently unit-testable without
    spinning up the whole FastAPI app.
    """
    if not (settings.telegram_enabled and settings.trading_mode == TradingMode.SIMULATION):
        return
    active_name = get_broker().name
    if active_name != "paper":
        raise RuntimeError(
            "Safety violation: TELEGRAM_ENABLED=true and TRADING_MODE=SIMULATION but the "
            f"active broker is '{active_name}', not 'paper'. Refusing to start."
        )


async def _poll_open_trades(poll_seconds: int) -> None:
    """Re-runs the same reconciliation the startup path uses, on a timer.

    A real broker closes a position on its own server the instant SL or TP
    is hit — our database only finds out by asking. Without this loop that
    only ever happened once, at process startup, so a trade that closed
    five minutes into a long-running process would sit as OPEN (wrong
    balance/equity, wrong win/loss stats) until the next restart.

    Also this process's heartbeat tick (concurrent-execution "Phase 2"
    update): written only when the broker actually reports healthy, piggy-
    backed on this existing timer rather than a separate one -- see
    AccountService.record_heartbeat's docstring for why this replaced the
    old manually-toggled `is_live` flag.
    """
    while True:
        await asyncio.sleep(poll_seconds)
        account_id = get_resolved_account_id()
        if account_id is None:
            continue
        try:
            with Session(get_engine()) as session:
                broker = get_broker()
                audit = AuditService(session)
                ReconciliationService(session, broker, audit).reconcile_open_trades(account_id)
                try:
                    healthy = broker.health().healthy
                except Exception:  # noqa: BLE001 - a broker fault must never block the poll
                    healthy = False
                if healthy:
                    AccountService(session, audit).record_heartbeat(account_id)
        except Exception:  # noqa: BLE001 - one bad poll must never kill the loop
            logger.exception("Periodic trade reconciliation failed")


async def _poll_expired_signals(poll_seconds: int) -> None:
    """Sweeps signals stuck waiting for a channel Stop Loss, on a timer.

    `TradingService.sweep_expired_signals` is only useful if something
    actually calls it on a schedule -- without this loop a signal that
    never got a follow-up Stop Loss message just sat in
    WAITING_FOR_DETAILS forever (no auto Stop Loss fallback, no expiry,
    no execution). A short interval keeps the gap between "timeout
    elapses" and "fallback Stop Loss applied / signal expired" tight.
    """
    while True:
        await asyncio.sleep(poll_seconds)
        try:
            with Session(get_engine()) as session:
                broker = get_broker()
                audit = AuditService(session)
                account_id = get_resolved_account_id()
                TradingService(
                    session, broker, audit, account_id=account_id
                ).sweep_expired_signals()
        except Exception:  # noqa: BLE001 - one bad sweep must never kill the loop
            logger.exception("Periodic signal expiry sweep failed")


def _message_handler(data: IncomingMessageData) -> None:
    """Entry point for any long-running async source (currently: Telegram).

    Opens its own session because it runs outside any HTTP request's
    request-scoped session. Uses this *process's own* resolved account
    (concurrent-execution "Phase 2" update: resolved once at startup by
    matching this process's own `.env` MT5 credentials against
    `MT5Account` -- see `lifespan` below and `app/core/runtime.py`) --
    `None` if this process's own credentials don't match any registered
    account, which still lets the message be parsed but never reach the
    trading engine. A second (or third...) account that wants real
    concurrent execution runs its own separate `backend/scripts/worker.py`
    process instead, each resolving and executing for its own account.
    """
    with Session(get_engine()) as session:
        try:
            audit = AuditService(session)
            account_id = get_resolved_account_id()
            TradingService(
                session, get_broker(), audit, account_id=account_id
            ).process_incoming_message(data)
        except Exception:  # noqa: BLE001 - a bad message must never kill the source
            logger.exception("Failed to process incoming message from %s", data.source)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    if settings.database_url.startswith("sqlite"):
        create_db_and_tables()

    #: Always available (used by `/api/health/signal-source` and by tests /
    #: any programmatic caller that wants to go through the adapter
    #: interface instead of calling TradingService directly).
    mock_source = get_mock_signal_source()
    mock_source.on_message(_message_handler)
    await mock_source.start()

    # This process's own identity (concurrent-execution "Phase 2" update):
    # resolved unconditionally here, from `.env`'s MT5_ACCOUNT/MT5_SERVER --
    # *not* gated on a real MT5 connection succeeding below, so dev/test/
    # simulation-only runs (MT5_ENABLED=false, the documented default)
    # resolve exactly like they did under Phase 1's `get_live_account()`.
    # A `backend/scripts/worker.py` instance resolves differently (only
    # after *its own* successful `MT5Broker.connect()` -- see that script)
    # since a worker with no real connection has no reason to run at all.
    with Session(get_engine()) as session:
        audit = AuditService(session)
        own_account = AccountService(session, audit).resolve_own_process_account(
            settings.mt5_account, settings.mt5_server
        )
        set_resolved_account_id(own_account.id if own_account else None)
        if own_account is not None:
            SettingsService(session, audit).get_for_account(own_account.id)  # type: ignore[arg-type]
        else:
            audit.log(
                AuditEventType.NO_LIVE_ACCOUNT_CONFIGURED,
                "Backend starting -- no MT5Account row matches this process's own "
                f"MT5_ACCOUNT/MT5_SERVER ('{settings.mt5_account}'/'{settings.mt5_server}'). "
                "Signals will be parsed but never executed until a matching account is "
                "registered (see the /accounts page).",
                severity=AuditSeverity.WARNING,
            )
        audit.log(
            AuditEventType.SERVICE_STARTED,
            "Backend started",
            metadata={
                "trading_mode": settings.trading_mode.value,
                "auto_trading": settings.auto_trading,
                "telegram_enabled": settings.telegram_enabled,
                "mt5_enabled": settings.mt5_enabled,
            },
        )

    telegram_source: TelegramSignalSource | None = None
    if settings.telegram_enabled:
        telegram_source = TelegramSignalSource(settings)
        telegram_source.on_message(_message_handler)
        try:
            await telegram_source.start()
            set_active_signal_source(telegram_source)
        except Exception:  # noqa: BLE001
            logger.exception("Telegram source failed to start; continuing without it.")
            with Session(get_engine()) as session:
                AuditService(session).log(
                    ErrorCode.TELEGRAM_SOURCE_DISCONNECTED,
                    "Telegram source failed to start at boot.",
                    severity=AuditSeverity.ERROR,
                )
            telegram_source = None

    mt5_broker: MT5Broker | None = None
    if settings.mt5_enabled:
        mt5_broker = MT5Broker(settings)
        try:
            mt5_broker.connect()
            set_active_broker(mt5_broker)
        except MT5ConnectionError:
            logger.exception("MT5 broker failed to connect at boot; falling back to Paper Broker.")
            with Session(get_engine()) as session:
                AuditService(session).log(
                    ErrorCode.BROKER_CONNECTION_FAILED,
                    "MT5 broker failed to connect at boot; using Paper Broker instead.",
                    severity=AuditSeverity.CRITICAL,
                )
            mt5_broker = None

    # Runs against whichever broker actually ended up active (paper, or MT5
    # if it just connected above) — must happen after the MT5 connect
    # attempt, not before, so a DB `OPEN` trade is checked against the real
    # broker rather than assumed to be a Paper Broker position. Scoped to
    # this process's own resolved account only -- see reconcile_open_trades'
    # docstring for why an unscoped reconciliation is unsafe now that other
    # accounts' OPEN trades can exist in the same shared database, reconciled
    # by their own separate worker processes against their own brokers.
    own_account_id = get_resolved_account_id()
    if own_account_id is not None:
        with Session(get_engine()) as session:
            active_broker = get_broker()
            reconciliation = ReconciliationService(session, active_broker, AuditService(session))
            reconciliation.reconcile_open_trades(own_account_id)

    assert_paper_broker_active_if_telegram_live(settings)

    poll_task = asyncio.create_task(_poll_open_trades(settings.position_poll_seconds))
    expiry_poll_task = asyncio.create_task(
        _poll_expired_signals(settings.signal_expiry_poll_seconds)
    )

    yield

    for task in (poll_task, expiry_poll_task):
        task.cancel()
    for task in (poll_task, expiry_poll_task):
        with contextlib.suppress(asyncio.CancelledError):
            await task

    await mock_source.stop()

    if telegram_source is not None:
        await telegram_source.stop()
    if mt5_broker is not None:
        mt5_broker.disconnect()

    with Session(get_engine()) as session:
        AuditService(session).log(AuditEventType.SERVICE_STOPPED, "Backend stopping")
