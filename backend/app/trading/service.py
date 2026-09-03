"""Top-level orchestration: message ingestion -> signal -> engine, and
broker price ticks -> trade closure -> statistics.

This is the one module allowed to depend on both `app.signals` and
`app.trading` — it is the glue the architecture diagram in
PROJECT_SPEC.md section 1 describes, kept out of both lower layers so
neither has to know the other exists.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, select

from app.audit.service import AuditService
from app.core.config import get_settings
from app.core.constants import (
    AuditEventType,
    AuditSeverity,
    SignalStatus,
    TradeResult,
    TradeStatus,
)
from app.core.decimals import fmt
from app.database.models.signal import Signal
from app.database.models.trade import Trade
from app.database.models.trading_settings import TradingSettings
from app.integrations.broker.base import Broker, BrokerPosition
from app.settings.service import SettingsService
from app.signals.models import IncomingMessageData
from app.signals.service import IngestResult, SignalService
from app.trading import pips
from app.trading.engine import TradingEngine

#: Fallback used only when there is no live account to read a real
#: `TradingSettings` row from (see `account_id=None` below) -- matches
#: `TradingSettings.signal_completion_timeout_seconds`'s own default.
_NO_ACCOUNT_TIMEOUT_SECONDS = 120


class TradingService:
    def __init__(
        self,
        session: Session,
        broker: Broker,
        audit: AuditService | None = None,
        *,
        account_id: int | None,
        signal_completion_timeout_seconds: int | None = None,
    ) -> None:
        """`account_id=None` means "no MT5 account is currently live" --
        used only from `app/lifecycle.py`'s background message handler and
        signal-expiry sweep, which run independent of any logged-in HTTP
        request. Every request-driven caller (the API routes) always has a
        real account via `get_current_account` and must pass its id.
        In the `None` case, messages are still ingested/parsed (so the
        signal history stays complete for whenever an account goes live
        again) but never reach the trading engine -- see
        `NO_LIVE_ACCOUNT_CONFIGURED` below.
        """
        self._session = session
        self._broker = broker
        self._audit = audit or AuditService(session)
        self._account_id = account_id
        self._settings_service = SettingsService(session, self._audit)

        timeout = signal_completion_timeout_seconds
        if timeout is None:
            timeout = (
                self._settings_service.get_for_account(
                    account_id
                ).signal_completion_timeout_seconds
                if account_id is not None
                else _NO_ACCOUNT_TIMEOUT_SECONDS
            )

        self._signal_service = SignalService(
            session, self._audit, signal_completion_timeout_seconds=timeout
        )
        self._engine = TradingEngine(session, broker, self._audit)

    # -- inbound messages -----------------------------------------------------

    def process_incoming_message(self, data: IncomingMessageData) -> IngestResult:
        result = self._signal_service.ingest_message(data)
        if result.became_ready and result.signal is not None:
            if self._account_id is None:
                self._audit.log(
                    AuditEventType.NO_LIVE_ACCOUNT_CONFIGURED,
                    f"Signal {result.signal.id} reached READY but no MT5 account is "
                    "currently live -- not sent to the trading engine.",
                    severity=AuditSeverity.WARNING,
                    metadata={"signal_id": result.signal.id, "source": data.source},
                )
            elif self._is_observed_only(data.source):
                self._audit.log(
                    AuditEventType.SIGNAL_OBSERVED_ONLY,
                    f"Signal {result.signal.id} reached READY from a real Telegram message "
                    "but TELEGRAM_OBSERVE_ONLY=true — not sent to the trading engine.",
                    severity=AuditSeverity.INFO,
                    metadata={"signal_id": result.signal.id, "source": data.source},
                )
            else:
                settings = self._settings_service.get_for_account(self._account_id)
                self._engine.execute(result.signal, settings)
                self._session.refresh(result.signal)
        return result

    def execute_ready_signals(self) -> list[Trade]:
        """Polled by `backend/scripts/worker.py` on its own account-scoped
        timer -- every execution-eligible signal this account doesn't have
        a `Trade` for yet (see `SignalService.find_unexecuted_for_account`
        for why that's the correct per-account watermark, not
        `Signal.status == READY`), executed via the same
        `TradingEngine.execute()` every other path uses -- fully idempotent
        per account via `execution_key`, so re-scanning the same signals on
        every poll tick is safe: a signal this account already executed is
        a guaranteed no-op, not a duplicate trade. Same observe-only gate
        as `process_incoming_message` -- a real Telegram signal must still
        never reach the engine while `TELEGRAM_OBSERVE_ONLY=true`, for
        every account, not just whichever one calls this synchronously.
        """
        if self._account_id is None:
            return []
        signals = self._signal_service.find_unexecuted_for_account(self._account_id)
        if not signals:
            return []
        settings = self._settings_service.get_for_account(self._account_id)
        executed: list[Trade] = []
        for signal in signals:
            if self._is_observed_only(signal.source):
                # Same event `process_incoming_message` logs for the
                # synchronous path (see below) -- without this a worker
                # silently skipping every signal (e.g. a fresh worker's
                # .env missing TELEGRAM_OBSERVE_ONLY=false, confirmed real
                # 2026-09-03) left zero trace anywhere: no error, no
                # Trade row, nothing to grep for.
                self._audit.log(
                    AuditEventType.SIGNAL_OBSERVED_ONLY,
                    f"Signal {signal.id} eligible for account {self._account_id} but "
                    "TELEGRAM_OBSERVE_ONLY=true -- not sent to the trading engine.",
                    severity=AuditSeverity.INFO,
                    metadata={"signal_id": signal.id, "account_id": self._account_id},
                )
                continue
            executed.append(self._engine.execute(signal, settings))
        return executed

    @staticmethod
    def _is_observed_only(source: str) -> bool:
        """Phase 11 safety gate (PROJECT_SPEC.md section 17): real Telegram
        messages still flow through the parser and state machine so the
        channel's actual grammar can be validated, but never reach
        `TradingEngine.execute` while this is on. Mock/simulation messages
        are unaffected — an operator testing via the dashboard always wants
        full execution."""
        return source == "telegram" and get_settings().telegram_observe_only

    def sweep_expired_signals(self) -> list[Signal]:
        """Runs on a timer (see app/lifecycle.py's `_poll_expired_signals`).

        For each signal past its completion timeout with no Stop Loss yet:
        if `TradingSettings.auto_sl_fallback_pips` is set, compute a Stop
        Loss that many pips behind the live quote, attach it, and execute
        the signal immediately -- instead of letting it expire unexecuted.
        `None` (or a broker that isn't currently reachable) falls back to
        the original behavior: expire it, no trade.
        """
        now = datetime.now(UTC)
        stale = self._signal_service.find_stale_signals(now=now)
        if not stale:
            return []

        if self._account_id is None:
            # No account to read a real auto-SL-fallback setting from --
            # same as the observe-only gate, just expire them (no trade).
            processed: list[Signal] = []
            for signal in stale:
                self._audit.log(
                    AuditEventType.NO_LIVE_ACCOUNT_CONFIGURED,
                    f"Signal {signal.id} timed out waiting for a Stop Loss and no MT5 "
                    "account is currently live -- expiring instead.",
                    severity=AuditSeverity.WARNING,
                    metadata={"signal_id": signal.id},
                )
                processed.append(self._signal_service.expire_one(signal, now=now))
            return processed

        settings = self._settings_service.get_for_account(self._account_id)
        processed = []
        for signal in stale:
            if settings.auto_sl_fallback_pips is not None:
                processed.append(self._apply_auto_stop_loss_fallback(signal, settings, now=now))
            else:
                processed.append(self._signal_service.expire_one(signal, now=now))
        return processed

    def _apply_auto_stop_loss_fallback(
        self, signal: Signal, settings: TradingSettings, *, now: datetime
    ) -> Signal:
        assert settings.auto_sl_fallback_pips is not None
        try:
            broker_health = self._broker.health()
            symbol_info = self._broker.get_symbol(signal.symbol) if broker_health.healthy else None
            quote = self._broker.get_quote(signal.symbol) if broker_health.healthy else None
        except Exception:  # noqa: BLE001 - a broker fault must never block the sweep
            symbol_info = None
            quote = None

        if symbol_info is None or quote is None:
            # Broker unreachable right now -- never guess a price. Falls
            # back to the safe pre-v1.2 behavior for this signal only; the
            # next sweep tick will retry the fallback for any signal still
            # open (this one is now EXPIRED, so it won't be retried, but a
            # broker outage at exactly the timeout instant is rare and the
            # existing REJECTED/FAILED-on-BROKER_UNAVAILABLE path already
            # covers "broker down" everywhere else in the pipeline).
            self._audit.log(
                AuditEventType.BROKER_ERROR,
                f"Signal {signal.id} timed out waiting for a Stop Loss and the broker was "
                "unavailable to price an auto fallback -- expiring instead.",
                severity=AuditSeverity.ERROR,
                metadata={"signal_id": signal.id},
            )
            return self._signal_service.expire_one(signal, now=now)

        stop_loss = pips.compute_stop_loss(
            entry_price=quote.ask,
            pips=settings.auto_sl_fallback_pips,
            symbol_info=symbol_info,
            side=signal.side,
        )
        self._audit.log(
            AuditEventType.AUTO_STOP_LOSS_APPLIED,
            f"Signal {signal.id} had no Stop Loss after "
            f"{settings.signal_completion_timeout_seconds}s -- applying auto fallback of "
            f"{fmt(settings.auto_sl_fallback_pips)} pips ({fmt(stop_loss)}) and executing.",
            metadata={
                "signal_id": signal.id,
                "auto_sl_fallback_pips": str(settings.auto_sl_fallback_pips),
                "stop_loss": str(stop_loss),
                "quote_ask": str(quote.ask),
            },
        )
        # `find_stale_signals` only ever returns open-status signals, and
        # `apply_stop_loss_and_ready` always advances an open signal to
        # READY once it has a Stop Loss -- so this is unconditionally READY
        # here, same as the has_sl-in-one-message path in
        # `process_incoming_message` right above.
        signal = self._signal_service.apply_stop_loss_and_ready(
            signal, stop_loss=stop_loss, now=now
        )
        assert signal.status == SignalStatus.READY

        if self._is_observed_only(signal.source):
            self._audit.log(
                AuditEventType.SIGNAL_OBSERVED_ONLY,
                f"Signal {signal.id} reached READY via auto Stop Loss fallback but "
                "TELEGRAM_OBSERVE_ONLY=true — not sent to the trading engine.",
                severity=AuditSeverity.INFO,
                metadata={"signal_id": signal.id, "source": signal.source},
            )
        else:
            self._engine.execute(signal, settings)
            self._session.refresh(signal)
        return signal

    # -- price ticks -> trade closure -----------------------------------------

    def on_positions_closed(self, closed_positions: list[BrokerPosition]) -> list[Trade]:
        updated: list[Trade] = []
        for position in closed_positions:
            statement = select(Trade).where(Trade.broker_position_id == position.position_id)
            trade = self._session.exec(statement).first()
            if trade is None:
                continue

            status_by_reason = {
                "TP": TradeStatus.CLOSED_TP,
                "SL": TradeStatus.CLOSED_SL,
                "MANUAL": TradeStatus.CLOSED_MANUAL,
            }
            trade.status = status_by_reason.get(
                position.close_reason or "", TradeStatus.CLOSED_MANUAL
            )
            trade.exit_price = position.close_price
            trade.closed_at = position.closed_at
            trade.profit = position.profit
            trade.close_reason = position.close_reason
            if trade.profit is not None:
                if trade.profit > 0:
                    trade.result = TradeResult.WIN
                elif trade.profit < 0:
                    trade.result = TradeResult.LOSS
                else:
                    trade.result = TradeResult.BREAKEVEN
            trade.updated_at = datetime.now(UTC)
            self._session.add(trade)
            self._session.commit()
            self._session.refresh(trade)

            self._audit.log(
                AuditEventType.TRADE_CLOSED,
                f"Trade {trade.id} closed ({trade.close_reason}): profit={fmt(trade.profit)}",
                severity=AuditSeverity.INFO,
                metadata={
                    "trade_id": trade.id,
                    "result": trade.result.value if trade.result else None,
                },
            )
            updated.append(trade)
        return updated
