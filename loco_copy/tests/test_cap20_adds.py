"""botup.md v4 §6 test_cap20_adds: adds follow dq x R; pending below the minimum; stop at the cap."""

from decimal import Decimal

from loco_copy.constants import Decision
from tests.conftest import F


async def test_adds_follow_the_leader_until_the_margin_cap(cap20):
    h = cap20()  # R = 0.024, CAP = 6000 USDT = 150 HYPE at 40
    assert await h.fill(F(0, "HYPE", "B", 40, 1000, 0)) == [Decision.COPIED]
    assert await h.fill(F(1000, "HYPE", "B", 40, 2000, 1000)) == [Decision.ADDED]
    assert h.copy("HYPE").qty_open == 72
    assert await h.fill(F(2000, "HYPE", "B", 40, 5000, 3000)) == [Decision.ADDED]  # 120 wanted, 78 room
    c = h.copy("HYPE")
    assert c.qty_open == 150 and c.capped and c.qty_at_cap == 150 and c.leader_pos_at_cap == 8000
    assert c.cost_notional == 6000 and "limited_by=cap" in h.last_event()["detail"]
    assert await h.fill(F(3000, "HYPE", "B", 40, 1000, 8000)) == [Decision.ADD_CAPPED]
    assert h.copy("HYPE").qty_open == 150 and h.exchange.position("HYPEUSDT").qty == 150
    # capped: reduces are still followed (9000 -> 4500 = half) ...
    assert await h.fill(F(4000, "HYPE", "A", 40, 4500, 9000)) == [Decision.REDUCED]
    assert h.copy("HYPE").qty_open == 75
    # ... and the trade stays capped: a later add is ignored (as in the research)
    assert await h.fill(F(5000, "HYPE", "B", 40, 1000, 4500)) == [Decision.ADD_CAPPED]
    assert await h.fill(F(6000, "HYPE", "A", 40, 5500, 5500)) == [Decision.CLOSED]
    assert h.exchange.positions() == [] and h.copy("HYPE").closed_at is not None


async def test_small_adds_accumulate_until_the_minimum_order(cap20):
    h = cap20(min_order_usdt=Decimal(10))
    await h.fill(F(0, "HYPE", "B", 40, 1000, 0))  # 24 HYPE
    assert await h.fill(F(1000, "HYPE", "B", 40, 5, 1000)) == [Decision.ADD_PENDING]  # 0.12 = 4.8 USDT
    assert h.copy("HYPE").pending_qty == Decimal("0.12") and h.last_event()["our_px"] == "40"
    assert await h.fill(F(2000, "HYPE", "B", 40, 5, 1005)) == [Decision.ADD_PENDING]  # 0.24 = 9.6 USDT
    assert await h.fill(F(3000, "HYPE", "B", 40, 5, 1010)) == [Decision.ADDED]  # 0.36 = 14.4 USDT
    c = h.copy("HYPE")
    assert c.qty_open == Decimal("24.36") and c.pending_qty == 0 and c.adds_done == 1


async def test_an_open_below_the_minimum_waits_and_a_close_ends_it_cleanly(cap20):
    h = cap20(min_order_usdt=Decimal(10))
    assert await h.fill(F(0, "HYPE", "B", 40, 5, 0)) == [Decision.ADD_PENDING]
    c = h.copy("HYPE")
    assert c.qty_open == 0 and c.pending_qty == Decimal("0.12") and c.closed_at is None
    assert await h.fill(F(1000, "HYPE", "B", 40, 10, 5)) == [Decision.ADDED]  # 0.36 -> the first order
    assert h.copy("HYPE").qty_open == Decimal("0.36") and h.copy("HYPE").entry_price == 40
    assert await h.fill(F(2000, "HYPE", "A", 40, 15, 15)) == [Decision.CLOSED]
    assert h.exchange.positions() == []


async def test_a_trade_that_never_filled_closes_with_nothing_held(cap20):
    h = cap20(min_order_usdt=Decimal(10))
    await h.fill(F(0, "HYPE", "B", 40, 5, 0))
    assert await h.fill(F(1000, "HYPE", "A", 40, 5, 5)) == [Decision.CLOSED]
    c = h.copy("HYPE")
    assert c.closed_at is not None and c.close_reason == "CLOSE (nothing held)" and h.exchange.positions() == []


async def test_adds_are_limited_by_free_margin(cap20):
    h = cap20()
    h.exchange.cash = Decimal(1000)  # E0 = 1000: R = 0.0024, CAP = 600 USDT
    await h.fill(F(0, "HYPE", "B", 40, 100000, 0))  # wants 240 HYPE = 9600 USDT -> the cap: 15 HYPE
    c = h.copy("HYPE")
    assert c.qty_open == 15 and c.capped
    h2 = cap20()
    h2.exchange.cash = Decimal(1000)
    h2.exchange.market_order("BTCUSDT", 1, Decimal("0.025"), client_id="other", reduce_only=False)  # 2500 / 3
    await h2.fill(F(0, "HYPE", "B", 40, 100000, 0))  # free margin (1000 - 833.33) x 3 = 500 USDT < cap 600
    c2 = h2.copy("HYPE")
    assert c2.qty_open == Decimal("12.5") and "limited_by=margin" in h2.last_event()["detail"]
