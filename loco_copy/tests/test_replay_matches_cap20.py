"""botup.md v4 §6 test_replay_matches_cap20 (rollout M1): Loco's recorded year of fills through the REAL cap20
engine (tracker -> Cap20Engine -> virtual cross account) must reproduce the research's own simulator
(fixtures/benchmark_cap20.json, built by tools/build_cap20_fixtures.py from hl_verify_sim.run, crypto only,
fee + slippage 0.095 % per side, $10,000): final equity within 2 %, the same number of liquidations (0).

The remaining gap (< 1 %) is two rules the spec has and the research does not: pending adds are scaled on a reduce
(spec §4.3), and quantities are rounded down to the step."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from loco_copy.replay import equity_at, load_equity_history, load_fixture_fills, load_trip_candles, run_cap20_replay
from tests.conftest import CAP20, make_settings

FX = Path(__file__).parent / "fixtures"
BENCH = json.loads((FX / "benchmark_cap20.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def data():
    return (load_fixture_fills(FX / "loco_fills.json.gz"), load_trip_candles(FX / "loco_candles_cap20.json.gz"),
            equity_at(load_equity_history(FX / "loco_equity.json.gz")))


@pytest.mark.parametrize("lev", [3, 5])
async def test_replay_reproduces_the_research_year(data, tmp_path, lev):
    fills, candles, w = data
    s = make_settings(tmp_path, **(CAP20 | {"leverage": lev}), min_order_usdt=Decimal(BENCH["min_order_usdt"]))
    r = await run_cap20_replay(fills, candles, w, s, start_ms=BENCH["t0_ms"], end_ms=BENCH["end_ms"],
                               fee_rate=Decimal(str(BENCH["fee_per_side"])),
                               start_equity=Decimal(str(BENCH["start_equity"])))
    b = BENCH[f"{lev}x"]
    assert abs(r.final_equity / Decimal(b["final"]) - 1) < Decimal("0.02")
    assert r.counts["liq"] == b["liq"] == 0
    assert r.counts["closed"] == b["closed"]
    assert abs(float(r.max_dd) - b["dd_live"]) < 0.01
    assert abs(r.counts["wins"] / r.counts["closed"] - b["win"]) < 0.01
    assert abs(r.counts["closest_x100"] / 100 - b["closest_maint"]) < 0.05
    assert not r.rejections  # the 6x exposure limit and every other rule stayed quiet, as in the backtest
