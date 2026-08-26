"""Paper Broker (PROJECT_SPEC.md section 9).

A fully self-contained in-memory simulation: account balance/equity, a
single simulated Gold quote (bid + configurable spread), BUY execution, and
TP/SL-triggered closing. It does not need to reproduce MT5 exactly, only to
exercise the same interface faithfully enough to validate the business logic
(rules engine, trading engine, statistics) before real money is involved.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.core.constants import Side
from app.integrations.broker.base import (
    Broker,
    BrokerAccount,
    BrokerExecution,
    BrokerHealth,
    BrokerPosition,
    OpenTradeRequest,
    Quote,
    SymbolInfo,
)


class PaperBroker(Broker):
    name = "paper"

    def __init__(
        self,
        *,
        starting_balance: Decimal,
        symbol: str,
        point: Decimal,
        digits: int,
        contract_size: Decimal,
        volume_min: Decimal,
        volume_max: Decimal,
        volume_step: Decimal,
        spread_points: int,
        starting_price: Decimal = Decimal("2000.00"),
    ) -> None:
        self._starting_balance = Decimal(starting_balance)
        self._balance = self._starting_balance
        self._symbol_info = SymbolInfo(
            symbol=symbol,
            digits=digits,
            point=Decimal(point),
            contract_size=Decimal(contract_size),
            volume_min=Decimal(volume_min),
            volume_max=Decimal(volume_max),
            volume_step=Decimal(volume_step),
        )
        self._spread = Decimal(spread_points) * self._symbol_info.point
        self._bid = Decimal(starting_price)
        self._positions: dict[str, BrokerPosition] = {}
        self._next_id = 1

    # -- quotes -----------------------------------------------------------

    @property
    def symbol(self) -> str:
        return self._symbol_info.symbol

    @property
    def bid(self) -> Decimal:
        return self._bid

    @property
    def ask(self) -> Decimal:
        return self._bid + self._spread

    def set_price(self, bid: Decimal) -> list[BrokerPosition]:
        """Set the simulated bid price and settle any position whose TP/SL was crossed."""
        self._bid = Decimal(bid)
        return self._evaluate_positions()

    def move_price(self, delta: Decimal) -> list[BrokerPosition]:
        return self.set_price(self._bid + Decimal(delta))

    def _evaluate_positions(self) -> list[BrokerPosition]:
        closed: list[BrokerPosition] = []
        for position in self._positions.values():
            if not position.is_open:
                continue
            if position.stop_loss is not None and self._bid <= position.stop_loss:
                self._settle(position, exit_price=position.stop_loss, reason="SL")
                closed.append(position)
            elif position.take_profit is not None and self._bid >= position.take_profit:
                self._settle(position, exit_price=position.take_profit, reason="TP")
                closed.append(position)
        return closed

    def _settle(self, position: BrokerPosition, *, exit_price: Decimal, reason: str) -> None:
        position.close_price = exit_price
        position.closed_at = datetime.now(UTC)
        position.is_open = False
        position.close_reason = reason
        position.profit = (
            (exit_price - position.entry_price) * position.lot * (self._symbol_info.contract_size)
        )
        self._balance += position.profit

    # -- Broker interface ---------------------------------------------------

    def get_account(self) -> BrokerAccount:
        unrealized = sum(
            (self._bid - p.entry_price) * p.lot * self._symbol_info.contract_size
            for p in self._positions.values()
            if p.is_open
        ) or Decimal("0")
        return BrokerAccount(balance=self._balance, equity=self._balance + unrealized)

    def get_symbol(self, symbol: str) -> SymbolInfo:
        if symbol != self._symbol_info.symbol:
            raise ValueError(f"Unsupported symbol for paper broker: {symbol}")
        return self._symbol_info

    def get_quote(self, symbol: str) -> Quote:
        self.get_symbol(symbol)  # validates the symbol
        return Quote(bid=self._bid, ask=self.ask)

    def open_buy(self, request: OpenTradeRequest) -> BrokerExecution:
        symbol_info = self.get_symbol(request.symbol)
        if not (symbol_info.volume_min <= request.lot <= symbol_info.volume_max):
            return BrokerExecution(success=False, error="Requested lot outside broker volume range")
        if request.stop_loss is not None and request.stop_loss >= self.ask:
            return BrokerExecution(success=False, error="Stop Loss must be below entry for a BUY")
        if request.take_profit <= self.ask:
            return BrokerExecution(success=False, error="Take Profit must be above entry for a BUY")

        position_id = str(self._next_id)
        self._next_id += 1
        entry_price = self.ask
        position = BrokerPosition(
            position_id=position_id,
            symbol=request.symbol,
            side=Side.BUY,
            lot=request.lot,
            entry_price=entry_price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            is_open=True,
        )
        self._positions[position_id] = position
        return BrokerExecution(
            success=True,
            broker_order_id=position_id,
            broker_position_id=position_id,
            entry_price=entry_price,
            executed_lot=request.lot,
            opened_at=datetime.now(UTC),
        )

    def get_position(self, position_id: str) -> BrokerPosition | None:
        return self._positions.get(position_id)

    def close_position(self, position_id: str) -> BrokerExecution:
        position = self._positions.get(position_id)
        if position is None:
            return BrokerExecution(success=False, error="Position not found")
        if not position.is_open:
            return BrokerExecution(success=False, error="Position already closed")
        self._settle(position, exit_price=self._bid, reason="MANUAL")
        return BrokerExecution(
            success=True,
            broker_order_id=position.position_id,
            broker_position_id=position.position_id,
            entry_price=position.entry_price,
            executed_lot=position.lot,
        )

    def health(self) -> BrokerHealth:
        return BrokerHealth(healthy=True, detail="paper broker is always available")

    # -- simulation controls (dashboard Simulation page) ---------------------

    def reset(self) -> None:
        self._balance = self._starting_balance
        self._positions.clear()
        self._next_id = 1

    def open_positions(self) -> list[BrokerPosition]:
        return [p for p in self._positions.values() if p.is_open]
