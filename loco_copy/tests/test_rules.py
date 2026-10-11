from dataclasses import replace
from decimal import Decimal

import pytest

from loco_copy.constants import CopyStatus, Decision, Rejection
from loco_copy.models import Account, InstrumentInfo
from loco_copy.rules import BrokerContext, PreContext, broker_checks, pre_broker_checks
from loco_copy.sizing import Sizing
from loco_copy.symbols import SymbolMap
from tests.conftest import T0, F

INST = InstrumentInfo("HYPEUSDT", Decimal("0.01"), Decimal("0.01"), Decimal("0.001"), 50)
PRE = PreContext(copy_enabled=True, risk_block=None, main_perp=True, synced=True, side=1, allow_long=True,
                 allow_short=True, coin="HYPE", allowlist=frozenset(), blocklist=frozenset(),
                 symbol=SymbolMap("HYPE", "HYPEUSDT", Decimal(1)), instrument=INST, now_ms=T0 + 500,
                 leader_time_ms=T0, max_signal_age_ms=10_000)
SIZING = Sizing(Decimal("1000"), Decimal("20000"), Decimal("500"), Decimal("41"), False)
BC = BrokerContext(side=1, leader_px=Decimal("40"), mark=Decimal("40.1"), max_entry_drift=Decimal("0.01"),
                   tp_roi=Decimal("0.5"), leverage=20, copy_exists_for_episode=False, open_copy_on_coin=False,
                   broker_healthy=True, open_copies=0, max_open_positions=10,
                   account=Account(Decimal("10000"), Decimal("10000")), risk_per_trade=Decimal("0.1"),
                   sizing=SIZING, max_notional=None, instrument=INST)


def test_all_clear():
    assert pre_broker_checks(PRE) is None and broker_checks(BC) is None


@pytest.mark.parametrize(("change", "expected"), [
    (dict(copy_enabled=False), Rejection.DISABLED),
    (dict(risk_block=Rejection.KILL_SWITCH), Rejection.KILL_SWITCH),
    (dict(risk_block=Rejection.DAILY_LOSS_LIMIT), Rejection.DAILY_LOSS_LIMIT),
    (dict(risk_block=Rejection.LEADER_BREAKER), Rejection.LEADER_BREAKER),
    (dict(risk_block=Rejection.LATENCY_BREAKER), Rejection.LATENCY_BREAKER),
    (dict(main_perp=False), Rejection.UNSUPPORTED_MARKET),
    (dict(synced=False), Rejection.PRE_EXISTING_POSITION),
    (dict(allow_long=False), Rejection.SIDE_DISABLED),
    (dict(blocklist=frozenset({"HYPE"})), Rejection.COIN_BLOCKED),
    (dict(allowlist=frozenset({"BTC"})), Rejection.COIN_BLOCKED),
    (dict(symbol=None), Rejection.SYMBOL_NOT_LISTED),
    (dict(now_ms=T0 + 10_001), Rejection.SIGNAL_TOO_OLD),
])
def test_pre_broker_rejections(change, expected):
    assert pre_broker_checks(replace(PRE, **change)) == expected


@pytest.mark.parametrize(("change", "expected"), [
    (dict(mark=Decimal("40.5")), Rejection.PRICE_DRIFTED),
    (dict(mark=Decimal("39.5")), Rejection.PRICE_DRIFTED),
    (dict(tp_roi=Decimal("0.05"), mark=Decimal("40.11")), Rejection.PRICE_DRIFTED),  # already past TP (0.25 %)
    (dict(copy_exists_for_episode=True), Rejection.DUPLICATE_EPISODE),
    (dict(open_copy_on_coin=True), Rejection.COIN_ALREADY_OPEN),
    (dict(broker_healthy=False), Rejection.BROKER_UNAVAILABLE),
    (dict(mark=None), Rejection.BROKER_UNAVAILABLE),
    (dict(open_copies=10), Rejection.MAX_OPEN_POSITIONS),
    (dict(account=Account(Decimal("10000"), Decimal("999"))), Rejection.INSUFFICIENT_MARGIN),
    (dict(max_notional=Decimal("100")), Rejection.NOTIONAL_CAP),
    (dict(sizing=replace(SIZING, qty=Decimal("0"))), Rejection.QTY_TOO_SMALL),
])
def test_broker_rejections(change, expected):
    assert broker_checks(replace(BC, **change)) == expected


def test_first_failure_wins():
    assert pre_broker_checks(replace(PRE, copy_enabled=False, synced=False, symbol=None)) == Rejection.DISABLED
    assert broker_checks(replace(BC, mark=Decimal("50"), open_copies=99)) == Rejection.PRICE_DRIFTED


async def test_exits_are_never_blocked(harness):
    h = harness()
    await h.fill(F(0, "HYPE", "B", 40, 10, 0))
    h.risk.engage_kill("test")  # kill switch on, signal far too old, price far away: exits still go through
    h.paper.set_price("HYPEUSDT", Decimal("40.5"))
    h.tick(60_000)
    assert await h.fill(F(60_000, "HYPE", "A", 30, 5, 10)) == [Decision.FOLLOWED]  # reduce
    assert await h.fill(F(61_000, "HYPE", "A", 30, 5, 5)) == [Decision.FOLLOWED]  # close
    assert h.store.get_copy("HYPE", 1).status == CopyStatus.CLOSED_LEADER


async def test_kill_switch_blocks_new_opens(harness):
    h = harness()
    h.risk.engage_kill("test")
    assert await h.fill(F(0, "HYPE", "B", 40, 10, 0)) == [Decision.REJECTED]
    assert h.store.events_since(0)[-1]["rejection"] == "KILL_SWITCH"


async def test_stop_file_blocks_new_opens(harness):
    h = harness()
    h.settings.stop_file.parent.mkdir(parents=True, exist_ok=True)
    h.settings.stop_file.touch()
    assert await h.fill(F(0, "HYPE", "B", 40, 10, 0)) == [Decision.REJECTED]


async def test_exit_without_copy_is_info_not_error(harness):
    h = harness(copy_enabled=False)
    await h.fill(F(0, "HYPE", "B", 40, 10, 0))  # rejected: DISABLED
    assert await h.fill(F(1000, "HYPE", "A", 41, 10, 10)) == [Decision.NO_MATCHING_COPY]


async def test_observe_only_runs_everything_but_never_calls_the_broker(harness):
    h = harness(copy_observe_only=True)
    assert await h.fill(F(0, "HYPE", "B", 40, 10, 0)) == [Decision.OBSERVED_ONLY]
    assert h.paper.positions() == []
    c = h.store.get_copy("HYPE", 1)
    assert c.status == CopyStatus.OBSERVED_ONLY and c.qty_initial == Decimal("500.00")
    assert await h.fill(F(1000, "HYPE", "A", 41, 10, 10)) == [Decision.OBSERVED_ONLY]
    assert h.store.get_copy("HYPE", 1).closed_at is not None
