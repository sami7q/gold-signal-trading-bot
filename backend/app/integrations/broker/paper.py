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
            sl, tp = position.stop_loss, position.take_profit
            if position.side is Side.BUY:
                sl_hit = sl is not None and self._bid <= sl
                tp_hit = tp is not None and self._bid >= tp
            else:
                sl_hit = sl is not None and self._bid >= sl
                tp_hit = tp is not None and self._bid <= tp
            if sl_hit and sl is not None:
                self._settle(position, exit_price=sl, reason="SL")
                closed.append(position)
            elif tp_hit and tp is not None:
                self._settle(position, exit_price=tp, reason="TP")
                closed.append(position)
        return closed

    def _settle(self, position: BrokerPosition, *, exit_price: Decimal, reason: str) -> None:
        position.close_price = exit_price
        position.closed_at = datetime.now(UTC)
        position.is_open = False
        position.close_reason = reason
        signed = 1 if position.side is Side.BUY else -1
        position.profit = (
            (exit_price - position.entry_price)
            * position.lot
            * self._symbol_info.contract_size
            * signed
        )
        self._balance += position.profit

    # -- Broker interface ---------------------------------------------------

    def get_account(self) -> BrokerAccount:
        unrealized = sum(
            (self._bid - p.entry_price)
            * p.lot
            * self._symbol_info.contract_size
            * (1 if p.side is Side.BUY else -1)
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
        return self._open_position(request, side=Side.BUY)

    def open_sell(self, request: OpenTradeRequest) -> BrokerExecution:
        return self._open_position(request, side=Side.SELL)

    def _open_position(self, request: OpenTradeRequest, *, side: Side) -> BrokerExecution:
        """Shared by `open_buy`/`open_sell` -- mirror image of each other. A
        BUY fills at the ask (SL below, TP above); a SELL fills at the bid
        (SL above, TP below)."""
        symbol_info = self.get_symbol(request.symbol)
        if not (symbol_info.volume_min <= request.lot <= symbol_info.volume_max):
            return BrokerExecution(success=False, error="Requested lot outside broker volume range")

        entry_price = self.ask if side is Side.BUY else self.bid
        sl = request.stop_loss
        sl_invalid = sl is not None and (
            sl >= entry_price if side is Side.BUY else sl <= entry_price
        )
        if sl_invalid:
            return BrokerExecution(success=False, error=f"Stop Loss invalid for a {side.value}")
        tp_invalid = (
            request.take_profit <= entry_price
            if side is Side.BUY
            else request.take_profit >= entry_price
        )
        if tp_invalid:
            return BrokerExecution(success=False, error=f"Take Profit invalid for a {side.value}")

        position_id = str(self._next_id)
        self._next_id += 1
        position = BrokerPosition(
            position_id=position_id,
            symbol=request.symbol,
            side=side,
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
        # Closing a BUY means selling at the bid; closing a SELL means
        # buying back at the ask -- the same mirror as opening.
        exit_price = self._bid if position.side is Side.BUY else self.ask
        self._settle(position, exit_price=exit_price, reason="MANUAL")
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
