"""Broker interface (botup.md §5.3). Calls are synchronous; the async app runs them via asyncio.to_thread.
One-way mode: at most one position per symbol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from loco_copy.models import Account, ClosedTrade, Health, InstrumentInfo, OrderResult, Position


class BrokerError(RuntimeError):
    """A broker call failed in a way the caller must treat as 'outcome unknown' unless stated otherwise."""


class Broker(ABC):
    name: str

    @abstractmethod
    def instruments(self) -> dict[str, InstrumentInfo]: ...

    @abstractmethod
    def account(self) -> Account: ...

    @abstractmethod
    def mark_price(self, symbol: str) -> Decimal: ...

    @abstractmethod
    def ensure_isolated_leverage(self, symbol: str, leverage: int) -> None: ...

    @abstractmethod
    def market_open(self, symbol: str, side: int, qty: Decimal, *, client_id: str, max_slippage: Decimal,
                    take_profit: Decimal | None) -> OrderResult: ...

    @abstractmethod
    def market_reduce(self, symbol: str, position_side: int, qty: Decimal, *, client_id: str) -> OrderResult: ...

    @abstractmethod
    def set_take_profit(self, symbol: str, price: Decimal) -> None: ...

    @abstractmethod
    def cancel_take_profit(self, symbol: str) -> None: ...

    @abstractmethod
    def position(self, symbol: str) -> Position | None: ...

    @abstractmethod
    def positions(self) -> list[Position]: ...

    @abstractmethod
    def order_by_client_id(self, client_id: str) -> OrderResult | None: ...

    @abstractmethod
    def closed_trades(self, symbol: str, since_ms: int) -> list[ClosedTrade]: ...

    @abstractmethod
    def health(self) -> Health: ...
