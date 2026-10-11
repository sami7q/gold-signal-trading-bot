from decimal import Decimal

from loco_copy.brokers.base import BrokerError
from loco_copy.constants import CopyStatus, Decision, EventType
from loco_copy.engine import open_client_id
from loco_copy.models import LeaderEvent, OrderResult
from tests.conftest import T0, F


async def test_open_sizes_sets_tp_and_records_latency(harness):
    h = harness()
    assert await h.fill(F(0, "HYPE", "B", 40, 10, 0, received=300)) == [Decision.COPIED]
    c = h.store.get_copy("HYPE", 1)
    assert c.status == CopyStatus.OPEN and c.qty_open == Decimal("500.00") and c.entry_price == Decimal("40")
    assert c.tp_price == Decimal("41.000") and c.latency_detect_ms == 300
    pos = h.paper.position("HYPEUSDT")
    assert pos is not None and pos.take_profit == Decimal("41.000")


async def test_same_event_twice_sends_one_order(harness):
    h = harness()
    events, _ = h.tracker.on_fill(F(0, "HYPE", "B", 40, 10, 0))
    assert await h.engine.handle(events[0]) == Decision.COPIED
    assert await h.engine.handle(events[0]) == Decision.REJECTED  # DUPLICATE_EPISODE
    assert len(h.paper.positions()) == 1
    assert h.store.events_since(0)[-1]["rejection"] == "DUPLICATE_EPISODE"


async def test_failure_is_never_written_as_open(harness):
    h = harness()
    h.paper.market_open = lambda *a, **k: OrderResult(False, k["client_id"], error="rejected by exchange")
    assert await h.fill(F(0, "HYPE", "B", 40, 10, 0)) == [Decision.FAILED]
    c = h.store.get_copy("HYPE", 1)
    assert c.status == CopyStatus.FAILED and c.qty_open == 0
    assert h.store.get_order(open_client_id("HYPE", 1))["status"] == "FAILED"


async def test_timeout_after_submit_is_recovered_from_the_exchange(harness):
    h = harness()
    real_open = h.paper.market_open

    def flaky(*a, **k):
        real_open(*a, **k)  # the order DID go through ...
        raise BrokerError("read timeout")  # ... but we never saw the answer

    h.paper.market_open = flaky
    assert await h.fill(F(0, "HYPE", "B", 40, 10, 0)) == [Decision.COPIED]
    assert h.store.get_copy("HYPE", 1).status == CopyStatus.OPEN


async def test_crash_after_submit_recovered_at_restart(harness):
    h = harness()
    h.tracker.on_fill(F(0, "HYPE", "B", 40, 10, 0))
    # simulate: copy written SUBMITTING + order sent, then the process died before recording the result
    from loco_copy.constants import OrderKind
    from loco_copy.models import Copy

    copy = h.store.insert_copy(Copy(coin="HYPE", symbol="HYPEUSDT", episode_id=1, side=1,
                                    status=CopyStatus.SUBMITTING, qty_initial=Decimal("500"), qty_open=Decimal(0),
                                    leader_entry_px=Decimal("40"), tp_price=Decimal("41"), margin=Decimal("1000")))
    cid = open_client_id("HYPE", 1)
    h.store.insert_order(copy_id=copy.id, client_id=cid, kind=OrderKind.OPEN, requested_qty=Decimal("500"))
    h.paper.market_open("HYPEUSDT", 1, Decimal("500"), client_id=cid, max_slippage=Decimal("0.01"),
                        take_profit=Decimal("41"))
    await h.engine.recover()
    assert h.store.get_copy("HYPE", 1).status == CopyStatus.OPEN
    assert h.store.get_order(cid)["status"] == "FILLED"


async def test_crash_before_submit_marks_failed_at_restart(harness):
    h = harness()
    from loco_copy.models import Copy

    h.store.insert_copy(Copy(coin="HYPE", symbol="HYPEUSDT", episode_id=7, side=1, status=CopyStatus.SUBMITTING,
                             qty_initial=Decimal("500"), qty_open=Decimal(0), leader_entry_px=Decimal("40")))
    await h.engine.recover()
    assert h.store.get_copy("HYPE", 7).status == CopyStatus.FAILED


