"""Bybit adapter against a fake pybit session: request shapes and response parsing (no network)."""

from decimal import Decimal

import pytest

from loco_copy.brokers.bybit import BybitBroker, link_id, parse_instruments

INSTR = [{"symbol": "HYPEUSDT", "status": "Trading", "quoteCoin": "USDT", "contractType": "LinearPerpetual",
          "lotSizeFilter": {"qtyStep": "0.01", "minOrderQty": "0.01", "minNotionalValue": "5"},
          "priceFilter": {"tickSize": "0.001"}, "leverageFilter": {"maxLeverage": "50.00"}},
         {"symbol": "OLDUSDT", "status": "Closed", "quoteCoin": "USDT", "contractType": "LinearPerpetual",
          "lotSizeFilter": {"qtyStep": "1", "minOrderQty": "1"}, "priceFilter": {"tickSize": "0.1"},
          "leverageFilter": {"maxLeverage": "10"}}]


class FakeSession:
    def __init__(self):
        self.calls = []
        self.orders = {}

    def _ok(self, result):
        return {"retCode": 0, "retMsg": "OK", "result": result}

    def get_instruments_info(self, **kw):
        return self._ok({"list": INSTR, "nextPageCursor": ""})

    def get_tickers(self, **kw):
        return self._ok({"list": [{"symbol": kw["symbol"], "markPrice": "40.000"}]})

    def get_account_info(self, **kw):
        return self._ok({"marginMode": "REGULAR_MARGIN"})

    def set_margin_mode(self, **kw):
        self.calls.append(("set_margin_mode", kw))
        return self._ok({})

    def set_leverage(self, **kw):
        self.calls.append(("set_leverage", kw))
        return self._ok({})

    def place_order(self, **kw):
        self.calls.append(("place_order", kw))
        self.orders[kw["orderLinkId"]] = {"orderId": "1", "orderStatus": "Filled", "cumExecQty": kw["qty"],
                                          "avgPrice": "40.010", "cumExecFee": "0.5", "updatedTime": "1790000000123"}
        return self._ok({"orderId": "1", "orderLinkId": kw["orderLinkId"]})

    def get_open_orders(self, **kw):
        o = self.orders.get(kw.get("orderLinkId"))
        return self._ok({"list": [o] if o else []})

    def get_order_history(self, **kw):
        return self._ok({"list": []})

    def get_wallet_balance(self, **kw):
        return self._ok({"list": [{"coin": [{"coin": "USDT", "equity": "10000", "walletBalance": "10000",
                                             "totalPositionIM": "1500", "totalOrderIM": "0"}]}]})

    def get_positions(self, **kw):
        return self._ok({"list": [{"symbol": "HYPEUSDT", "side": "Buy", "size": "100", "avgPrice": "40",
                                   "takeProfit": "41"}, {"symbol": "X", "side": "", "size": "0"}],
                         "nextPageCursor": ""})

    def get_closed_pnl(self, **kw):
        return self._ok({"list": [{"symbol": "HYPEUSDT", "side": "Sell", "closedSize": "100",
                                   "avgExitPrice": "38.2", "closedPnl": "-200", "execType": "BustTrade",
                                   "updatedTime": "1790000999000"}]})

    def set_trading_stop(self, **kw):
        self.calls.append(("set_trading_stop", kw))
        return self._ok({})

    def get_server_time(self, **kw):
        return self._ok({"timeSecond": "1"})


@pytest.fixture
def broker():
    return BybitBroker(api_key="k", api_secret="s", env="demo", session=FakeSession(), poll_seconds=0)


def test_instruments_parsed_and_closed_ones_dropped():
    inst = parse_instruments(INSTR)
    assert list(inst) == ["HYPEUSDT"]
    assert inst["HYPEUSDT"].qty_step == Decimal("0.01") and inst["HYPEUSDT"].max_leverage == 50


def test_link_id_is_deterministic_and_valid():
    a = link_id("loco:kPEPE:123:reduce:999999999999999")
    assert a == link_id("loco:kPEPE:123:reduce:999999999999999") and len(a) <= 36
    assert all(c.isalnum() or c in "-_" for c in a)


def test_isolated_margin_then_leverage(broker):
    broker.ensure_isolated_leverage("HYPEUSDT", 20)
    names = [c[0] for c in broker._s.calls]
    assert names == ["set_margin_mode", "set_leverage"]
    assert broker._s.calls[0][1]["setMarginMode"] == "ISOLATED_MARGIN"
    assert broker._s.calls[1][1]["buyLeverage"] == "20"


def test_open_is_capped_limit_ioc_with_full_tp(broker):
    res = broker.market_open("HYPEUSDT", 1, Decimal("100"), client_id="loco:HYPE:1:open",
                             max_slippage=Decimal("0.005"), take_profit=Decimal("41.000"))
    kw = broker._s.calls[-1][1]
    assert kw["orderType"] == "Limit" and kw["timeInForce"] == "IOC" and kw["side"] == "Buy"
    assert Decimal(kw["price"]) == Decimal("40.200")  # 40 x 1.005, never worse than that
    assert kw["takeProfit"] == "41.000" and kw["tpslMode"] == "Full" and kw["positionIdx"] == 0
    assert res.success and res.filled_qty == Decimal("100") and res.avg_price == Decimal("40.010")


def test_reduce_is_market_reduce_only(broker):
    broker.market_reduce("HYPEUSDT", 1, Decimal("50"), client_id="loco:HYPE:1:close")
    kw = broker._s.calls[-1][1]
    assert kw["orderType"] == "Market" and kw["reduceOnly"] is True and kw["side"] == "Sell"


def test_account_positions_closed_pnl(broker):
    a = broker.account()
    assert a.equity == Decimal("10000") and a.available == Decimal("8500")
    assert [(p.symbol, p.side, p.qty, p.take_profit) for p in broker.positions()] == [
        ("HYPEUSDT", 1, Decimal("100"), Decimal("41"))]
    ct = broker.closed_trades("HYPEUSDT", 0)[0]
    assert ct.is_liquidation and ct.side == 1 and ct.realized_pnl == Decimal("-200")


def test_cancel_tp_sends_zero(broker):
    broker.cancel_take_profit("HYPEUSDT")
    assert broker._s.calls[-1] == ("set_trading_stop", {"category": "linear", "symbol": "HYPEUSDT",
                                                        "takeProfit": "0", "tpslMode": "Full",
                                                        "tpTriggerBy": "LastPrice", "positionIdx": 0})
