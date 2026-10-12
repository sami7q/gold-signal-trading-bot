"""botup.md v4 §6 test_cap20_reduce_close_flip."""

from decimal import Decimal

from loco_copy.constants import CopyStatus, Decision
from tests.conftest import F


async def test_reduce_is_proportional_and_scales_pending(cap20):
    h = cap20(min_order_usdt=Decimal(10))
    await h.fill(F(0, "HYPE", "B", 40, 1000, 0))  # 24
    await h.fill(F(1000, "HYPE", "B", 40, 5, 1000))  # pending 0.12
    assert await h.fill(F(2000, "HYPE", "A", 40, 500, 1005)) == [Decision.REDUCED]  # keep 505/1005
    c = h.copy("HYPE")
    assert c.qty_open == Decimal("12.06")  # 24 - floor(24 x 500/1005 = 11.94..)
    assert c.pending_qty == Decimal("0.12") * 505 / 1005 and c.leader_pos == 505
    assert h.exchange.position("HYPEUSDT").qty == Decimal("12.06")
    assert c.cost_notional == Decimal("12.06") * 40  # the cap room grows back with what was sold


async def test_close_is_100_percent_and_books_the_pnl(cap20):
    h = cap20(fee="0.0005")
    await h.fill(F(0, "HYPE", "B", 40, 1000, 0))  # 24 @ 40
    h.price("HYPEUSDT", 44)
    assert await h.fill(F(1000, "HYPE", "A", 44, 1000, 1000)) == [Decision.CLOSED]
    c = h.copy("HYPE")
    fees = Decimal(24) * 40 * Decimal("0.0005") + Decimal(24) * 44 * Decimal("0.0005")
    assert c.status == CopyStatus.CLOSED_LEADER and c.qty_open == 0 and c.close_reason == "CLOSE"
    assert c.realized_pnl == 24 * 4 - fees and c.fees == fees
    assert h.exchange.positions() == [] and h.exchange.equity() == 10000 + c.realized_pnl


async def test_flip_closes_then_opens_a_new_trade_with_a_new_r(cap20):
    h = cap20()
    await h.fill(F(0, "HYPE", "B", 40, 1000, 0))  # long 24, R 0.024
    h.leader_equity(2000000)
    assert await h.fill(F(1000, "HYPE", "A", 40, 1500, 1000)) == [Decision.CLOSED, Decision.COPIED]
    old, new = h.copy("HYPE", 1), h.copy("HYPE", 2)
    assert old.status == CopyStatus.CLOSED_LEADER and old.close_reason == "FLIP"
    assert new.side == -1 and new.ratio_r == Decimal("0.012") and new.qty_open == 6
    pos = h.exchange.position("HYPEUSDT")
    assert pos.side == -1 and pos.qty == 6


async def test_exit_events_record_our_price(cap20):
    h = cap20()
    await h.fill(F(0, "HYPE", "B", 40, 1000, 0))
    h.price("HYPEUSDT", 41, spread="0.02")
    await h.fill(F(1000, "HYPE", "A", 41, 1000, 1000))
    ev = h.last_event()
    assert ev["type"] == "CLOSE" and ev["our_px"] == "40.99" and ev["pos_before"] == "1000"


async def test_a_v3_first_fill_copy_is_still_followed_to_its_end(cap20):
    from loco_copy.models import Copy

    h = cap20()
    h.tracker.on_fill(F(0, "HYPE", "B", 40, 1000, 0))  # the leader opened under v3 ...
    old = Copy(coin="HYPE", symbol="HYPEUSDT", episode_id=1, side=1, status=CopyStatus.OPEN,
               qty_initial=Decimal(10), qty_open=Decimal(10), leader_entry_px=Decimal(40), entry_price=Decimal(40))
    h.store.insert_copy(old)  # ... and v3 copied his first fill
    h.exchange.market_order("HYPEUSDT", 1, Decimal(10), client_id="v3", reduce_only=False)
    assert await h.fill(F(1000, "HYPE", "B", 40, 1000, 1000)) == [Decision.IGNORED_ADD]
    assert await h.fill(F(2000, "HYPE", "A", 40, 2000, 2000)) == [Decision.FOLLOWED]
    assert h.copy("HYPE").status == CopyStatus.CLOSED_LEADER and h.exchange.positions() == []
