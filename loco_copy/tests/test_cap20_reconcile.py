"""botup.md v4 §6 test_cap20_reconcile: target formula (capped / not capped), threshold, stale snapshot never
moves money (C-4), never opens a trade; missed exits are closed."""

from decimal import Decimal

from loco_copy.cap20 import reconcile_target
from loco_copy.constants import CopyStatus
from loco_copy.models import Copy
from tests.conftest import T0, F


def _snapshot(h, positions, at_ms):
    h.store.set_state("leader_snapshot:loco", {"taken_at_ms": T0 + at_ms, "account_value": "1000000",
                                               "positions": {c: str(p) for c, p in positions.items()},
                                               "entry_px": {}})


def test_target_formula():
    c = Copy(coin="HYPE", symbol="HYPEUSDT", episode_id=1, side=1, status=CopyStatus.OPEN, qty_initial=Decimal(24),
             qty_open=Decimal(24), leader_entry_px=Decimal(40), entry_price=Decimal(40), ratio_r=Decimal("0.024"),
             cap_notional=Decimal(6000))
    assert reconcile_target(c, Decimal(1000), Decimal(40)) == 24  # |szi| x R
    assert reconcile_target(c, Decimal(100000), Decimal(40)) == 150  # CAP / avg
    assert reconcile_target(c, Decimal(0), Decimal(40)) == 0
    c.capped, c.qty_at_cap, c.leader_pos_at_cap = True, Decimal(150), Decimal(8000)
    assert reconcile_target(c, Decimal(4000), Decimal(40)) == 75  # qty_at_cap x |szi| / |szi at cap|


async def _with_extra(h, extra):
    """Our copy holds `extra` more than the target (as if a reduce had been missed)."""
    await h.fill(F(0, "HYPE", "B", 40, 1000, 0))
    c = h.copy("HYPE")
    h.exchange.market_order("HYPEUSDT", 1, Decimal(extra), client_id="drift", reduce_only=False)
    c.qty_open += Decimal(extra)
    c.cost_notional += Decimal(extra) * 40
    h.store.update_copy(c)


async def test_a_fresh_consistent_snapshot_corrects_the_gap(cap20):
    h = cap20()
    await _with_extra(h, 10)  # 34 held, target 24: 400 USDT > max(5, 0.5 % x 10,000 = 50)
    _snapshot(h, {"HYPE": Decimal(1000)}, 1000)
    h.tick(2000)
    await h.recon.run_once()
    assert h.copy("HYPE").qty_open == 24 and h.exchange.position("HYPEUSDT").qty == 24
    assert any(r["type"] == "RECONCILE_CORRECTED" for r in h.store.logs_since(0))


async def test_a_stale_or_inconsistent_snapshot_never_moves_money(cap20):
    h = cap20()
    await _with_extra(h, 10)
    _snapshot(h, {"HYPE": Decimal(1000)}, 0)
    h.tick(91_000)  # older than 90 s
    await h.recon.run_once()
    assert h.copy("HYPE").qty_open == 34
    _snapshot(h, {"HYPE": Decimal(1200)}, 91_000)  # disagrees with the fills we followed
    await h.recon.run_once()
    assert h.copy("HYPE").qty_open == 34


async def test_a_small_gap_is_left_alone(cap20):
    h = cap20()
    await _with_extra(h, 1)  # 40 USDT <= 50 USDT (0.5 % of E0)
    _snapshot(h, {"HYPE": Decimal(1000)}, 1000)
    h.tick(2000)
    await h.recon.run_once()
    assert h.copy("HYPE").qty_open == 25


async def test_reconcile_never_opens_a_trade(cap20):
    h = cap20()
    h.tracker.on_fill(F(0, "HYPE", "B", 40, 1000, 0))  # the leader is in HYPE, we have no copy
    _snapshot(h, {"HYPE": Decimal(1000)}, 0)
    await h.recon.run_once()
    assert h.store.copies() == [] and h.exchange.positions() == []


async def test_a_missed_exit_closes_our_share(cap20):
    h = cap20()
    await h.fill(F(0, "HYPE", "B", 40, 1000, 0))
    h.tracker.on_fill(F(1000, "HYPE", "A", 40, 1000, 1000))  # the close reached the tracker but not the engine
    h.tick(2000)
    await h.recon.run_once()  # first sighting: wait MISSED_EXIT_SECONDS
    assert h.copy("HYPE").qty_open == 24
    h.tick(2000 + 61_000)
    await h.recon.run_once()
    c = h.copy("HYPE")
    assert c.status == CopyStatus.CLOSED_RECONCILED and h.exchange.positions() == []


async def test_an_observed_trade_is_closed_on_the_virtual_ledger(cap20):
    h = cap20(copy_observe_only=True)
    await h.fill(F(0, "HYPE", "B", 40, 1000, 0))
    assert h.virtual.position("HYPEUSDT").qty == 24 and h.exchange.positions() == []
    h.tracker.on_fill(F(1000, "HYPE", "A", 40, 1000, 1000))
    await h.recon.run_once()
    h.tick(70_000)
    await h.recon.run_once()
    c = h.copy("HYPE")
    assert c.status == CopyStatus.OBSERVED_ONLY and c.closed_at is not None and h.virtual.positions() == []
