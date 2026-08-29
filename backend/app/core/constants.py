"""Shared enums and constants used across the trading domain.

Kept in one place so the parser, state machine, rules engine, and database
models all agree on the same vocabulary (see PROJECT_SPEC.md sections 6-13).
"""

from __future__ import annotations

from enum import StrEnum

GOLD_ALIASES: frozenset[str] = frozenset(
    {"GOLD", "XAU", "XAUUSD", "XAU/USD", "XAU-USD", "الذهب"}
)
CANONICAL_GOLD_SYMBOL = "XAUUSD"

STRATEGY_VERSION = "v1"


class TradingMode(StrEnum):
    SIMULATION = "SIMULATION"
    DEMO = "DEMO"
    LIVE = "LIVE"


class SignalKind(StrEnum):
    """The exact directional/order-type variant detected by the parser.

    Combines direction and order type (PROJECT_SPEC.md section 6.2) because
    the parser must distinguish all six at classification time. Storage
    splits this back into `side` + `signal_type` (section 13.2).
    """

    BUY = "BUY"
    SELL = "SELL"
    BUY_LIMIT = "BUY_LIMIT"
    SELL_LIMIT = "SELL_LIMIT"
    BUY_STOP = "BUY_STOP"
    SELL_STOP = "SELL_STOP"


#: Kinds the v1 strategy is allowed to execute. Everything else is
#: classified and stored but never sent to the trading engine. LIMIT/STOP
#: (pending/conditional orders) are deliberately excluded -- they need a
#: separate pending-order lifecycle (fill/expire/cancel monitoring) the
#: broker adapters and Trade state machine don't have yet.
EXECUTABLE_KINDS: frozenset[SignalKind] = frozenset({SignalKind.BUY, SignalKind.SELL})


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


_KIND_TO_SIDE: dict[SignalKind, Side] = {
    SignalKind.BUY: Side.BUY,
    SignalKind.BUY_LIMIT: Side.BUY,
    SignalKind.BUY_STOP: Side.BUY,
    SignalKind.SELL: Side.SELL,
    SignalKind.SELL_LIMIT: Side.SELL,
    SignalKind.SELL_STOP: Side.SELL,
}

_KIND_TO_ORDER_TYPE: dict[SignalKind, OrderType] = {
    SignalKind.BUY: OrderType.MARKET,
    SignalKind.SELL: OrderType.MARKET,
    SignalKind.BUY_LIMIT: OrderType.LIMIT,
    SignalKind.SELL_LIMIT: OrderType.LIMIT,
    SignalKind.BUY_STOP: OrderType.STOP,
    SignalKind.SELL_STOP: OrderType.STOP,
}


def kind_to_side(kind: SignalKind) -> Side:
    return _KIND_TO_SIDE[kind]


def kind_to_order_type(kind: SignalKind) -> OrderType:
    return _KIND_TO_ORDER_TYPE[kind]


class SignalStatus(StrEnum):
    RECEIVED = "RECEIVED"
    CLASSIFIED = "CLASSIFIED"
    WAITING_FOR_DETAILS = "WAITING_FOR_DETAILS"
    READY = "READY"
    VALIDATING = "VALIDATING"
    REJECTED = "REJECTED"
    EXECUTION_PENDING = "EXECUTION_PENDING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    IGNORED = "IGNORED"
    DUPLICATE = "DUPLICATE"


class MessageRole(StrEnum):
    ENTRY = "ENTRY"
    DETAILS = "DETAILS"
    STOP_LOSS = "STOP_LOSS"
    TARGETS = "TARGETS"
    UPDATE = "UPDATE"
    RESULT = "RESULT"
    OTHER = "OTHER"


class MessageClassification(StrEnum):
    ENTRY_SIGNAL = "ENTRY_SIGNAL"
    SIGNAL_DETAILS = "SIGNAL_DETAILS"
    TARGET_UPDATE = "TARGET_UPDATE"
    RESULT_UPDATE = "RESULT_UPDATE"
    IGNORED = "IGNORED"


class TradeStatus(StrEnum):
    CREATED = "CREATED"
    SUBMITTING = "SUBMITTING"
    OPEN = "OPEN"
    CLOSED_TP = "CLOSED_TP"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_MANUAL = "CLOSED_MANUAL"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


#: Statuses that represent a trade no longer open.
CLOSED_TRADE_STATUSES: frozenset[TradeStatus] = frozenset(
    {TradeStatus.CLOSED_TP, TradeStatus.CLOSED_SL, TradeStatus.CLOSED_MANUAL}
)

#: Statuses that must still block a new trade from opening (max_open_trades)
#: and must never be counted as a win/loss. `UNKNOWN` means reconciliation
#: could not confirm the position is actually gone — see
#: `app/trading/reconciliation.py`. Ambiguity here must default to treating
#: the slot as still consumed, never to silently freeing it up
#: (PROJECT_SPEC.md section 32, rule 13).
OPEN_TRADE_STATUSES: frozenset[TradeStatus] = frozenset({TradeStatus.OPEN, TradeStatus.UNKNOWN})


class TradeResult(StrEnum):
    WIN = "WIN"
    LOSS = "LOSS"
    BREAKEVEN = "BREAKEVEN"


