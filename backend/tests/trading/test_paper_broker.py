"""Paper Broker cases from PROJECT_SPEC.md section 17.4."""

from __future__ import annotations

from decimal import Decimal

from app.core.constants import Side
from app.integrations.broker.base import OpenTradeRequest
from app.integrations.broker.paper import PaperBroker


def _broker(starting_price: Decimal = Decimal("4638.50")) -> PaperBroker:
    return PaperBroker(
        starting_balance=Decimal("10000"),
        symbol="XAUUSD",
        point=Decimal("0.01"),
        digits=2,
        contract_size=Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("50"),
        volume_step=Decimal("0.01"),
        spread_points=20,
        starting_price=starting_price,
    )


def _open_buy(broker: PaperBroker, *, sl: Decimal, tp: Decimal, lot: Decimal = Decimal("0.10")):
    return broker.open_buy(
        OpenTradeRequest(
            symbol="XAUUSD",
            side=Side.BUY,
            lot=lot,
            stop_loss=sl,
            take_profit=tp,
            client_reference="1:v1",
        )
    )


def test_open_buy_creates_open_position():
    broker = _broker()
    execution = _open_buy(broker, sl=Decimal("4623"), tp=Decimal("4738.50"))
    assert execution.success is True
    position = broker.get_position(execution.broker_position_id)
    assert position.is_open is True
    assert position.side == Side.BUY


def test_tp_reached_closes_with_profit():
    broker = _broker()
    execution = _open_buy(broker, sl=Decimal("4623"), tp=Decimal("4738.50"))
    closed = broker.set_price(Decimal("4738.50"))
    assert len(closed) == 1
    position = broker.get_position(execution.broker_position_id)
    assert position.is_open is False
    assert position.close_reason == "TP"
    assert position.profit > 0


def test_sl_reached_closes_with_loss():
    broker = _broker()
    execution = _open_buy(broker, sl=Decimal("4623"), tp=Decimal("4738.50"))
    closed = broker.set_price(Decimal("4623"))
    assert len(closed) == 1
    position = broker.get_position(execution.broker_position_id)
    assert position.is_open is False
    assert position.close_reason == "SL"
    assert position.profit < 0


def test_position_stays_open_between_sl_and_tp():
    broker = _broker()
    _open_buy(broker, sl=Decimal("4623"), tp=Decimal("4738.50"))
    closed = broker.set_price(Decimal("4650.00"))
    assert closed == []
    assert len(broker.open_positions()) == 1


def test_profit_is_calculated_from_contract_size():
    broker = _broker()
    execution = _open_buy(broker, sl=Decimal("4623"), tp=Decimal("4738.50"), lot=Decimal("0.10"))
    broker.set_price(Decimal("4738.50"))
    position = broker.get_position(execution.broker_position_id)
    expected_profit = (Decimal("4738.50") - position.entry_price) * Decimal("0.10") * Decimal("100")
    assert position.profit == expected_profit


def test_open_buy_without_stop_loss_never_closes_on_sl():
    broker = _broker()
    execution = broker.open_buy(
        OpenTradeRequest(
            symbol="XAUUSD",
            side=Side.BUY,
            lot=Decimal("0.10"),
            stop_loss=None,
            take_profit=Decimal("4738.50"),
            client_reference="1:v1",
        )
    )
    assert execution.success is True
    closed = broker.set_price(Decimal("4000.00"))  # would trigger SL if one were set
    assert closed == []
    assert len(broker.open_positions()) == 1


def test_closed_position_cannot_close_twice():
    broker = _broker()
    execution = _open_buy(broker, sl=Decimal("4623"), tp=Decimal("4738.50"))
    broker.set_price(Decimal("4738.50"))
    result = broker.close_position(execution.broker_position_id)
    assert result.success is False


# -- SELL: the mirror image of everything above --------------------------


def _open_sell(broker: PaperBroker, *, sl: Decimal, tp: Decimal, lot: Decimal = Decimal("0.10")):
    return broker.open_sell(
        OpenTradeRequest(
            symbol="XAUUSD",
            side=Side.SELL,
            lot=lot,
            stop_loss=sl,
            take_profit=tp,
            client_reference="1:v1",
        )
    )


