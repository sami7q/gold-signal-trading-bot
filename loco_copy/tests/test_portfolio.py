"""botup.md §V6: the «new trio» portfolio simulator -- ledger rules (= research psim), the live wiring (prices,
staleness, dedupe, missed exits), the WS routing and the dashboard API."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from loco_copy.auth import hash_password
from loco_copy.feed import HyperliquidFeed
from loco_copy.notify import Notifier
from loco_copy.pf_ledger import DAY_MS, Ledger
from loco_copy.portfolio import PortfolioSim, parse_any_perp_fill, parse_wallets
from loco_copy.store import Store, now_ms
from loco_copy.web import create_app
from tests.conftest import T0, make_settings
from tests.test_web import ORIGIN, login

LOCO = "0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1"
W8477 = "0x8477e447846c758f5a675856001ea72298fd9cb5"


def ledger(**kw: Any) -> Ledger:
    args: dict[str, Any] = dict(start_equity=10_000.0, leverage=3, fee=0.0)
    args.update(kw)
    return Ledger("t", **args)


# -- ledger ------------------------------------------------------------------------------------------------------

def test_sizing_uses_weight_of_the_account():
    L = ledger()
    out = L.on_fill("loco", 0.5, "BTC", 0, 1, 100.0, T0, 100_000.0)  # wallet buys 1 BTC at $100
    tr = L.T["loco|BTC"]
    assert tr["R"] == pytest.approx(2.4 * 0.5 * 10_000 / 100_000)  # K x w x E / W = 0.12
    assert tr["cap"] == pytest.approx(0.2 * 3 * 0.5 * 10_000)  # $3,000
    assert out.action == "OPEN" and out.qty == pytest.approx(0.12)
    assert L.P["loco|BTC"]["q"] == pytest.approx(0.12)


def test_small_adds_wait_until_ten_dollars_and_stop_at_the_cap():
    L = ledger()
    assert L.on_fill("a", 0.25, "ETH", 0, 0.5, 10.0, T0, 10_000.0).action == "PENDING"  # 0.5x0.6=0.3 -> $3
    assert L.on_fill("a", 0.25, "ETH", 0.5, 3, 10.0, T0 + 1, 10_000.0).action == "OPEN"  # 2.1 -> $21: first order
    assert L.P["a|ETH"]["q"] == pytest.approx(2.1)
    out = L.on_fill("a", 0.25, "ETH", 3.5, 10_000, 10.0, T0 + 2, 10_000.0)  # far past the cap ($1,500)
    assert out.action == "ADD" and L.P["a|ETH"]["q"] * 10 == pytest.approx(1_500)
    assert L.on_fill("a", 0.25, "ETH", 10_003.5, 100, 10.0, T0 + 3, 10_000.0).action == "CAPPED"
    assert L.T["a|ETH"]["capped"] >= 1


def test_reduce_close_and_flip():
    L = ledger()
    L.on_fill("a", 0.5, "SOL", 0, -10, 100.0, T0, 10_000.0)  # short: R = 1.2 -> 12 SOL ($1,200 <= cap 3,000)
    assert L.P["a|SOL"]["q"] == pytest.approx(-12)
    assert L.on_fill("a", 0.5, "SOL", -10, 5, 90.0, T0 + 1, 10_000.0).action == "REDUCE"  # half closed at 90
    assert L.P["a|SOL"]["q"] == pytest.approx(-6)
    assert L.cash == pytest.approx(10_000 + 6 * 10)
    out = L.on_fill("a", 0.5, "SOL", -5, 7, 80.0, T0 + 2, 10_000.0)  # flip to +2 long
    assert out.action == "FLIP"
    assert L.P["a|SOL"]["q"] == pytest.approx(2 * L.T["a|SOL"]["R"])
    assert L.trades == 1 and L.wins == 1
    assert L.on_fill("a", 0.5, "SOL", 2, -2, 81.0, T0 + 3, 10_000.0).action == "CLOSE"
    assert "a|SOL" not in L.P and L.trades == 2


def test_position_open_before_start_is_ignored_until_flat():
    L = ledger()
    assert L.on_fill("a", 0.5, "BTC", 3, 1, 100.0, T0, 10_000.0).action == "WAIT"
    assert L.on_fill("a", 0.5, "BTC", 4, -4, 100.0, T0 + 1, 10_000.0).action == "WAIT"  # flat now
    assert L.on_fill("a", 0.5, "BTC", 0, 1, 100.0, T0 + 2, 10_000.0).action == "OPEN"


def test_hip3_coin_and_fees():
    L = ledger(fee=0.00095)
    L.on_fill("a", 0.5, "xyz:TSLA", 0, 100, 400.0, T0, 1_000_000.0)  # R = 0.012 -> 1.2 shares, $480
    assert L.fees_paid == pytest.approx(480 * 0.00095)
    L.on_fill("a", 0.5, "xyz:TSLA", 100, -100, 440.0, T0 + 1, 1_000_000.0)
    assert L.equity() == pytest.approx(10_000 + 1.2 * 40 - (480 + 528) * 0.00095)


def test_drawdown_pause_blocks_new_trades_not_exits():
    L = ledger(maxlev=lambda c: 50.0)
    L.on_fill("a", 0.5, "BTC", 0, 1, 100.0, T0, 400.0)  # R = 30 -> capped at $3,000 = 30 BTC
    L.mark({"BTC": 70.0}, T0 + 1)  # -$900 ... not yet 25 %
    assert not L.paused(T0 + 1)
    L.mark({"BTC": 15.0}, T0 + 2)  # -$2,550 -> equity 7,450 < 75 % of 10,000
    assert L.paused(T0 + 2) and L.pauses == 1
    assert L.on_fill("a", 0.5, "ETH", 0, 1, 10.0, T0 + 3, 400.0).action == "SKIP_PAUSED"
    assert L.on_fill("a", 0.5, "BTC", 1, -1, 15.0, T0 + 4, 400.0).action == "CLOSE"  # exits always
    assert L.on_fill("a", 0.5, "ETH", 1, 1, 10.0, T0 + 5, 400.0).action == "WAIT"  # skipped trade stays skipped
    assert not L.paused(T0 + 3 + 7 * DAY_MS)


def test_cross_liquidation_closes_everything():
    L = ledger(maxlev=lambda c: 3.0)
    coins = ["A", "B", "C", "D", "E"]
    for c in coins:  # five trades at their $6,000 cap: $30,000 = 3x the account
        L.on_fill("a", 1.0, c, 0, 1, 100.0, T0, 100.0)
    assert L.notional() == pytest.approx(30_000)
    assert L.mark({c: 66.0 for c in coins}, T0 + 1) == [f"a|{c}" for c in coins]
    assert L.liquidations == 1 and not L.P and L.cash == 0
    assert L.closed[-1]["reason"] == "LIQUIDATED" and L.WAIT["a|A"]


def test_persistence_round_trip():
    L = ledger()
    L.on_fill("a", 0.5, "BTC", 0, 1, 100.0, T0, 10_000.0)
    M = ledger()
    M.load(json.loads(json.dumps(L.to_dict())))
    assert M.equity() == L.equity() and M.T == L.T and M.P == L.P


# -- live wiring -------------------------------------------------------------------------------------------------

class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.positions: dict[str, list[dict[str, Any]]] = {}

    async def info(self, body: dict[str, Any], weight: int) -> Any:
        self.calls.append(body)
        if body["type"] == "clearinghouseState":
            return {"assetPositions": self.positions.get(body["user"], [])}
        if body["type"] == "perpDexs":
            return [None, {"name": "xyz"}]
        if body["type"] == "meta":
            return {"universe": [{"name": "BTC", "maxLeverage": 40}] if "dex" not in body
                    else [{"name": "xyz:TSLA", "maxLeverage": 10}]}
        return []

    async def portfolio_equity(self, user: str) -> Decimal | None:
        return Decimal("100000")


def make_sim(tmp_path: Any, now: list[int]) -> tuple[PortfolioSim, Store, FakeClient]:
    s = make_settings(tmp_path, portfolio_wallets=f"loco:{LOCO}:0.5,8477:{W8477}:0.25")
    store = Store(s.db_path)
    client = FakeClient()
    sim = PortfolioSim(s, store, Notifier(store, echo=False), client, clock=lambda: now[0])  # type: ignore[arg-type]
    return sim, store, client


def raw(t: int, coin: str, side: str, px: float, sz: float, start: float, tid: int) -> dict[str, Any]:
    return {"coin": coin, "side": side, "px": str(px), "sz": str(sz), "startPosition": str(start),
            "time": T0 + t, "tid": tid, "oid": tid}


async def test_live_prices_staleness_and_dedupe(tmp_path):
    now = [T0 - 1000]
    sim, store, _ = make_sim(tmp_path, now)
    await sim.start()
    assert sim.maxlev("xyz:TSLA") == 10 and sim.dexes == ["", "xyz"]
    now[0] = T0 + 500
    sim.on_mids({"mids": {"BTC": "101"}})
    await sim.on_raw_fills(LOCO, [raw(0, "BTC", "B", 100, 1, 0, 1)], False)
    base, real = sim.ledgers["base"], sim.ledgers["real"]
    assert base.P["loco|BTC"]["a"] == 100 and real.P["loco|BTC"]["a"] == 101  # real = live mid when seen
    await sim.on_raw_fills(LOCO, [raw(0, "BTC", "B", 100, 1, 0, 1)], False)  # same tid again: ignored
    assert len(store.pf_events_since(0)) == 1
    now[0] = T0 + 10 * 60_000  # a new trade seen 10 min late: base copies it, real does not open
    await sim.on_raw_fills(W8477, [raw(1000, "xyz:TSLA", "A", 400, 10, 0, 2)], False)
    assert "8477|xyz:TSLA" in base.P and "8477|xyz:TSLA" not in real.P
    assert store.pf_events_since(0)[0]["real_action"] == "SKIP_STALE"
    assert sim.ledgers["real"].on_fill("8477", 0.25, "xyz:TSLA", -10, 10, 400.0, T0 + 2000, 1.0).action == "WAIT"


async def test_spot_and_outcome_fills_are_dropped():
    assert parse_any_perp_fill(raw(0, "@107", "B", 1, 1, 0, 1), T0, hip3=True) is None
    assert parse_any_perp_fill(raw(0, "#102", "B", 1, 1, 0, 1), T0, hip3=True) is None
    assert parse_any_perp_fill(raw(0, "xyz:TSLA", "B", 1, 1, 0, 1), T0, hip3=False) is None
    assert parse_any_perp_fill(raw(0, "xyz:TSLA", "B", 1, 1, 0, 1), T0, hip3=True) is not None
    with pytest.raises(ValueError):
        parse_wallets(f"a:{LOCO}:0.7,b:{W8477}:0.7")


async def test_missed_exit_needs_two_flat_snapshots(tmp_path):
    now = [T0 - 1000]
    sim, store, client = make_sim(tmp_path, now)
    await sim.start()
    now[0] = T0 + 100
    await sim.on_raw_fills(LOCO, [raw(0, "BTC", "B", 100, 1, 0, 1)], False)
    client.positions[LOCO] = [{"position": {"coin": "BTC", "szi": "1"}}]
    now[0] += 60_000
    await sim.check_missed_exits(sim.wallets[0])
    assert "loco|BTC" in sim.ledgers["real"].P
    client.positions[LOCO] = []  # the wallet is flat but its close never reached us
    now[0] += 60_000
    await sim.check_missed_exits(sim.wallets[0])
    assert "loco|BTC" in sim.ledgers["real"].P  # one snapshot is never enough
    now[0] += 60_000
    await sim.check_missed_exits(sim.wallets[0])
    assert "loco|BTC" not in sim.ledgers["real"].P and "loco|BTC" not in sim.ledgers["base"].P
    assert store.pf_trades_since(0)[0]["reason"] == "RECONCILED"


async def test_ws_routes_raw_fills_and_mids():
    got: list[tuple[str, int]] = []
    mids: list[dict[str, Any]] = []

    async def on_raw(user: str, raws: list[dict[str, Any]], is_history: bool) -> None:
        got.append((user, len(raws)))

    async def lane(fills: Any, is_history: bool) -> None:
        got.append(("lane", len(fills)))

    feed = HyperliquidFeed(ws_url="", users={LOCO: lane}, client=None, on_frame=lambda: None,  # type: ignore[arg-type]
                           last_seen_ms=lambda a: None, raw_users={LOCO: on_raw, W8477: on_raw}, on_mids=mids.append)
    frame = {"channel": "userFills", "data": {"user": LOCO, "fills": [raw(0, "BTC", "B", 1, 1, 0, 1),
                                                                      raw(0, "xyz:TSLA", "B", 1, 1, 0, 2)]}}
    await feed._dispatch(json.dumps(frame))
    assert got == [(LOCO, 2), ("lane", 1)]  # the lane never sees the HIP-3 fill
    await feed._dispatch(json.dumps({"channel": "userFills", "data": {"user": W8477, "fills": [raw(0, "ETH", "A", 1, 1,
                                                                                                     0, 3)]}}))
    assert got[-1] == (W8477, 1)
    await feed._dispatch(json.dumps({"channel": "allMids", "data": {"dex": "xyz", "mids": {"xyz:TSLA": "1"}}}))
    assert mids == [{"dex": "xyz", "mids": {"xyz:TSLA": "1"}}]


async def test_dashboard_api(tmp_path):
    now = [now_ms() - 5000]
    sim, store, _ = make_sim(tmp_path, now)
    await sim.start()
    now[0] += 1000
    sim.on_mids({"mids": {"BTC": "100.1"}})
    await sim.on_raw_fills(LOCO, [{**raw(0, "BTC", "B", 100, 1, 0, 9), "time": now[0] - 300}], False)
    await sim.mark_once()
    store.upsert_user("sami", "Sami", hash_password("correct-horse"))
    app = create_app(make_settings(tmp_path, api_cookie_secure=False))
    with TestClient(app, headers={"origin": ORIGIN}) as c:
        login(c)
        d = c.get("/api/portfolio").json()
    assert d["enabled"] and d["runtime"]["ledgers"]["real"]["positions"][0]["coin"] == "BTC"
    assert d["recent"][0]["slippage_bps"] == pytest.approx(10, abs=0.01)
    assert d["stats"]["latency_p50_ms"] == pytest.approx(300)
    assert {c["key"] for c in d["checks"]} >= {"days", "max_dd", "liquidations", "missed_exits"}
    assert len(d["curves"]["real"]) == 1


async def test_removed_wallet_is_followed_to_its_exits_only(tmp_path):
    now = [T0 - 1000]
    sim, store, _ = make_sim(tmp_path, now)
    await sim.start()
    now[0] = T0 + 100
    await sim.on_raw_fills(W8477, [raw(0, "ETH", "B", 10, 100, 0, 1)], False)
    assert "8477|ETH" in sim.ledgers["base"].P
    s2 = make_settings(tmp_path, portfolio_wallets=f"loco:{LOCO}:0.5")  # 8477 taken out
    sim2 = PortfolioSim(s2, store, Notifier(store, echo=False), sim.client, clock=lambda: now[0])
    await sim2.start()
    assert W8477 in sim2.addresses and sim2.by_address[W8477].weight == 0
    now[0] += 1000
    await sim2.on_raw_fills(W8477, [raw(1000, "BTC", "B", 100, 1, 0, 2)], False)  # a new trade: never copied
    assert "8477|BTC" not in sim2.ledgers["base"].P
    await sim2.on_raw_fills(W8477, [raw(1001, "ETH", "A", 11, 100, 100, 3)], False)  # its exit: followed
    assert "8477|ETH" not in sim2.ledgers["base"].P