class RejectionReason(StrEnum):
    AUTO_TRADING_DISABLED = "AUTO_TRADING_DISABLED"
    BUY_DISABLED = "BUY_DISABLED"
    SELL_DISABLED = "SELL_DISABLED"
    STOP_LOSS_REQUIRED = "STOP_LOSS_REQUIRED"
    STOP_LOSS_INVALID = "STOP_LOSS_INVALID"
    #: The channel's own stated Stop Loss is farther from the current
    #: market price than `TradingSettings.max_channel_stop_pips` allows.
    #: Added after historical backtesting (see VIP_FOREX_ANALYSIS.md)
    #: showed signals with an unusually wide channel Stop Loss are both
    #: lower win-rate AND carry outsized losses when wrong -- filtering
    #: them out turns the strategy's historical net result strongly
    #: positive. Purely a signal-quality filter, not a risk cap: the SL
    #: actually sent to the broker is still the channel's own number.
    STOP_LOSS_TOO_WIDE = "STOP_LOSS_TOO_WIDE"
    DUPLICATE_SIGNAL = "DUPLICATE_SIGNAL"
    MAX_OPEN_TRADES_REACHED = "MAX_OPEN_TRADES_REACHED"
    BROKER_UNAVAILABLE = "BROKER_UNAVAILABLE"
    INVALID_LOT_SIZE = "INVALID_LOT_SIZE"
    INVALID_TARGET = "INVALID_TARGET"
    UNSUPPORTED_SYMBOL = "UNSUPPORTED_SYMBOL"
    UNSUPPORTED_SIGNAL_TYPE = "UNSUPPORTED_SIGNAL_TYPE"
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"


class AuditEventType(StrEnum):
    SIGNAL_RECEIVED = "SIGNAL_RECEIVED"
    SIGNAL_READY = "SIGNAL_READY"
    SIGNAL_REJECTED = "SIGNAL_REJECTED"
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
    SIGNAL_DUPLICATE = "SIGNAL_DUPLICATE"
    #: A details/Stop-Loss message could not be uniquely attached to one
    #: signal (multiple were waiting and reply_to_message_id didn't resolve
    #: it) — the message is stored but deliberately left unattached, and no
    #: signal advances. See PROJECT_SPEC.md section 32, rule 13.
    AMBIGUOUS_SIGNAL_DETAILS = "AMBIGUOUS_SIGNAL_DETAILS"
    TRADE_SUBMISSION_STARTED = "TRADE_SUBMISSION_STARTED"
    TRADE_OPENED = "TRADE_OPENED"
    TRADE_CLOSED = "TRADE_CLOSED"
    TRADE_FAILED = "TRADE_FAILED"
    BROKER_ERROR = "BROKER_ERROR"
    SETTINGS_CHANGED = "SETTINGS_CHANGED"
    SERVICE_STARTED = "SERVICE_STARTED"
    SERVICE_STOPPED = "SERVICE_STOPPED"
    #: Startup broker/DB reconciliation (app/trading/reconciliation.py).
    RECONCILIATION_STARTED = "RECONCILIATION_STARTED"
    POSITION_CONFIRMED = "POSITION_CONFIRMED"
    POSITION_MISSING = "POSITION_MISSING"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    RECONCILIATION_COMPLETED = "RECONCILIATION_COMPLETED"
    #: Telegram connection lifecycle (app/integrations/signal_source/telegram.py).
    TELEGRAM_CONNECTING = "TELEGRAM_CONNECTING"
    TELEGRAM_CONNECTED = "TELEGRAM_CONNECTED"
    TELEGRAM_DISCONNECTED = "TELEGRAM_DISCONNECTED"
    TELEGRAM_RECONNECTING = "TELEGRAM_RECONNECTING"
    TELEGRAM_ERROR = "TELEGRAM_ERROR"
    TELEGRAM_MESSAGE_RECEIVED = "TELEGRAM_MESSAGE_RECEIVED"
    #: A signal reached READY from a real Telegram message while
    #: TELEGRAM_OBSERVE_ONLY=true — deliberately not sent to the trading
    #: engine (PROJECT_SPEC.md Phase 11 section 17).
    SIGNAL_OBSERVED_ONLY = "SIGNAL_OBSERVED_ONLY"
    #: The channel never sent a Stop Loss before the completion timeout, and
    #: TradingSettings.auto_sl_fallback_pips was set -- a fallback Stop Loss
    #: was computed from the live quote and the signal proceeded to
    #: execution instead of expiring. See app/trading/service.py.
    AUTO_STOP_LOSS_APPLIED = "AUTO_STOP_LOSS_APPLIED"


class AuditSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ErrorCode(StrEnum):
    TELEGRAM_SOURCE_DISCONNECTED = "TELEGRAM_SOURCE_DISCONNECTED"
    SIGNAL_PARSE_FAILED = "SIGNAL_PARSE_FAILED"
    SIGNAL_EXPIRED = "SIGNAL_EXPIRED"
    BROKER_CONNECTION_FAILED = "BROKER_CONNECTION_FAILED"
    ORDER_REJECTED = "ORDER_REJECTED"
    DATABASE_WRITE_FAILED = "DATABASE_WRITE_FAILED"
    DUPLICATE_EXECUTION_BLOCKED = "DUPLICATE_EXECUTION_BLOCKED"
