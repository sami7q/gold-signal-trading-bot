"""Broker adapter interface (PROJECT_SPEC.md section 5.2).

Synchronous by design (the official `MetaTrader5` Python package is a sync
C-binding — there is no async MT5 client to wrap), which also keeps it
consistent with the rest of the backend. The trading engine only ever talks
to this interface, never to `PaperBroker`/`MT5Broker` directly by type.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.core.constants import Side


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    symbol: str
    digits: int
    point: Decimal
    contract_size: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    balance: Decimal
    equity: Decimal
    currency: str = "USD"


@dataclass(frozen=True, slots=True)
class OpenTradeRequest:
    symbol: str
    side: Side
    lot: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal
    #: Idempotency key (== Trade.execution_key). A real MT5 adapter should
    #: pass this through as the order comment so a duplicate submission is
    #: detectable even after an application restart.
    client_reference: str


@dataclass(frozen=True, slots=True)
class BrokerExecution:
    success: bool
    broker_order_id: str | None = None
    broker_position_id: str | None = None
    entry_price: Decimal | None = None
    executed_lot: Decimal | None = None
    opened_at: datetime | None = None
    error: str | None = None


@dataclass(slots=True)
class BrokerPosition:
    position_id: str
    symbol: str
    side: Side
    lot: Decimal
    entry_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    is_open: bool
    close_price: Decimal | None = None
    closed_at: datetime | None = None
    profit: Decimal | None = None
    close_reason: str | None = None  # "TP" | "SL" | "MANUAL"


@dataclass(frozen=True, slots=True)
class BrokerHealth:
    healthy: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Quote:
    bid: Decimal
    ask: Decimal


class Broker(ABC):
    name: str

    @abstractmethod
    def get_account(self) -> BrokerAccount: ...

    @abstractmethod
    def get_symbol(self, symbol: str) -> SymbolInfo: ...

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote: ...

    @abstractmethod
    def open_buy(self, request: OpenTradeRequest) -> BrokerExecution: ...

    @abstractmethod
    def get_position(self, position_id: str) -> BrokerPosition | None: ...

    @abstractmethod
    def close_position(self, position_id: str) -> BrokerExecution: ...

    @abstractmethod
    def health(self) -> BrokerHealth: ...
