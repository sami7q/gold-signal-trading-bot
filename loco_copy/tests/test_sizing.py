from decimal import Decimal

from loco_copy.models import InstrumentInfo
from loco_copy.sizing import floor_to_step, liquidation_price, reduce_qty, size_order, take_profit_price

INST = InstrumentInfo("HYPEUSDT", Decimal("0.01"), Decimal("0.1"), Decimal("0.001"), 50)


def size(**kw):
    args = dict(equity=Decimal("10000"), mark=Decimal("40"), side=1, instrument=INST, risk_per_trade=Decimal("0.10"),
                leverage=20, tp_roi=Decimal("0.50"), max_notional=None)
    args.update(kw)
    return size_order(**args)


def test_ten_percent_margin_at_20x():
    s = size()
    assert s.margin == Decimal("1000")
    assert s.qty == Decimal("500.00")  # 1000 x 20 / 40
    assert s.notional == Decimal("20000.00")


def test_qty_floored_to_step():
    s = size(mark=Decimal("41.7"))
    assert s.qty == Decimal("479.61")  # 20000/41.7 = 479.616... -> floor
    assert s.qty * Decimal("41.7") <= 20000


def test_notional_cap():
    s = size(max_notional=Decimal("5000"))
    assert s.capped and s.qty == Decimal("125.00")


def test_tp_long_and_short_rounded_to_tick():
    assert take_profit_price(Decimal("40"), 1, Decimal("0.5"), 20, Decimal("0.001")) == Decimal("41.000")
    assert take_profit_price(Decimal("40"), -1, Decimal("0.5"), 20, Decimal("0.001")) == Decimal("39.000")
    assert take_profit_price(Decimal("40.1234"), 1, Decimal("0.5"), 20, Decimal("0.01")) == Decimal("41.13")


def test_liquidation_about_minus_4_5_percent():
    assert liquidation_price(Decimal("100"), 1, 20, Decimal("0.005")) == Decimal("95.500")
    assert liquidation_price(Decimal("100"), -1, 20, Decimal("0.005")) == Decimal("104.500")


def test_reduce_same_fraction_and_min_qty_closes_all():
    assert reduce_qty(Decimal("10"), Decimal("0.5"), INST) == Decimal("5.00")
    assert reduce_qty(Decimal("1.0"), Decimal("0.95"), INST) == Decimal("1.0")  # remainder 0.05 < min 0.1


def test_floor_to_step():
    assert floor_to_step(Decimal("1.239"), Decimal("0.01")) == Decimal("1.23")
