"""Per-broker Gold naming (2026-09-07).

The parser normalises every channel message to the canonical `XAUUSD`, but
that is an internal identity, not a name a broker owes us. XM lists Gold as
`GOLD`; asking it for `XAUUSD` fails, and the rules engine can only report
that as BROKER_UNAVAILABLE -- which is exactly what happened live: every
signal was rejected on the XM account while the same signal filled on Axi
seconds earlier. `MT5Account.symbol` carries the per-broker name and is now
what the broker is actually asked for.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.constants import CANONICAL_GOLD_SYMBOL
from app.integrations.broker.base import SymbolInfo
from app.trading import pips
from app.trading.engine import broker_symbol_for


def _symbol_info(name: str) -> SymbolInfo:
    return SymbolInfo(
        symbol=name,
        digits=2,
        point=Decimal("0.01"),
        contract_size=Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("50"),
        volume_step=Decimal("0.01"),
    )


def test_the_processes_own_env_symbol_wins(session, seed_user, account_factory):
    """`MT5_SYMBOL` is the only one of the three that is proven: connect()
    resolves it, selects it and refuses to start without a live tick for
    it. The account row is operator-typed and can be stale -- on the day
    this was written the XM row still said XAUUSD while its worker's env
    had long said GOLD, and every one of its signals was rejected."""
    account = account_factory(session, seed_user, label="XM", mt5_login="777020")
    account.symbol = CANONICAL_GOLD_SYMBOL
    session.add(account)
    session.commit()

    assert broker_symbol_for(session, account.id, CANONICAL_GOLD_SYMBOL, "GOLD") == "GOLD"


def test_broker_symbol_falls_back_to_the_account(session, seed_user, account_factory):
    account = account_factory(session, seed_user, label="XM", mt5_login="777022")
    account.symbol = "GOLD"
    session.add(account)
    session.commit()

    assert broker_symbol_for(session, account.id, CANONICAL_GOLD_SYMBOL) == "GOLD"
    assert broker_symbol_for(session, account.id, CANONICAL_GOLD_SYMBOL, "") == "GOLD"


def test_falls_back_to_the_canonical_symbol_when_the_account_has_none(
    session, seed_user, account_factory
):
    """An account row with a blank symbol must not silently ask the broker
    for an empty string -- the canonical name is the safe default, and is
    what every account has had until now."""
    account = account_factory(session, seed_user, label="Blank", mt5_login="777021")
    account.symbol = ""
    session.add(account)
    session.commit()

    assert broker_symbol_for(session, account.id, CANONICAL_GOLD_SYMBOL) == CANONICAL_GOLD_SYMBOL
    assert broker_symbol_for(session, 999_999, CANONICAL_GOLD_SYMBOL) == CANONICAL_GOLD_SYMBOL


def test_a_brokers_own_name_for_gold_keeps_the_gold_pip_size():
    """The 2-digit Gold convention (1 pip = 10 points = $0.10) hangs on
    recognising the symbol as Gold. Matching only `XAUUSD` made every pip
    on a `GOLD`-naming broker ten times too small -- a 60-pip target would
    have been $0.60 of price instead of $6.00."""
    for name in ("XAUUSD", "GOLD", "GOLD#", "XAUUSD.r", "xauusd"):
        assert pips.is_gold_symbol(name), name
        assert pips.pip_size(_symbol_info(name)) == Decimal("0.10")


def test_a_non_gold_symbol_keeps_the_plain_point_size():
    assert not pips.is_gold_symbol("EURUSD")
    assert pips.pip_size(_symbol_info("EURUSD")) == Decimal("0.01")
