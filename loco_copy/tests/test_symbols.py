from decimal import Decimal

from loco_copy.symbols import SymbolMapper

LISTED = ["HYPEUSDT", "BTCUSDT", "1000PEPEUSDT", "SHIB1000USDT", "1000BONKUSDT"]


def test_plain_coin():
    m = SymbolMapper(LISTED).map("HYPE")
    assert m and m.symbol == "HYPEUSDT" and m.price_multiplier == 1


def test_k_prefix_maps_to_1000_contract():
    m = SymbolMapper(LISTED).map("kPEPE")
    assert m and m.symbol == "1000PEPEUSDT" and m.price_multiplier == 1
    assert SymbolMapper(LISTED).map("kSHIB").symbol == "SHIB1000USDT"


def test_plain_coin_only_listed_per_1000_gets_a_multiplier():
    m = SymbolMapper(LISTED).map("BONK")
    assert m and m.symbol == "1000BONKUSDT" and m.price_multiplier == Decimal(1000)


def test_not_listed():
    assert SymbolMapper(LISTED).map("PURR") is None
    assert SymbolMapper(LISTED).map("CASHCAT") is None


def test_identity_mode():
    assert SymbolMapper(["HYPE"], identity=True).map("HYPE").symbol == "HYPE"
