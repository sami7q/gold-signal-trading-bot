"""The virtual cross account (botup.md v5 §V5.5 ledger, v4 C-6 liquidation)."""

from decimal import Decimal

from loco_copy.brokers.virtual import VirtualAccount
from loco_copy.models import Quote
from tests.conftest import INSTRUMENTS


def account(prices, fee="0.00055", mmr=None):
    return VirtualAccount(instruments=dict(INSTRUMENTS), quote=lambda s: prices[s], start_equity=Decimal(10000),
                          fee_rate=Decimal(fee), leverage=3, maintenance_rates=mmr or {})


def test_fills_at_the_ask_or_bid_plus_the_taker_fee_and_is_idempotent():
    prices = {"BTCUSDT": Quote(Decimal(99), Decimal(101), Decimal(100))}
    v = account(prices)
    r = v.market_order("BTCUSDT", 1, Decimal(1), client_id="a", reduce_only=False)
    assert r.success and r.avg_price == 101 and r.fee == Decimal(101) * Decimal("0.00055")
    assert v.market_order("BTCUSDT", 1, Decimal(1), client_id="a", reduce_only=False) is r  # same id, same order
    assert v.position("BTCUSDT").qty == 1
    assert v.equity() == 10000 - r.fee + (100 - 101)  # marked at 100
    out = v.market_order("BTCUSDT", -1, Decimal(1), client_id="b", reduce_only=True)
    assert out.avg_price == 99 and v.positions() == []
    assert v.cash == 10000 - r.fee - out.fee - 2 and v.fees_paid == r.fee + out.fee


def test_one_way_netting_and_flip():
    prices = {"HYPEUSDT": Quote(Decimal(40), Decimal(40), Decimal(40))}
    v = account(prices, fee="0")
    v.market_order("HYPEUSDT", 1, Decimal(2), client_id="a", reduce_only=False)
    prices["HYPEUSDT"] = Quote(Decimal(42), Decimal(42), Decimal(42))
    v.market_order("HYPEUSDT", -1, Decimal(3), client_id="b", reduce_only=False)
    pos = v.position("HYPEUSDT")
    assert pos.side == -1 and pos.qty == 1 and pos.entry_price == 42 and v.cash == 10004
    assert not v.market_order("BTCUSDT", 1, Decimal(1), client_id="c", reduce_only=True).success


def test_cross_liquidation_closes_everything_at_the_mark():
    prices = {"BTCUSDT": Quote(Decimal(100), Decimal(100), Decimal(100))}
    v = account(prices, fee="0", mmr={"BTCUSDT": Decimal("0.005")})
    v.market_order("BTCUSDT", 1, Decimal(290), client_id="a", reduce_only=False)  # 29,000 notional
    assert v.mark_all({"BTCUSDT": Quote(Decimal(70), Decimal(70), Decimal(70))}) is None  # equity 1300 > mm 101.5
    assert v.liquidation_distance() > 1
    closed = v.mark_all({"BTCUSDT": Quote(Decimal("65.6"), Decimal("65.6"), Decimal("65.6"))})
    assert closed == [("BTCUSDT", Decimal(290), Decimal("65.6"))]
    assert v.positions() == [] and v.liquidations == 1 and v.cash >= 0


def test_state_roundtrip():
    prices = {"HYPEUSDT": Quote(Decimal(40), Decimal(40), Decimal(40))}
    v = account(prices)
    v.market_order("HYPEUSDT", -1, Decimal(5), client_id="a", reduce_only=False)
    w = account(prices)
    w.import_state(v.export_state())
    assert w.position("HYPEUSDT") == v.position("HYPEUSDT") and w.cash == v.cash and w.equity() == v.equity()
    summary = w.summary()
    assert summary["positions"][0]["side"] == -1 and summary["start_equity"] == "10000"
