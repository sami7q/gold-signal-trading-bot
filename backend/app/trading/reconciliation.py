"""Startup broker/database reconciliation.

Every `Trade` row the database calls `OPEN` is only a claim — the broker is
the actual source of truth for whether a position is still live. A process
restart is exactly the moment those two can drift apart (PaperBroker's
positions are pure in-memory state and vanish on restart; a real MT5
terminal could just as easily close a position, or become unreachable,
while this backend was down). This service is the single place that
re-establishes trust between the two before anything else runs.

The rule is the same one the rest of the codebase follows
(PROJECT_SPEC.md section 32, rule 13 — "any ambiguity that could create a
trade must default to no trade"): if the broker can prove what happened to
a position, use that. If it can't, the trade goes to `TradeStatus.UNKNOWN`
with `close_reason="RECONCILIATION_REQUIRED"` — never a guessed WIN/LOSS,
and never silently left looking "healthy" as `OPEN`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlmodel import Session, select

from app.audit.service import AuditService
from app.core.constants import AuditEventType, AuditSeverity, TradeResult, TradeStatus
from app.core.decimals import fmt
from app.core.logging import get_logger
from app.database.models.trade import Trade
from app.integrations.broker.base import Broker

logger = get_logger(__name__)

_CLOSE_REASON_TO_STATUS = {
    "TP": TradeStatus.CLOSED_TP,
    "SL": TradeStatus.CLOSED_SL,
    "MANUAL": TradeStatus.CLOSED_MANUAL,
}

#: Set on `Trade.close_reason` (not `Trade.result`) when a position can't be
#: confirmed one way or the other — a human needs to look at it.
RECONCILIATION_REQUIRED_REASON = "RECONCILIATION_REQUIRED"


@dataclass(slots=True)
class ReconciliationSummary:
    checked: int = 0
    confirmed_open: int = 0
    resolved_from_broker: int = 0
    marked_reconciliation_required: int = 0
    trade_ids_requiring_attention: list[int] = field(default_factory=list)


class ReconciliationService:
    def __init__(self, session: Session, broker: Broker, audit: AuditService | None = None) -> None:
        self._session = session
        self._broker = broker
        self._audit = audit or AuditService(session)

    def reconcile_open_trades(self) -> ReconciliationSummary:
        statement = select(Trade).where(Trade.status == TradeStatus.OPEN)
        open_trades = list(self._session.exec(statement))

        summary = ReconciliationSummary(checked=len(open_trades))
        self._audit.log(
            AuditEventType.RECONCILIATION_STARTED,
            f"Reconciling {len(open_trades)} OPEN trade(s) against broker '{self._broker.name}'.",
            metadata={"broker": self._broker.name, "trade_count": len(open_trades)},
        )

        for trade in open_trades:
            self._reconcile_one(trade, summary)

        self._audit.log(
            AuditEventType.RECONCILIATION_COMPLETED,
            f"Reconciliation complete: {summary.confirmed_open} confirmed open, "
            f"{summary.resolved_from_broker} resolved from broker data, "
            f"{summary.marked_reconciliation_required} require manual review.",
            severity=(
                AuditSeverity.WARNING
                if summary.marked_reconciliation_required
                else AuditSeverity.INFO
            ),
            metadata={
                "checked": summary.checked,
                "confirmed_open": summary.confirmed_open,
                "resolved_from_broker": summary.resolved_from_broker,
                "marked_reconciliation_required": summary.marked_reconciliation_required,
                "trade_ids_requiring_attention": summary.trade_ids_requiring_attention,
            },
        )
        return summary

    def _reconcile_one(self, trade: Trade, summary: ReconciliationSummary) -> None:
        if trade.broker_position_id is None:
            # An OPEN trade with no broker_position_id should never exist
            # (TradingEngine only sets status=OPEN after the broker confirms
            # and returns a position id) — treat defensively as unresolved
            # rather than assume anything about it.
            self._mark_requires_reconciliation(
                trade, summary, "Trade is OPEN but has no broker_position_id on record."
            )
            return

        try:
            position = self._broker.get_position(trade.broker_position_id)
        except Exception as exc:  # noqa: BLE001 - a broker fault during reconciliation must not crash startup
            logger.exception(
                "Broker raised while reconciling trade %s (position %s)",
                trade.id,
                trade.broker_position_id,
            )
            self._mark_requires_reconciliation(
                trade,
                summary,
                f"Broker query failed during reconciliation: {type(exc).__name__}",
                extra_metadata={
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:500],
                },
            )
            return

        if position is None:
            self._mark_requires_reconciliation(
                trade, summary, "Broker has no record of this position (POSITION_MISSING)."
            )
            return

        if position.is_open:
            self._audit.log(
                AuditEventType.POSITION_CONFIRMED,
                f"Trade {trade.id} confirmed still open on broker "
                f"(position {trade.broker_position_id}).",
                metadata={"trade_id": trade.id, "broker_position_id": trade.broker_position_id},
            )
            summary.confirmed_open += 1
            return

        # The broker has real, definitive data that this position closed —
        # not a guess, so it's safe to resolve properly instead of parking
        # it as UNKNOWN.
        trade.status = _CLOSE_REASON_TO_STATUS.get(
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

        self._audit.log(
            AuditEventType.POSITION_CONFIRMED,
            f"Trade {trade.id} was closed by the broker while this process was down "
            f"(reason={trade.close_reason}, profit={fmt(trade.profit)}) — resolved from "
            "broker data.",
            metadata={"trade_id": trade.id, "result": trade.result.value if trade.result else None},
        )
        summary.resolved_from_broker += 1

    def _mark_requires_reconciliation(
        self,
        trade: Trade,
        summary: ReconciliationSummary,
        reason: str,
        *,
        extra_metadata: dict[str, object] | None = None,
    ) -> None:
        trade.status = TradeStatus.UNKNOWN
        trade.close_reason = RECONCILIATION_REQUIRED_REASON
        trade.updated_at = datetime.now(UTC)
        self._session.add(trade)
        self._session.commit()

        self._audit.log(
            AuditEventType.POSITION_MISSING,
            f"Trade {trade.id}: {reason}",
            severity=AuditSeverity.WARNING,
            metadata={"trade_id": trade.id, **(extra_metadata or {})},
        )
        self._audit.log(
            AuditEventType.RECONCILIATION_REQUIRED,
            f"Trade {trade.id} marked UNKNOWN — requires manual review. It still counts "
            "toward max_open_trades until resolved.",
            severity=AuditSeverity.WARNING,
            metadata={"trade_id": trade.id, "signal_id": trade.signal_id},
        )
        summary.marked_reconciliation_required += 1
        if trade.id is not None:
            summary.trade_ids_requiring_attention.append(trade.id)
