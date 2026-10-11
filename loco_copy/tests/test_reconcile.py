from decimal import Decimal

from loco_copy.constants import CopyStatus
from loco_copy.models import ClosedTrade, Copy, LeaderSnapshot
from loco_copy.reconcile import classify_close
from tests.conftest import T0, F


async def test_missed_close_is_auto_closed_after_60s(harness):
    h = harness()
    await h.fill(F(0, "HYPE", "B", 40, 8, 0))
    # Loco's close never reached us (WS gap); two snapshots confirm he is flat
    h.tracker.on_snapshot(LeaderSnapshot({}, {}, Decimal(0), T0 + 10_000))
    h.tracker.on_snapshot(LeaderSnapshot({}, {}, Decimal(0), T0 + 30_000))
    h.tick(31_000)
    await h.recon.run_once()
    assert h.store.get_copy("HYPE", 1).status == CopyStatus.OPEN  # not yet: waits MISSED_EXIT_SECONDS
    h.tick(92_000)
    await h.recon.run_once()
    c = h.store.get_copy("HYPE", 1)
    assert c.status == CopyStatus.CLOSED_RECONCILED and h.paper.positions() == []
    assert any(r["type"] == "MISSED_EXIT_AUTO_CLOSED" for r in h.store.logs_since(0))


async def test_open_copy_while_loco_still_in_the_episode_is_left_alone(harness):
    h = harness()
    await h.fill(F(0, "HYPE", "B", 40, 8, 0))
    h.tick(500_000)
    await h.recon.run_once()
    assert h.store.get_copy("HYPE", 1).status == CopyStatus.OPEN


async def test_orphan_position_flagged_never_closed(harness):
    h = harness()
    h.paper.market_open("BTCUSDT", 1, Decimal("0.01"), client_id="manual", max_slippage=Decimal("0.01"),
                        take_profit=None)
    await h.recon.run_once()
    assert any(r["type"] == "ORPHAN_POSITION" and r["level"] == "CRITICAL" for r in h.store.logs_since(0))
    assert h.paper.position("BTCUSDT") is not None


async def test_tp_and_liquidation_resolution(harness):
    h = harness()
    await h.fill(F(0, "HYPE", "B", 40, 8, 0))
    await h.fill(F(10, "BTC", "B", 100000, 1, 0))
    h.paper.set_price("HYPEUSDT", Decimal("41.5"))  # TP 41
    h.paper.set_price("BTCUSDT", Decimal("95000"))  # liq ~95500
    await h.recon.run_once()
    assert h.store.get_copy("HYPE", 1).status == CopyStatus.CLOSED_TP
    btc = h.store.get_copy("BTC", 1)
    assert btc.status == CopyStatus.CLOSED_LIQ and btc.realized_pnl < 0


async def test_exchange_close_counts_the_opening_fee_once(harness):
    # gross (41 - 40) x 500 = 500, opening fee 20, closing fee 20.5 -> the account made 459.5
    for bybit_style in (False, True):
        h = harness()
        h.paper._fee = Decimal("0.001")
        await h.fill(F(0, "HYPE", "B", 40, 8, 0))
        h.paper.set_price("HYPEUSDT", Decimal("41"))  # TP 41: the exchange closes on its own
        if bybit_style:  # Bybit's closedPnl nets BOTH fees and reports the opening one separately
            row = ClosedTrade("HYPEUSDT", 1, Decimal(500), Decimal(41), Decimal("459.5"), T0 + 1, False,
                              open_fee=Decimal(20))
            h.paper.closed_trades = lambda _symbol, _since, row=row: [row]
        await h.recon.run_once()
        c = h.store.get_copy("HYPE", 1)
        assert c.status == CopyStatus.CLOSED_TP and c.realized_pnl == Decimal("459.5"), bybit_style


def test_classify_manual_and_unknown():
    c = Copy(coin="X", symbol="XUSDT", episode_id=1, side=1, status="OPEN", qty_initial=Decimal(1),
             qty_open=Decimal(1), leader_entry_px=Decimal(100), tp_price=Decimal("102.5"))
    assert classify_close(c, [])[0] == CopyStatus.UNKNOWN
    manual = ClosedTrade("XUSDT", 1, Decimal(1), Decimal(101), Decimal(1), T0, False)
    assert classify_close(c, [manual])[0] == CopyStatus.CLOSED_MANUAL
    tp = ClosedTrade("XUSDT", 1, Decimal(1), Decimal("102.4"), Decimal(2), T0, False)
    assert classify_close(c, [tp])[0] == CopyStatus.CLOSED_TP
