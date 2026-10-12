"""botup.md v4 §6 test_bybit_cross: cross margin (REGULAR_MARGIN) once, leverage per symbol, totalEquity (C-3).
The fake-session tests always run; the demo contract test runs with `pytest -m demo` (demo keys in the keyring)."""

from decimal import Decimal

import pytest

from loco_copy.brokers.bybit import BybitBroker, parse_quote
from tests.test_bybit_adapter import FakeSession


class CrossSession(FakeSession):
    def __init__(self, mode="ISOLATED_MARGIN"):
        super().__init__()
        self.mode = mode

    def get_account_info(self, **kw):
        return self._ok({"marginMode": self.mode})

    def get_wallet_balance(self, **kw):
        return self._ok({"list": [{"totalEquity": "10234.5", "totalAvailableBalance": "8000.1",
                                   "coin": [{"coin": "USDT", "equity": "9000", "walletBalance": "9000",
                                             "totalPositionIM": "0", "totalOrderIM": "0"}]}]})


def test_cross_once_then_leverage_capped_by_the_symbol():
    session = CrossSession()
    b = BybitBroker(api_key="k", api_secret="s", env="demo", session=session, poll_seconds=0)
    b.instruments()
    b.ensure_cross_leverage("HYPEUSDT", 75)  # HYPE max 50
    b.ensure_cross_leverage("HYPEUSDT", 75)  # already set: no second call
    calls = [c for c in session.calls if c[0] in ("set_margin_mode", "set_leverage")]
    assert calls == [("set_margin_mode", {"setMarginMode": "REGULAR_MARGIN"}),
                     ("set_leverage", {"category": "linear", "symbol": "HYPEUSDT", "buyLeverage": "50",
                                       "sellLeverage": "50"})]


def test_total_equity_is_the_account_row_not_the_usdt_coin():
    b = BybitBroker(api_key="k", api_secret="s", env="demo", session=CrossSession("REGULAR_MARGIN"), poll_seconds=0)
    assert b.total_equity() == Decimal("10234.5")
    b.instruments()
    b.ensure_cross_leverage("HYPEUSDT", 3)
    acct = b.account()
    assert acct.equity == Decimal("10234.5") and acct.available == Decimal("8000.1")


def test_market_order_is_one_way_and_reduce_only_only_when_asked():
    session = CrossSession("REGULAR_MARGIN")
    b = BybitBroker(api_key="k", api_secret="s", env="demo", session=session, poll_seconds=0)
    assert b.market_order("HYPEUSDT", -1, Decimal("1.5"), client_id="8bae:HYPE:1:close", reduce_only=False).success
    kw = next(c[1] for c in session.calls if c[0] == "place_order")
    assert kw["side"] == "Sell" and kw["reduceOnly"] is False and kw["positionIdx"] == 0 and kw["qty"] == "1.5"


def test_quotes_fall_back_to_the_mark_when_the_book_is_empty():
    q = parse_quote({"bid1Price": "", "ask1Price": "40.02", "markPrice": "40"})
    assert q is not None and (q.bid, q.ask, q.mark) == (Decimal(40), Decimal("40.02"), Decimal(40))
    assert parse_quote({"markPrice": "0"}) is None


@pytest.mark.demo
def test_demo_cross_contract():
    keyring = pytest.importorskip("keyring")
    key = keyring.get_password("loco-copy-bybit-demo", "api_key")
    secret = keyring.get_password("loco-copy-bybit-demo", "api_secret")
    if not key or not secret:
        pytest.skip("no Bybit demo keys in the keyring")
    b = BybitBroker(api_key=key, api_secret=secret, env="demo")
    assert b.total_equity() > 0
    q = b.quote("HYPEUSDT")
    assert q.bid <= q.ask and q.mark > 0
