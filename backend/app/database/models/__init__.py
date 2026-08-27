"""Import every table model so `SQLModel.metadata` is fully populated.

Anything that calls `SQLModel.metadata.create_all(...)` or runs Alembic
autogenerate must import this package first.
"""

from app.database.models.archived_signal import ArchivedSignal
from app.database.models.audit_event import AuditEvent
from app.database.models.incoming_message import IncomingMessage
from app.database.models.signal import Signal
from app.database.models.signal_message import SignalMessage
from app.database.models.trade import Trade
from app.database.models.trading_settings import TradingSettings

__all__ = [
    "ArchivedSignal",
    "AuditEvent",
    "IncomingMessage",
    "Signal",
    "SignalMessage",
    "Trade",
    "TradingSettings",
]
