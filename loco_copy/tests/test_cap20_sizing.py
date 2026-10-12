"""botup.md v4 §6 test_cap20_sizing: R = K x E0 / W0 with K = 0.8 L, CAP = 0.2 x L x E0, R fixed per trade."""

from decimal import Decimal

import pytest

from loco_copy.constants import Decision
from loco_copy.sizing import cap_notional, copy_ratio
from tests.conftest import F


def test_formulas():
    assert copy_ratio(Decimal("2.4"), Decimal(10000), Decimal(1000000)) == Decimal("0.024")
    assert cap_notional(Decimal(20), 3, Decimal(10000)) == Decimal(6000)  # 0.6 x E0 at 3x
    assert cap_notional(Decimal(20), 5, Decimal(10000)) == Decimal(10000)  # 1.0 x E0 at 5x
    with pytest.raises(ValueError):
        copy_ratio(Decimal(2), Decimal(1), Decimal(0))


@pytest.mark.parametrize(("lev", "k", "cap"), [(3, "2.4", "6000"), (5, "4.0", "10000")])
async def test_open_sizes_from_account_and_leader_equity(cap20, lev, k, cap):
    h = cap20(leverage=lev)
    assert h.settings.k == Decimal(k)
    assert await h.fill(F(0, "HYPE", "B", 40, 1000, 0)) == [Decision.COPIED]
    c = h.copy("HYPE")
    assert c.strategy == "cap20_cross" and c.ratio_r == Decimal(k) * 10000 / 1000000
    assert c.cap_notional == Decimal(cap) and c.qty_open == 1000 * c.ratio_r
    assert h.exchange.position("HYPEUSDT").qty == c.qty_open and c.leader_pos == 1000
    assert h.last_event()["our_px"] == "40" and h.last_event()["pos_after"] == "1000"


async def test_r_and_cap_stay_fixed_for_the_whole_trade(cap20):
    h = cap20()
    await h.fill(F(0, "HYPE", "B", 40, 1000, 0))
    r, cap = h.copy("HYPE").ratio_r, h.copy("HYPE").cap_notional
    h.leader_equity(500000)  # the leader's equity halves, ours grows: neither changes this trade
    h.exchange.cash += 10000
    assert await h.fill(F(1000, "HYPE", "B", 40, 1000, 1000)) == [Decision.ADDED]
    c = h.copy("HYPE")
    assert (c.ratio_r, c.cap_notional) == (r, cap) and c.qty_open == 2000 * r and c.adds_done == 1


async def test_no_fresh_leader_equity_no_trade(cap20):
    h = cap20()
    h.store.set_state("leader_equity:loco", None)
    assert await h.fill(F(0, "HYPE", "B", 40, 1000, 0)) == [Decision.REJECTED]
    assert h.last_event()["rejection"] == "LEADER_EQUITY_UNKNOWN"
    h.leader_equity(1000000)
    assert await h.fill(F(300_000, "BTC", "B", 100000, 1, 0)) == [Decision.COPIED]  # exactly 300 s old: fine
    # 301 s later: older than LEADER_EQUITY_REFRESH_SECONDS (botup.md v4 §4: "<= 300 s old")
    assert await h.fill(F(301_000, "kPEPE", "B", "0.01", 1000000, 0)) == [Decision.REJECTED]
    assert h.last_event()["rejection"] == "LEADER_EQUITY_UNKNOWN" and h.exchange.position("1000PEPEUSDT") is None
