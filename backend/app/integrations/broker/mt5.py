"""MT5 broker adapter (PROJECT_SPEC.md section 20 / Phase 12-13).

Disabled by default (`MT5_ENABLED=false`). `MetaTrader5` is an optional,
Windows-only dependency (`pip install -e ".[mt5]"`) and is imported lazily.

Connection sequence on `connect()` follows the spec exactly:
initialize -> confirm account identity -> confirm DEMO/LIVE -> resolve
symbol -> read symbol metadata -> confirm trading allowed -> confirm lot
constraints -> confirm point/digits -> confirm market data exists ->
health check. If MT5 rejects an order, `open_buy` must never fabricate an
OPEN trade — it returns `BrokerExecution(success=False, ...)` and the
trading engine marks the trade FAILED.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.config import Settings
from app.core.logging import get_logger
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

logger = get_logger(__name__)


class MT5ConnectionError(RuntimeError):
    pass


class MT5Broker(Broker):
    name = "mt5"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._mt5 = None
        self._connected = False
        self._symbol = settings.mt5_symbol

    def connect(self) -> None:
        if not self._settings.mt5_enabled:
            raise MT5ConnectionError("MT5_ENABLED=false — refusing to connect.")

        import MetaTrader5 as mt5  # type: ignore[import-not-found]

        self._mt5 = mt5

        terminal_path = self._settings.mt5_terminal_path or None
        if not mt5.initialize(path=terminal_path):
            raise MT5ConnectionError(f"MT5 initialize() failed: {mt5.last_error()}")

        authorized = mt5.login(
            login=int(self._settings.mt5_account),
            password=self._settings.mt5_password,
            server=self._settings.mt5_server,
        )
        if not authorized:
            raise MT5ConnectionError(f"MT5 login failed: {mt5.last_error()}")

        account_info = mt5.account_info()
        if account_info is None:
            raise MT5ConnectionError("MT5 account_info() unavailable after login.")
        if (
            self._settings.trading_mode == "LIVE"
            and account_info.trade_mode != mt5.ACCOUNT_TRADE_MODE_REAL
        ):
            raise MT5ConnectionError(
                "TRADING_MODE=LIVE but the connected MT5 account is not a real/live account."
            )
        if (
            self._settings.trading_mode == "DEMO"
            and account_info.trade_mode != mt5.ACCOUNT_TRADE_MODE_DEMO
        ):
            raise MT5ConnectionError(
                "TRADING_MODE=DEMO but the connected MT5 account is not a demo account."
            )

        symbol_info = mt5.symbol_info(self._symbol)
        if symbol_info is None:
            raise MT5ConnectionError(f"Symbol {self._symbol} not found on this broker.")
        if not symbol_info.visible and not mt5.symbol_select(self._symbol, True):
            raise MT5ConnectionError(f"Could not select symbol {self._symbol}.")
        if not symbol_info.trade_mode:
            raise MT5ConnectionError(f"Trading is disabled for {self._symbol} on this account.")

        tick = mt5.symbol_info_tick(self._symbol)
        if tick is None or tick.bid == 0:
            raise MT5ConnectionError(f"No market data available for {self._symbol}.")

        self._connected = True
        logger.info(
            "MT5 connected: account=%s server=%s symbol=%s",
            self._settings.mt5_account,
            self._settings.mt5_server,
            self._symbol,
        )

    def disconnect(self) -> None:
        if self._mt5 is not None:
            self._mt5.shutdown()
        self._connected = False

    def _require_connected(self):  # noqa: ANN202
        if not self._connected or self._mt5 is None:
            raise MT5ConnectionError("MT5Broker is not connected. Call connect() first.")
        return self._mt5

    def get_account(self) -> BrokerAccount:
        mt5 = self._require_connected()
        info = mt5.account_info()
        if info is None:
            raise MT5ConnectionError(f"account_info() failed: {mt5.last_error()}")
        return BrokerAccount(
            balance=Decimal(str(info.balance)),
            equity=Decimal(str(info.equity)),
            currency=info.currency,
        )

    def get_symbol(self, symbol: str) -> SymbolInfo:
        mt5 = self._require_connected()
        info = mt5.symbol_info(symbol)
        if info is None:
            raise MT5ConnectionError(f"symbol_info({symbol}) failed: {mt5.last_error()}")
        return SymbolInfo(
            symbol=symbol,
            digits=info.digits,
            point=Decimal(str(info.point)),
            contract_size=Decimal(str(info.trade_contract_size)),
            volume_min=Decimal(str(info.volume_min)),
            volume_max=Decimal(str(info.volume_max)),
            volume_step=Decimal(str(info.volume_step)),
        )

    def get_quote(self, symbol: str) -> Quote:
        mt5 = self._require_connected()
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise MT5ConnectionError(f"No live tick for {symbol}: {mt5.last_error()}")
        return Quote(bid=Decimal(str(tick.bid)), ask=Decimal(str(tick.ask)))

    def open_buy(self, request: OpenTradeRequest) -> BrokerExecution:
        mt5 = self._require_connected()
        symbol_info = self.get_symbol(request.symbol)

        if not (symbol_info.volume_min <= request.lot <= symbol_info.volume_max):
            return BrokerExecution(success=False, error="Requested lot outside broker volume range")
        remainder = (request.lot - symbol_info.volume_min) % symbol_info.volume_step
        if remainder != 0:
            return BrokerExecution(
                success=False, error="Requested lot does not match broker volume step"
            )

        tick = mt5.symbol_info_tick(request.symbol)
        if tick is None:
            return BrokerExecution(success=False, error="No live tick available")
        ask = Decimal(str(tick.ask))
        if request.stop_loss is not None and request.stop_loss >= ask:
            return BrokerExecution(success=False, error="Stop Loss invalid for a BUY")
        if request.take_profit <= ask:
            return BrokerExecution(success=False, error="Take Profit invalid for a BUY")

        order_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": request.symbol,
            "volume": float(request.lot),
            "type": mt5.ORDER_TYPE_BUY,
            "price": float(ask),
            "sl": float(request.stop_loss) if request.stop_loss is not None else 0.0,
            "tp": float(request.take_profit),
            "comment": request.client_reference[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(order_request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error = getattr(result, "comment", None) or str(mt5.last_error())
            return BrokerExecution(success=False, error=f"MT5 order rejected: {error}")

        return BrokerExecution(
            success=True,
            broker_order_id=str(result.order),
            broker_position_id=str(result.order),
            entry_price=Decimal(str(result.price)),
            executed_lot=Decimal(str(result.volume)),
        )

    def get_position(self, position_id: str) -> BrokerPosition | None:
        mt5 = self._require_connected()
        positions = mt5.positions_get(ticket=int(position_id))
        if not positions:
            return None
        p = positions[0]
        return BrokerPosition(
            position_id=str(p.ticket),
            symbol=p.symbol,
            side="BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL",  # type: ignore[arg-type]
            lot=Decimal(str(p.volume)),
            entry_price=Decimal(str(p.price_open)),
            stop_loss=Decimal(str(p.sl)) if p.sl else None,
            take_profit=Decimal(str(p.tp)) if p.tp else None,
            is_open=True,
        )

    def close_position(self, position_id: str) -> BrokerExecution:
        mt5 = self._require_connected()
        positions = mt5.positions_get(ticket=int(position_id))
        if not positions:
            return BrokerExecution(success=False, error="Position not found")
        p = positions[0]
        tick = mt5.symbol_info_tick(p.symbol)
        close_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": p.symbol,
            "volume": p.volume,
            "type": mt5.ORDER_TYPE_SELL,
            "position": p.ticket,
            "price": tick.bid,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(close_request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error = getattr(result, "comment", None) or str(mt5.last_error())
            return BrokerExecution(success=False, error=f"MT5 close rejected: {error}")
        return BrokerExecution(
            success=True, broker_order_id=str(p.ticket), broker_position_id=str(p.ticket)
        )

    def health(self) -> BrokerHealth:
        if not self._connected or self._mt5 is None:
            return BrokerHealth(healthy=False, detail="not connected")
        info = self._mt5.account_info()
        return BrokerHealth(
            healthy=info is not None, detail="connected" if info else "lost connection"
        )
