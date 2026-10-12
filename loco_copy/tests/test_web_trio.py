"""Dashboard API for v4/v5: cap20 fields on trades and events, the trio panel (/api/virtual), strategy card."""

from decimal import Decimal

from fastapi.testclient import TestClient

from loco_copy.auth import hash_password
from loco_copy.config import LOCO_ADDRESS
from loco_copy.store import Store, now_ms
from loco_copy.web import create_app
from tests.conftest import F, make_settings
from tests.test_web import ORIGIN, login

A = "0x8bae3527e5a33fa0cf184f37bc112d071463ab6d"


async def test_trio_panel_and_cap20_fields(tmp_path, cap20):
    s = make_settings(tmp_path, api_cookie_secure=False, leaders=f"loco:{LOCO_ADDRESS},8bae:{A}",
                      strategy="cap20_cross", margin_mode="CROSS", follow_adds=True, leverage=3)
    store = Store(s.db_path)
    store.upsert_user("sami", "Sami", hash_password("correct-horse"))
    h = cap20(copy_observe_only=True, label="8bae", store=store)
    h.price("HYPEUSDT", 40, spread="0.08")
    await h.fill(F(0, "HYPE", "B", 40, 1000, 0, received=1500))
    await h.fill(F(1000, "HYPE", "B", 40, 2000, 1000, received=2500))
    store.record_equity("virtual", now_ms() - 1000, h.virtual.equity())
    store.set_state("runtime", {"updated_ms": now_ms(), "lanes": {"8bae": {"account": "paper",
                                                                          "virtual_account": "virtual"}},
                                "virtual": {"virtual": h.virtual.summary() | {"lanes": ["8bae"], "peak": None,
                                                                              "kill_tripped": False}}})
    store.close()

    c = TestClient(create_app(s), headers={"Origin": ORIGIN})
    login(c)
    v = c.get("/api/virtual").json()
    assert v["name"] == "virtual" and v["account"]["start_equity"] == "10000" and len(v["curve"]) == 1
    lane = v["lanes"][0]
    assert lane["label"] == "8bae" and lane["trades"] == 1 and lane["open"] == 1 and lane["adds"] == 1
    assert lane["counts"]["opens"] == 1 and lane["counts"]["adds"] == 1
    assert v["stats"]["latency_p50_ms"] == 1500 and round(v["stats"]["slippage_median_bps"], 6) == 10
    assert v["open"][0]["virtual"] and v["open"][0]["cap_used_pct"] == 48.0 and v["open"][0]["adds_done"] == 1
    assert v["recent"][0]["our_px"] == "40.04" and v["recent"][0]["pos_after"] == "3000"
    checks = {x["key"]: x for x in v["checks"]}
    assert checks["missed_exits"]["pass"] and checks["latency_p95"]["pass"]
    assert checks["trades_observed"]["pass"] is False  # 1 of the 10 needed

    copy = c.get("/api/copies", params={"leader": "8bae"}).json()[0]
    assert copy["ratio_r"] == "0.024" and Decimal(copy["cap_notional"]) == 6000 and not copy["capped"]
    st = c.get("/api/settings").json()
    assert st["cap20"]["strategy"] == "cap20_cross" and st["cap20"]["k"] == "2.4"
    assert st["lane_strategies"]["8bae"]["cap_x_equity"] == "0.6"
    leaders = {x["label"]: x for x in c.get("/api/leaders").json()}
    assert leaders["8bae"]["virtual"]["trades"] == 1 and leaders["8bae"]["strategy"]["name"] == "cap20_cross"
