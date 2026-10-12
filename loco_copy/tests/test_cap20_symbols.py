"""botup.md v4 §6 test_cap20_symbols: xyz / spot / # ignored; kPEPE -> 1000PEPEUSDT without conversion (C-1);
an unlisted coin skips the whole trade."""

from decimal import Decimal

import pytest

from loco_copy.constants import Decision
from loco_copy.models import InstrumentInfo
from loco_copy.symbols import SymbolMapper
from tests.conftest import F


@pytest.mark.parametrize("coin", ["xyz:TSLA", "@107", "#123", "PURR/USDC"])
async def test_non_perp_markets_never_trade(cap20, coin):
    h = cap20()
    assert await h.fill(F(0, coin, "B", 10, 100, 0)) == []
    assert h.store.copies() == [] and h.exchange.positions() == []


async def test_kpepe_maps_to_1000pepe_with_no_conversion(cap20):
    h = cap20()
    assert await h.fill(F(0, "kPEPE", "B", "0.01", 1000000, 0)) == [Decision.COPIED]
    c = h.copy("kPEPE")
    assert c.symbol == "1000PEPEUSDT" and c.leader_entry_px == Decimal("0.01") and c.qty_open == 24000


async def test_a_1000x_contract_converts_size_and_price(cap20):
    h = cap20()
    h.engine.instruments["1000BONKUSDT"] = InstrumentInfo("1000BONKUSDT", Decimal(1), Decimal(1),
                                                          Decimal("0.000001"), 50)
    h.engine._mapper = SymbolMapper(h.engine.instruments)
    h.price("1000BONKUSDT", "0.02")
    assert await h.fill(F(0, "BONK", "B", "0.00002", 100000000, 0)) == [Decision.COPIED]
    c = h.copy("BONK")  # 100M BONK x 0.024 = 2.4M BONK = 2400 contracts of 1000 BONK (48 USDT)
    assert c.symbol == "1000BONKUSDT" and c.qty_open == 2400 and c.leader_entry_px == Decimal("0.02")


async def test_an_unlisted_coin_skips_the_whole_trade(cap20):
    h = cap20()
    assert await h.fill(F(0, "FOO", "B", 1, 1000, 0)) == [Decision.REJECTED]
    assert h.last_event()["rejection"] == "SYMBOL_NOT_LISTED"
    assert await h.fill(F(1000, "FOO", "B", 1, 1000, 1000)) == [Decision.NO_MATCHING_COPY]
    assert await h.fill(F(2000, "FOO", "A", 1, 2000, 2000)) == [Decision.NO_MATCHING_COPY]
    assert h.exchange.positions() == []