async def test_reduce_maths_follow_the_same_fraction(harness):
    h = harness()
    await h.fill(F(0, "HYPE", "B", 40, 8, 0))  # we hold 500
    h.paper.set_price("HYPEUSDT", Decimal("40.5"))
    assert await h.fill(F(1000, "HYPE", "A", "40.5", 2, 8)) == [Decision.FOLLOWED]  # Loco -25 %
    c = h.store.get_copy("HYPE", 1)
    assert c.qty_open == Decimal("375.00") and c.status == CopyStatus.OPEN
    assert c.realized_pnl == Decimal("62.5")  # 125 sold x (40.5 - 40); fees are 0 in the harness
    assert h.paper.position("HYPEUSDT").qty == Decimal("375.00")


async def test_close_follows_and_cancels_tp(harness):
    h = harness()
    await h.fill(F(0, "HYPE", "B", 40, 8, 0))
    h.paper.set_price("HYPEUSDT", Decimal("40.2"))
    assert await h.fill(F(1000, "HYPE", "A", "40.2", 8, 8)) == [Decision.FOLLOWED]
    c = h.store.get_copy("HYPE", 1)
    assert c.status == CopyStatus.CLOSED_LEADER and c.qty_open == 0 and c.close_reason == "CLOSE"
    assert h.paper.positions() == []


async def test_flip_closes_then_opens_new_episode_through_the_rules(harness):
    h = harness()
    await h.fill(F(0, "HYPE", "B", 40, 8, 0))
    h.paper.set_price("HYPEUSDT", Decimal("39.9"))
    assert await h.fill(F(1000, "HYPE", "A", "39.9", 12, 8)) == [Decision.FOLLOWED, Decision.COPIED]
    old, new = h.store.get_copy("HYPE", 1), h.store.get_copy("HYPE", 2)
    assert old.status == CopyStatus.CLOSED_LEADER and old.close_reason == "FLIP"
    assert new.status == CopyStatus.OPEN and new.side == -1
    assert h.paper.position("HYPEUSDT").side == -1


async def test_exit_after_exchange_tp_is_a_noop(harness):
    h = harness()
    await h.fill(F(0, "HYPE", "B", 40, 8, 0))
    h.paper.set_price("HYPEUSDT", Decimal("41.1"))  # TP hit on the exchange
    await h.recon.run_once()
    assert h.store.get_copy("HYPE", 1).status == CopyStatus.CLOSED_TP
    assert await h.fill(F(2000, "HYPE", "A", 41, 8, 8)) == [Decision.NO_MATCHING_COPY]


async def test_unlisted_symbol_is_rejected_loudly(harness):
    h = harness()
    assert await h.fill(F(0, "PURR", "B", 1, 10, 0)) == [Decision.REJECTED]
    row = h.store.events_since(0)[-1]
    assert row["rejection"] == "SYMBOL_NOT_LISTED"
    assert h.store.logs_since(0, min_level="WARNING")[-1]["type"] == "OPEN_REJECTED"


async def test_kpepe_mapped_to_1000pepe(harness):
    h = harness()
    assert await h.fill(F(0, "kPEPE", "B", "0.01", 1000, 0)) == [Decision.COPIED]
    assert h.store.get_copy("kPEPE", 1).symbol == "1000PEPEUSDT"


async def test_dashboard_mode_switch_applies_without_restart(harness):
    from loco_copy.config import with_mode_override

    h = harness(copy_observe_only=True)
    assert await h.fill(F(0, "HYPE", "B", 40, 8, 0)) == [Decision.OBSERVED_ONLY]
    h.engine.use_settings(with_mode_override(h.settings, {"copy_enabled": True, "observe_only": False}))
    assert await h.fill(F(10, "BTC", "B", 100000, 1, 0)) == [Decision.COPIED]
    h.engine.use_settings(with_mode_override(h.settings, {"copy_enabled": False, "observe_only": True}))
    assert await h.fill(F(20, "kPEPE", "B", "0.01", 1000, 0)) == [Decision.REJECTED]  # WATCH: DISABLED


async def test_max_open_positions(harness):
    h = harness(max_open_positions=1)
    await h.fill(F(0, "HYPE", "B", 40, 8, 0))
    ev = LeaderEvent(EventType.OPEN, "BTC", 1, 1, Decimal("100000"), T0, T0, 99)
    h.tracker.state("BTC")
    assert await h.engine.handle(ev) == Decision.REJECTED
    assert h.store.events_since(0)[-1]["rejection"] == "MAX_OPEN_POSITIONS"
