"""Per-account real-execution worker (concurrent-execution "Phase 2",
2026-09). One of these runs per additional MT5 account that should trade
for real at the same time as the hub (backend/app/main.py) -- each with
its own MT5 terminal connection, its own Windows process, its own Task
Scheduler task. See the approved plan for the full architecture and the
VPS provisioning runbook.

No HTTP server, no Telegram connection (TELEGRAM_ENABLED must be false in
this process's own .env -- ingestion is the hub's job alone; running a
second Telegram session concurrently triggers Telegram's own
AuthKeyDuplicatedError, as happened for real during this project's
development). This process only: connects its own MT5 terminal, resolves
which MT5Account row that is, then loops polling the shared database for
signals it hasn't executed yet, reconciling its own open trades, and
writing its own heartbeat.

Usage (from the backend/ directory, with the project venv active, on
Windows -- set the env var however the process supervisor does it, e.g. as
the Task Scheduler action's environment, or inline for a manual test run):

    set GOLDBOT_ENV_FILE=C:\\gold-bot\\.env.worker-450137654
    python scripts\\worker.py

Never falls back to a Paper Broker -- a worker's entire purpose is real
execution; a failed connection exits immediately (non-zero) so the process
supervisor's restart loop gets a fresh connection attempt, exactly like the
hub already relies on for its own recovery.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.accounts.service import AccountService  # noqa: E402
from app.audit.service import AuditService  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.database.session import create_db_and_tables, get_engine  # noqa: E402
from app.integrations.broker.mt5 import MT5Broker, MT5ConnectionError  # noqa: E402
from app.trading.reconciliation import ReconciliationService  # noqa: E402
from app.trading.service import TradingService  # noqa: E402

logger = get_logger(__name__)

#: Consecutive failed poll ticks before this process gives up and exits
#: (non-zero), letting the process supervisor's restart loop attempt a
#: fresh MT5 connection -- the same recovery mechanism the hub already
#: relies on via full-process restart, not a new reconnect path invented
#: here (docs/10-mt5-integration.md documents "MT5 connection drops
#: mid-session: not yet auto-reconnected" as an accepted hub limitation;
#: this worker stays consistent with that instead of diverging from it).
MAX_CONSECUTIVE_FAILURES = 5


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.mt5_enabled:
        logger.error("MT5_ENABLED=false in this worker's env file -- refusing to start.")
        raise SystemExit(1)

    if settings.database_url.startswith("sqlite"):
        create_db_and_tables()

    broker = MT5Broker(settings)
    try:
        broker.connect()
    except MT5ConnectionError:
        logger.exception("Worker failed to connect to its own MT5 terminal -- exiting.")
        raise SystemExit(1) from None

    with Session(get_engine()) as session:
        audit = AuditService(session)
        account = AccountService(session, audit).resolve_own_process_account(
            settings.mt5_account, settings.mt5_server
        )
        if account is None or account.id is None:
            logger.error(
                "No MT5Account row matches this worker's MT5_ACCOUNT/MT5_SERVER "
                "('%s'/'%s') -- register it via the /accounts page first, then "
                "restart this worker.",
                settings.mt5_account,
                settings.mt5_server,
            )
            raise SystemExit(1)
        account_id = account.id

    logger.info(
        "Worker connected -- resolved account_id=%s, polling every %ss.",
        account_id,
        settings.worker_poll_seconds,
    )

    consecutive_failures = 0
    while True:
        time.sleep(settings.worker_poll_seconds)
        try:
            with Session(get_engine()) as session:
                audit = AuditService(session)
                TradingService(
                    session, broker, audit, account_id=account_id
                ).execute_ready_signals()
                ReconciliationService(session, broker, audit).reconcile_open_trades(account_id)
                try:
                    healthy = broker.health().healthy
                except Exception:  # noqa: BLE001 - a broker fault must never crash the loop
                    healthy = False
                if healthy:
                    balance = equity = None
                    try:
                        account_info = broker.get_account()
                        balance, equity = account_info.balance, account_info.equity
                    except Exception:  # noqa: BLE001 - stale balance beats a broken loop
                        pass
                    AccountService(session, audit).record_heartbeat(
                        account_id, balance=balance, equity=equity
                    )
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
        except Exception:  # noqa: BLE001 - one bad tick must never silently hang; count it instead
            logger.exception("Worker poll tick failed")
            consecutive_failures += 1

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.error(
                "%s consecutive failed ticks -- exiting so the process supervisor "
                "can attempt a fresh connection.",
                consecutive_failures,
            )
            raise SystemExit(1)


if __name__ == "__main__":
    main()
