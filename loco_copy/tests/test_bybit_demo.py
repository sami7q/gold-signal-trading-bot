"""Contract tests against the real Bybit DEMO account. Skipped unless demo keys are in the OS keyring
(`loco-copy set-key bybit-demo`). Run explicitly:  pytest -m demo

They place one tiny HYPEUSDT order and close it -- demo money only, never live.
"""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

pytestmark = pytest.mark.demo


@pytest.fixture(scope="module")
def broker():
    keyring = pytest.importorskip("keyring")
    key = keyring.get_password("loco-copy-bybit-demo", "api_key")
    secret = keyring.get_password("loco-copy-bybit-demo", "api_secret")
    if not key or not secret:
        pytest.skip("no Bybit demo keys in the keyring")
    from loco_copy.brokers.bybit import BybitBroker

    return BybitBroker(api_key=key, api_secret=secret, env="demo")


def test_health_account_instruments(broker):
    assert broker.health().healthy
    assert broker.account().equity >= 0
    inst = broker.instruments()
    assert "HYPEUSDT" in inst and "1000PEPEUSDT" in inst


def test_open_tp_reduce_close_roundtrip(broker):
    sym = "HYPEUSDT"
    inst = broker.instruments()[sym]
    broker.ensure_isolated_leverage(sym, 20)
    mark = broker.mark_price(sym)
    qty = max(inst.min_qty, (Decimal(6) / mark / inst.qty_step).to_integral_value() * inst.qty_step)
    tag = f"loco:TEST:{int(time.time())}"
    tp = (mark * Decimal("1.025") / inst.tick_size).to_integral_value() * inst.tick_size
    res = broker.market_open(sym, 1, qty, client_id=f"{tag}:open", max_slippage=Decimal("0.005"), take_profit=tp)
    assert res.success, res.error
    assert broker.order_by_client_id(f"{tag}:open").success
    pos = broker.position(sym)
    assert pos is not None and pos.take_profit is not None
    broker.cancel_take_profit(sym)
    out = broker.market_reduce(sym, 1, pos.qty, client_id=f"{tag}:close")
    assert out.success, out.error
    assert broker.position(sym) is None
