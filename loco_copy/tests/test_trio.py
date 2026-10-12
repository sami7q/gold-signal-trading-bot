"""botup.md v5: the trio in ONE cross account -- two lanes on the same coin net out (virtual position per lane,
net order), each sized on the whole account; the observe numbers; the App wiring."""

from dataclasses import replace
from decimal import Decimal

from loco_copy.brokers.bybit_stream import QuoteBook
from loco_copy.config import LOCO_ADDRESS
from loco_copy.constants import CopyStatus, Decision
from loco_copy.models import Quote
from loco_copy.notify import Notifier
from loco_copy.observe import daily_line, observe_stats
from loco_copy.risk import AccountGuard
from tests.conftest import INSTRUMENTS, F, make_settings
from tests.test_speed import FakeTickerSocket

A, B = "0x8bae3527e5a33fa0cf184f37bc112d071463ab6d", "0x8fc7c0442e582bca195978c5a4fdec2e7c5bb0f7"


def trio(cap20):
    a = cap20(copy_observe_only=True, shared=True)
    b = cap20(copy_observe_only=True, shared=True, label="8fc7", store=a.store, virtual=a.virtual)
    b.now = a.now  # one clock
    b.engine._clock = a.engine._clock
    return a, b


async def test_two_lanes_on_one_coin_net_out_and_exit_only_their_own_share(cap20):
    a, b = trio(cap20)
    assert await a.fill(F(0, "HYPE", "B", 40, 1000, 0)) == [Decision.OBSERVED_ONLY]  # lane a long 24
    assert await b.fill(F(10, "HYPE", "A", 40, 500, 0, tid=99)) == [Decision.OBSERVED_ONLY]  # lane b short 12
    assert a.virtual.position("HYPEUSDT").qty == 12 and a.virtual.position("HYPEUSDT").side == 1  # net
    ca, cb = a.copy("HYPE"), b.copy("HYPE")
    assert ca.qty_open == 24 and cb.qty_open == 12 and cb.side == -1
    # both were sized on the whole account (alloc 1.0): same R for the same leader equity
    assert ca.ratio_r == cb.ratio_r == Decimal("0.024")
    a.price("HYPEUSDT", 41)
    assert await a.fill(F(1000, "HYPE", "A", 41, 1000, 1000)) == [Decision.CLOSED]  # a sells 24: not reduce-only
    pos = a.virtual.position("HYPEUSDT")
    assert pos.side == -1 and pos.qty == 12  # what is left is exactly lane b's share
    assert a.copy("HYPE").realized_pnl == 24 and b.copy("HYPE").closed_at is None
    assert await b.fill(F(2000, "HYPE", "B", 41, 500, -500, tid=100)) == [Decision.CLOSED]
    assert a.virtual.positions() == [] and b.copy("HYPE").realized_pnl == -12
    assert a.virtual.equity() == 10000 + 24 - 12
    assert all(c.status == CopyStatus.OBSERVED_ONLY for c in a.store.copies()) and a.exchange.positions() == []


async def test_the_shared_account_kill_switch_stops_every_lane(cap20):
    a, b = trio(cap20)
    guard = AccountGuard(a.store, "virtual", Decimal("0.25"), Notifier(a.store, echo=False))
    a.engine.virtual.guard = b.engine.virtual.guard = guard
    guard.on_equity(Decimal(10000))
    guard.on_equity(Decimal(7000))
    assert await a.fill(F(0, "HYPE", "B", 40, 1000, 0)) == [Decision.REJECTED]
    assert await b.fill(F(10, "BTC", "B", 100000, 1, 0)) == [Decision.REJECTED]
    assert {e["rejection"] for e in a.store.events_since(0)} == {"ACCOUNT_KILL"}


async def test_observe_numbers_latency_slippage_and_daily_line(cap20):
    a, _b = trio(cap20)
    a.price("HYPEUSDT", 40, spread="0.08")  # ask 40.04: 10 bps worse than the leader's 40
    await a.fill(F(0, "HYPE", "B", 40, 1000, 0, received=2500))  # seen 2.5 s after the leader
    st = observe_stats(a.store, ["loco", "8fc7"], 0)
    assert st["events"] == 1 and st["latency_p50_ms"] == 2500 and round(st["slippage_median_bps"], 6) == 10
    assert st["lanes"]["loco"]["opens"] == 1 and st["lanes"]["8fc7"]["events"] == 0  # an idle lane is fine
    line = daily_line(a.store, ["loco", "8fc7"], a.virtual.summary(), a.now[0])
    assert "loco=1" in line and "8fc7=0" in line and "latency p50/p95 2.5s" in line and "0.100%" in line


async def test_app_wires_the_trio_on_one_virtual_account(tmp_path, monkeypatch):
    from loco_copy import app as app_module
    from loco_copy.cap20 import Cap20Engine

    quotes = {"HYPEUSDT": Quote(Decimal(40), Decimal(40), Decimal(40))}
    monkeypatch.setattr(app_module, "fetch_public_instruments", lambda: dict(INSTRUMENTS))
    monkeypatch.setattr(app_module, "fetch_maintenance_rates", lambda: {"HYPEUSDT": Decimal("0.01")})
    monkeypatch.setattr(app_module, "fetch_public_tickers", lambda *a, **k: dict(quotes))
    s = make_settings(tmp_path, leaders=f"loco:{LOCO_ADDRESS},8bae:{A},8fc7:{B}", strategy="cap20_cross",
                      margin_mode="CROSS", follow_adds=True, leverage=3, shared_equity=True,
                      account_kill_dd_pct="25", copy_observe_only=True)
    app = app_module.App(s)
    app.quotes = QuoteBook(connect=FakeTickerSocket, rest=lambda symbol: dict(quotes))  # no network in tests
    await app.build_brokers()
    app.build_lanes()
    assert set(app.virtual) == {"virtual", "paper"}
    for lane in app.lanes.values():
        assert isinstance(lane.engine, Cap20Engine) and lane.virtual_name == "virtual" and lane.account == "paper"
        assert lane.engine.virtual.shared and lane.engine.virtual.guard is app.guards["virtual"]
    loco = app.lanes["loco"]
    app.store.set_state("leader_equity:loco", {"value": "1000000", "taken_at_ms": app_module.now_ms()})
    loco.risk.on_hl_ok()
    now = app_module.now_ms()
    piece = replace(F(0, "HYPE", "B", 40, 600, 0, tid=1), time_ms=now, received_at_ms=now, oid=5)
    rest = replace(F(0, "HYPE", "B", 40, 400, 600, tid=2), time_ms=now, received_at_ms=now, oid=5)
    await app.on_fills(loco, [rest, piece], False)  # one order in two pieces, delivered out of order
    events = app.store.events_since(0, leader="loco")
    assert [e["decision"] for e in events] == [Decision.OBSERVED_ONLY] and events[0]["pos_after"] == "1000"
    assert app.store.get_copy("HYPE", 1, leader="loco").qty_open == 24
    assert app.virtual["virtual"].position("HYPEUSDT").qty == 24 and app.virtual["paper"].positions() == []
    await app.on_fills(loco, [piece], False)  # a repeat: deduped by tid
    assert len(app.store.events_since(0, leader="loco")) == 1
    app.write_heartbeat()
    rt = app.store.get_state("runtime")
    assert rt["virtual"]["virtual"]["lanes"] == ["loco", "8bae", "8fc7"] and rt["lanes"]["8bae"]["strategy"] == \
        "cap20_cross"
    app.save_runtime()
    assert Decimal(app.store.get_state("virtual_account:virtual")["positions"]["HYPEUSDT"]["qty"]) == 24
    await app.client.close()
