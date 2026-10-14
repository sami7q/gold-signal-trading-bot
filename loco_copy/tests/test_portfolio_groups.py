"""botup.md §V10: more simulator sections next to the trio (e.g. 8bae + 8fc7 + 69b0 + 2ac4) on the same wallet
fills -- each with its own ledgers for every balance, each copying only trades that start after it started; the
dashboard API per section and the comparison of all sections."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from loco_copy.auth import hash_password
from loco_copy.notify import Notifier
from loco_copy.portfolio import PortfolioSim, parse_groups
from loco_copy.store import Store
from loco_copy.web import create_app
from tests.conftest import T0, make_settings
from tests.test_portfolio import FakeClient, raw
from tests.test_web import ORIGIN, login

A = "0x8bae3527e5a33fa0cf184f37bc112d071463ab6d"
B = "0x8fc7c0442e582bca195978c5a4fdec2e7c5bb0f7"
C = "0x2ac4880624dae9e823874d65e43bfeba97fc61b5"
D = "0x2396bc99feedc162af809314257571bb7ff07a36"
GROUPS = "mixc=aa+bb+cc:29.01:5.79;mixd=aa+bb+dd"


def make(tmp_path: Any, now: list[int], groups: str = GROUPS) -> tuple[PortfolioSim, Store]:
    s = make_settings(tmp_path, portfolio_wallets=f"aa:{A}:1,bb:{B}:1", portfolio_extra_wallets=f"cc:{C}:1,dd:{D}:1",
                      portfolio_groups=groups, api_cookie_secure=False)
    store = Store(s.db_path)
    sim = PortfolioSim(s, store, Notifier(store, echo=False), FakeClient(), clock=lambda: now[0])  # type: ignore[arg-type]
    return sim, store


def test_parse_groups_and_bad_config(tmp_path):
    g = parse_groups(GROUPS)
    assert [x.name for x in g] == ["mixc", "mixd"] and g[0].labels == ("aa", "bb", "cc")
    assert g[0].research_year == 29.01 and g[1].research_year is None
    for bad in ("trio=aa+bb", "mix=aa+aa", "=aa", "Mix X=aa"):
        with pytest.raises(ValueError):
            parse_groups(bad)
    with pytest.raises(ValueError, match="not in PORTFOLIO_WALLETS"):
        make(tmp_path, [T0], "mixz=aa+zz")


def test_default_settings_are_the_trio_and_two_mixes(tmp_path):
    s = make_settings(tmp_path)  # no custom wallets: the shipped defaults
    sim = PortfolioSim(s, Store(s.db_path), Notifier(Store(s.db_path), echo=False), FakeClient())  # type: ignore[arg-type]
    assert [(g.name, "+".join(g.labels)) for g in sim.groups] == [
        ("trio", "8bae+8fc7+69b0"), ("mix2ac4", "8bae+8fc7+69b0+2ac4"), ("mix2396", "8bae+8fc7+69b0+2396")]
    assert len(sim.wallets) == 5 and len(sim.ledgers) == 3 * 3 * 2  # 3 sections x 3 balances x base/real
    assert "real:10000" in sim.ledgers and "mix2ac4:real:100000" in sim.ledgers


async def test_each_section_copies_its_own_wallets(tmp_path):
    now = [T0 - 1000]
    sim, store = make(tmp_path, now)
    await sim.start()
    now[0] = T0 + 500
    await sim.on_raw_fills(A, [raw(0, "BTC", "B", 100, 1, 0, 1)], False)  # aa is in all three sections
    await sim.on_raw_fills(C, [raw(10, "ETH", "A", 10, 5, 0, 2)], False)  # cc only in mixc
    for name in ("real:10000", "mixc:real:10000", "mixd:base:50000"):
        assert "aa|BTC" in sim.ledgers[name].P
    assert "cc|ETH" in sim.ledgers["mixc:real:10000"].P
    assert all("cc|ETH" not in sim.ledgers[n].P for n in ("real:10000", "mixd:real:10000"))
    # the same 8bae trade at the same balance is the same size in every section (each wallet on the whole account)
    assert sim.ledgers["real:10000"].P["aa|BTC"]["q"] == pytest.approx(sim.ledgers["mixc:real:10000"].P["aa|BTC"]["q"])
    ev = store.pf_events_since(0)
    assert len(ev) == 2  # one row per wallet fill, every section's action in it
    by_wallet = {e["wallet"]: json.loads(e["actions"]) for e in ev}
    assert set(by_wallet["aa"]) == {"trio", "mixc", "mixd"} and set(by_wallet["cc"]) == {"mixc"}
    assert by_wallet["cc"]["mixc"][2] == "OPEN"
    assert {e["wallet"]: e["real_action"] for e in ev}["cc"] == "OPEN"  # its own section in the columns
    # the trio's daily line now lists every section
    assert sim.daily_line().count("\n") == 2


async def test_a_section_added_later_never_inherits_an_open_trade(tmp_path):
    now = [T0 - 1000]
    sim, _ = make(tmp_path, now, groups="")
    await sim.start()
    now[0] = T0 + 500
    await sim.on_raw_fills(A, [raw(0, "BTC", "B", 100, 1, 0, 1)], False)
    assert "aa|BTC" in sim.ledgers["real:10000"].P
    sim.save()
    now[0] = T0 + 3_600_000  # an hour later the mixes are switched on (same database, the trio's state restored)
    sim2, store = make(tmp_path, now)
    await sim2.start()
    assert sim2.group_started["trio"] == T0 - 1000 and sim2.group_started["mixc"] == now[0]
    assert "aa|BTC" in sim2.ledgers["real:10000"].P and not sim2.ledgers["mixc:real:10000"].P
    now[0] += 1000
    await sim2.on_raw_fills(A, [raw(3_600_500, "BTC", "B", 101, 1, 1, 3)], False)  # aa adds to its old trade
    assert sim2.ledgers["real:10000"].T["aa|BTC"]["adds"] == 1
    assert "aa|BTC" not in sim2.ledgers["mixc:real:10000"].P  # WAIT: opened before the mix started
    await sim2.on_raw_fills(A, [raw(3_600_600, "SOL", "B", 10, 10, 0, 4)], False)  # a new trade: both copy it
    assert "aa|SOL" in sim2.ledgers["real:10000"].P and "aa|SOL" in sim2.ledgers["mixc:real:10000"].P
    # an old fill (before the mix started) delivered late by the REST poll: the trio only
    await sim2.on_raw_fills(B, [raw(1000, "ETH", "B", 10, 5, 0, 5)], False)
    assert "bb|ETH" in sim2.ledgers["base:10000"].P and "bb|ETH" not in sim2.ledgers["mixc:base:10000"].P
    e = next(x for x in store.pf_events_since(0) if x["wallet"] == "bb")
    assert set(json.loads(e["actions"])) == {"trio"}


async def test_dashboard_api_per_section_and_compare(tmp_path):
    now = [T0 - 1000]
    sim, store = make(tmp_path, now)
    await sim.start()
    now[0] = T0 + 500
    await sim.on_raw_fills(A, [raw(0, "BTC", "B", 100, 1, 0, 1)], False)
    await sim.on_raw_fills(C, [raw(10, "ETH", "A", 10, 5, 0, 2)], False)
    await sim.mark_once()
    store.upsert_user("sami", "Sami", hash_password("correct-horse"))
    app = create_app(sim.s)
    with TestClient(app, headers={"origin": ORIGIN}) as c:
        login(c)
        trio = c.get("/api/portfolio").json()
        mix = c.get("/api/portfolio", params={"group": "mixc", "capital": 50000}).json()
        cmp_ = c.get("/api/portfolio/compare", params={"capital": 100000}).json()
        chart = c.get("/api/portfolio/chart", params={"wallet": "cc", "coin": "ETH", "group": "mixc"}).json()
        unknown = c.get("/api/portfolio", params={"group": "nope"}).json()
    assert trio["group"]["name"] == "trio" and {r["wallet"] for r in trio["recent"]} == {"aa"}
    assert [w["label"] for w in trio["runtime"]["wallets"]] == ["aa", "bb"]
    assert mix["group"]["name"] == "mixc" and mix["capital"] == 50000
    assert {r["wallet"] for r in mix["recent"]} == {"aa", "cc"}
    assert {p["wallet"] for p in mix["ledger"]["real"]["positions"]} == {"aa", "cc"}
    assert mix["expected"][0]["year"] == 29.01 and len(mix["expected"]) == 2
    assert [g["name"] for g in cmp_["groups"]] == ["trio", "mixc", "mixd"] and cmp_["capital"] == 100000
    assert cmp_["groups"][1]["capitals"]["10000"]["real"]["open_trades"] == 2
    assert chart["ledger"] == "mixc:real:10000" and chart["position"]["wallet"] == "cc"
    assert unknown["group"]["name"] == "trio"
