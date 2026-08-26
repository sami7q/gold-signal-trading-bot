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
from app.core.constants import AuditEventType, AuditSeverity, TradeResult, TradeStatus
from app.core.decimals import fmt
from app.database.models.signal import Signal
from app.database.models.trade import Trade
from app.integrations.broker.base import Broker, BrokerPosition
from app.settings.service import SettingsService
from app.signals.models import IncomingMessageData
from app.signals.service import IngestResult, SignalService
from app.trading.engine import TradingEngine


class TradingService:
    def __init__(
        self,
        session: Session,
        broker: Broker,
        audit: AuditService | None = None,
        *,
        signal_completion_timeout_seconds: int | None = None,
    ) -> None:
        self._session = session
        self._broker = broker
        self._audit = audit or AuditService(session)
        self._settings_service = SettingsService(session, self._audit)

        timeout = signal_completion_timeout_seconds
        if timeout is None:
            timeout = self._settings_service.get_active().signal_completion_timeout_seconds

        self._signal_service = SignalService(
            session, self._audit, signal_completion_timeout_seconds=timeout
        )
        self._engine = TradingEngine(session, broker, self._audit)

    # -- inbound messages -----------------------------------------------------

    def process_incoming_message(self, data: IncomingMessageData) -> IngestResult:
        result = self._signal_service.ingest_message(data)
        if result.became_ready and result.signal is not None:
            if self._is_observed_only(data):
                self._audit.log(
                    AuditEventType.SIGNAL_OBSERVED_ONLY,
                    f"Signal {result.signal.id} reached READY from a real Telegram message "
                    "but TELEGRAM_OBSERVE_ONLY=true — not sent to the trading engine.",
                    severity=AuditSeverity.INFO,
                    metadata={"signal_id": result.signal.id, "source": data.source},
                )
            else:
                settings = self._settings_service.get_active()
                self._engine.execute(result.signal, settings)
                self._session.refresh(result.signal)
        return result

    @staticmethod
    def _is_observed_only(data: IncomingMessageData) -> bool:
        """Phase 11 safety gate (PROJECT_SPEC.md section 17): real Telegram
        messages still flow through the parser and state machine so the
        channel's actual grammar can be validated, but never reach
        `TradingEngine.execute` while this is on. Mock/simulation messages
        are unaffected — an operator testing via the dashboard always wants
        full execution."""
        return data.source == "telegram" and get_settings().telegram_observe_only

    def sweep_expired_signals(self) -> list[Signal]:
        return self._signal_service.expire_stale_signals()

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