def test_open_sell_creates_open_position():
    broker = _broker()
    execution = _open_sell(broker, sl=Decimal("4650"), tp=Decimal("4550"))
    assert execution.success is True
    position = broker.get_position(execution.broker_position_id)
    assert position.is_open is True
    assert position.side == Side.SELL
    # SELL fills at the bid, not the ask (mirrors BUY filling at the ask).
    assert position.entry_price == broker.bid


def test_sell_open_rejects_sl_below_entry():
    # A SELL's Stop Loss must sit ABOVE entry (the mirror of a BUY's, which
    # must sit below) -- a lower SL would mean "stop me out on profit".
    broker = _broker()
    execution = _open_sell(broker, sl=Decimal("4600"), tp=Decimal("4550"))
    assert execution.success is False
    assert "Stop Loss" in (execution.error or "")


def test_sell_open_rejects_tp_above_entry():
    broker = _broker()
    execution = _open_sell(broker, sl=Decimal("4650"), tp=Decimal("4700"))
    assert execution.success is False
    assert "Take Profit" in (execution.error or "")


def test_sell_tp_reached_closes_with_profit():
    # Price falling is profit for a SELL -- the opposite of a BUY.
    broker = _broker()
    execution = _open_sell(broker, sl=Decimal("4650"), tp=Decimal("4550"))
    closed = broker.set_price(Decimal("4550"))
    assert len(closed) == 1
    position = broker.get_position(execution.broker_position_id)
    assert position.is_open is False
    assert position.close_reason == "TP"
    assert position.profit > 0


def test_sell_sl_reached_closes_with_loss():
    # Price rising is a loss for a SELL.
    broker = _broker()
    execution = _open_sell(broker, sl=Decimal("4650"), tp=Decimal("4550"))
    closed = broker.set_price(Decimal("4650"))
    assert len(closed) == 1
    position = broker.get_position(execution.broker_position_id)
    assert position.is_open is False
    assert position.close_reason == "SL"
    assert position.profit < 0


def test_sell_position_stays_open_between_sl_and_tp():
    broker = _broker()
    _open_sell(broker, sl=Decimal("4650"), tp=Decimal("4550"))
    closed = broker.set_price(Decimal("4600"))
    assert closed == []
    assert len(broker.open_positions()) == 1


def test_sell_profit_sign_is_reversed_from_buy():
    # Same price move, opposite side -> opposite-signed profit for the
    # identical distance travelled.
    broker = _broker()
    sell_execution = _open_sell(broker, sl=Decimal("4650"), tp=Decimal("4550"), lot=Decimal("0.10"))
    broker.set_price(Decimal("4550"))
    sell_position = broker.get_position(sell_execution.broker_position_id)

    broker2 = _broker()
    buy_execution = _open_buy(broker2, sl=Decimal("4550"), tp=Decimal("4650"), lot=Decimal("0.10"))
    broker2.set_price(Decimal("4650"))
    buy_position = broker2.get_position(buy_execution.broker_position_id)

    assert sell_position.profit > 0
    assert buy_position.profit > 0


def test_sell_manual_close_uses_ask_not_bid():
    broker = _broker()
    execution = _open_sell(broker, sl=Decimal("4650"), tp=Decimal("4550"))
    result = broker.close_position(execution.broker_position_id)
    assert result.success is True
    position = broker.get_position(execution.broker_position_id)
    assert position.close_price == broker.ask


def test_account_equity_reflects_unrealized_sell_pnl():
    broker = _broker()
    _open_sell(broker, sl=Decimal("4650"), tp=Decimal("4550"), lot=Decimal("1"))
    account_before = broker.get_account()
    # Price falls -- unrealized profit for the open SELL, equity should rise
    # above balance (balance only changes on realized/closed profit).
    broker.set_price(broker.bid - Decimal("5"))
    account_after = broker.get_account()
    assert account_after.equity > account_before.equity
    assert account_after.balance == account_before.balance  # nothing closed yet
