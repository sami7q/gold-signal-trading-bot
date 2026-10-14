"""botup.md §V8: the AI trade advisor -- the trader's record, market indicators, risk and exit alerts, the Claude
call (faked here), its triggers and limits, and the dashboard API."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from loco_copy.advisor import HOUR_MS, Advisor, assess, indicators, round_trips, trader_stats
from loco_copy.auth import hash_password
from loco_copy.notify import Notifier
from loco_copy.portfolio import PortfolioSim
from loco_copy.store import Store, now_ms
from loco_copy.web import create_app
from tests.conftest import make_settings
from tests.test_web import ORIGIN, login

LOCO = "0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1"


def fill(t: int, coin: str, side: str, px: float, sz: float, start: float, pnl: float = 0.0,
         tid: int = 0) -> dict[str, Any]:
    return {"coin": coin, "side": side, "px": str(px), "sz": str(sz), "startPosition": str(start), "time": t,
            "closedPnl": str(pnl), "fee": "0", "tid": tid or t}


def trip(t: int, coin: str, side: int, pnl: float, hold_h: float) -> list[dict[str, Any]]:
    """One closed trade of 1 unit at 100 (notional $100): ret = pnl / 100."""
    o, c = ("B", "A") if side > 0 else ("A", "B")
    return [fill(t, coin, o, 100, 1, 0), fill(t + int(hold_h * HOUR_MS), coin, c, 100, 1, side, pnl, t + 1)]


def history() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    t = 1_700_000_000_000
    for i in range(20):  # 15 BTC shorts: 12 win (+3 %), 3 lose (-2 %); 5 ETH longs all lose (-1 %)
        out += trip(t + i * 30 * HOUR_MS, "BTC", -1, 3.0 if i < 12 else -2.0, 4 + i) if i < 15 else \
            trip(t + i * 30 * HOUR_MS, "ETH", 1, -1.0, 2)
    return out


def test_record_and_probability():
    trips = round_trips(history())
    assert len(trips) == 20 and trips[0]["ret"] == pytest.approx(0.03)
    st = trader_stats(trips, coin="BTC", side=-1, age_ms=0)
    assert st["win_rate"] == pytest.approx(12 / 20) and st["coin_win_rate"] == pytest.approx(12 / 15)
    p_all = 13 / 22
    p_side = (12 + 10 * p_all) / (15 + 10)
    assert st["probability"] == pytest.approx((12 + 10 * p_side) / (15 + 10))
    assert st["win_ret_p50"] == pytest.approx(0.03) and st["loss_ret_p75"] == pytest.approx(0.02)
    eth = trader_stats(trips, coin="ETH", side=1, age_ms=0)
    assert eth["probability"] < st["probability"]  # his longs on ETH always lost


def candles(prices: list[float]) -> list[dict[str, Any]]:
    return [{"t": i * HOUR_MS, "o": p, "h": p * 1.002, "l": p * 0.998, "c": p} for i, p in enumerate(prices)]


def test_indicators_and_alerts():
    up = indicators(candles([100 + i for i in range(80)]))
    assert up is not None and up["trend"] == "up" and up["rsi14"] > 90
    stats = trader_stats(round_trips(history()), coin="BTC", side=-1, age_ms=0)
    short = {"side": -1, "entry": 100.0, "mark": 96.0, "qty": 1.0, "cap": 600.0}
    v = assess(short, stats, up, {"funding": "0.0001"}, age_ms=50 * HOUR_MS, liq_distance=1.5, leader_reducing=True)
    codes = {a["code"] for a in v["alerts"]}
    # +4 % for a short; usual win 3 % -> take-profit zone; uptrend against a short with price above EMA20;
    # 50 h > his p90 hold; liquidation near; funding: shorts are paid when it is positive (not against us)
    assert {"TAKE_PROFIT_ZONE", "MOMENTUM_AGAINST", "OVERDUE", "LIQ_NEAR", "LEADER_REDUCING"} <= codes
    assert "FUNDING_AGAINST" not in codes and v["risk_level"] == "high"
    losing = {**short, "mark": 103.0}
    lv = assess(losing, stats, None, None, age_ms=HOUR_MS, liq_distance=10, leader_reducing=False)
    assert [a["code"] for a in lv["alerts"]] == ["LOSS_BEYOND_USUAL"]


class FakeHL:
    def __init__(self) -> None:
        self.prices = [100 + i * 0.1 for i in range(300)]

    async def info(self, body: dict[str, Any], weight: int) -> Any:
        if body["type"] == "userFillsByTime":
            return history()
        if body["type"] == "candleSnapshot":
            return candles(self.prices)
        if body["type"] == "metaAndAssetCtxs":
            return [{"universe": [{"name": "BTC"}]}, [{"funding": "0.00001", "openInterest": "1"}]]
        if body["type"] == "perpDexs":
            return [None]
        if body["type"] == "meta":
            return {"universe": [{"name": "BTC", "maxLeverage": 40}]}
        return []

    async def portfolio_equity(self, user: str) -> Any:
        from decimal import Decimal

        return Decimal("100000")


class FakeClaude:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def analyse(self, facts: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        self.calls.append(facts)
        return ({"entry_reason": "كسر دعم", "thesis": "هبوط", "success_comment": "جيد", "risks": ["ارتداد"],
                 "exit_plan": "مع المتداول", "manual_exit_hint": "عند 3%", "verdict": "hold", "confidence": "medium"},
                {"model": "fake", "usage": {}})


async def setup(tmp_path: Any, now: list[int]) -> tuple[PortfolioSim, Advisor, Store, FakeClaude]:
    s = make_settings(tmp_path, portfolio_wallets=f"loco:{LOCO}:1", advisor_max_daily_calls=2)
    store = Store(s.db_path)
    hl = FakeHL()
    sim = PortfolioSim(s, store, Notifier(store, echo=False), hl, clock=lambda: now[0])  # type: ignore[arg-type]
    await sim.start()
    claude = FakeClaude()
    adv = Advisor(s, store, Notifier(store, echo=False), hl, sim, claude=claude, clock=lambda: now[0])  # type: ignore[arg-type]
    now[0] += 1000
    sim.on_mids({"mids": {"BTC": "100"}})
    await sim.on_raw_fills(LOCO, [fill(now[0] - 100, "BTC", "A", 100, 10, 0, tid=1)], False)  # a short opens
    return sim, adv, store, claude


async def test_advisor_runs_claude_on_open_and_on_request_within_budget(tmp_path):
    now = [now_ms() - 10 * HOUR_MS]
    _sim, adv, store, claude = await setup(tmp_path, now)
    await adv.tick()
    assert claude.calls == []  # not before the trade settled
    now[0] += 3 * 60_000
    await adv.tick()
    assert len(claude.calls) == 1 and claude.calls[0]["side"].startswith("بيع")
    row = store.pf_latest_analysis("loco", "BTC", with_ai=True)
    assert row is not None and row["kind"] == "open" and 0 < row["probability"] < 1
    await adv.tick()
    assert len(claude.calls) == 1  # nothing new: no call
    store.set_state("advisor_request:loco|BTC", {"at": now[0], "done": False})
    await adv.tick()
    assert len(claude.calls) == 2 and (store.get_state("advisor_request:loco|BTC") or {})["done"]
    store.set_state("advisor_request:loco|BTC", {"at": now[0], "done": False})
    await adv.tick()  # the daily cap (2) is reached: the numbers are stored, Claude is not called
    assert len(claude.calls) == 2
    last = store.pf_latest_analysis("loco", "BTC")
    assert last is not None and last["ai"] is None and "الحد اليومي" in last["meta"]


async def test_alerts_fire_once_and_the_api_shows_everything(tmp_path):
    now = [now_ms() - 10 * HOUR_MS]
    sim, adv, store, _claude = await setup(tmp_path, now)
    now[0] += 3 * 60_000
    sim.on_mids({"mids": {"BTC": "96"}})  # +4 % for our short: past his usual 3 % win
    await sim.mark_once()
    await adv.tick()
    await adv.tick()
    alerts = store.pf_alerts_for("loco", "BTC", 0)
    assert [a["code"] for a in alerts].count("TAKE_PROFIT_ZONE") == 1  # stays quiet while it keeps firing
    store.upsert_user("sami", "Sami", hash_password("correct-horse"))
    app = create_app(make_settings(tmp_path, api_cookie_secure=False))
    with TestClient(app, headers={"origin": ORIGIN}) as c:
        login(c)
        d = c.get("/api/portfolio/advice?wallet=loco&coin=BTC").json()
        assert c.post("/api/portfolio/advice/refresh", json={"wallet": "loco", "coin": "BTC"}).json()["ok"]
        assert c.get("/api/portfolio/advice?wallet=loco&coin=BTC").json()["pending"]
        everything = c.get("/api/portfolio/alerts").json()
    assert d["live"]["stats"]["trades"] == 20 and d["live"]["market"]["trend"] == "up"
    assert d["analysis"]["ai"]["verdict"] == "hold" and d["alerts"][0]["code"]
    assert any(a["code"] == "TAKE_PROFIT_ZONE" for a in everything)
    assert not any(r["type"] == "PF_ADVICE_ALERT" and r["level"] != "INFO" for r in store.logs_since(0))
