"""The most important test (botup.md §12): Loco's recorded fills + candles through the REAL pipeline
(tracker -> rules -> engine -> reconciler -> PaperBroker) must reproduce the research backtest.

Finding recorded here on purpose (2026-10-10): the research's published numbers price every trade at Loco's
volume-weighted entry INCLUDING his adds. This bot ignores adds (owner decision), so it enters at his FIRST
fill -- and Loco scales into ~half of his positions, often several % lower. Re-running the research's own
simulator with first-fill entries (fixtures/benchmark.json -> "first_fill") is what this pipeline must match,
and it does. The published numbers are NOT reachable by this strategy; see README "Strategy finding".
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from loco_copy.replay import fixture_candle_source, load_fixture_fills, run_replay
from tests.conftest import make_settings

FX = Path(__file__).parent / "fixtures"
BENCH = json.loads((FX / "benchmark.json").read_text())


@pytest.fixture(scope="module")
def data():
    return load_fixture_fills(FX / "loco_fills.json.gz"), fixture_candle_source(FX / "loco_candles.json.gz")


def research_equivalent(tmp_path):
    # The research had no position cap and no daily-loss limit; everything else is the bot's real config.
    return make_settings(tmp_path, max_open_positions=999, daily_loss_limit_pct=Decimal("0.99"))


async def replay(data, tmp_path, window):
    fills, candles = data
    w = BENCH["windows"]["first_fill"][window]
    return await run_replay(fills, candles, research_equivalent(tmp_path), start_ms=w["cutoff_ms"],
                            end_ms=BENCH["collected_ms"], fee_rate=Decimal(str(BENCH["strategy"]["cost_per_side"])))


@pytest.mark.parametrize("window", ["90d", "180d"])
async def test_counts_match_research_exactly(data, tmp_path, window):
    r = await replay(data, tmp_path, window)
    b = BENCH["windows"]["first_fill"][window]
    assert r.counts["copied"] == b["copied"]
    assert r.counts["tp"] == b["tp"]
    assert r.counts["liq"] == b["liq"]
    assert r.counts["exits"] == b["exits"]
    assert r.counts["open"] == b["open"]


async def test_full_history_matches_except_three_crash_day_trades(data, tmp_path):
    """10 Oct 2025: in 3 trades Loco's own fills traded beyond our liquidation price while the research's coarse
    candles missed it. The replay (correctly) liquidates them; nothing else differs."""
    r = await replay(data, tmp_path, "all")
    b = BENCH["windows"]["first_fill"]["all"]
    assert r.counts["copied"] == b["copied"]
    assert 0 <= r.counts["liq"] - b["liq"] <= 3
    assert abs(r.counts["tp"] - b["tp"]) <= 3


async def test_published_benchmark_is_not_reachable_with_adds_ignored(data, tmp_path):
    r = await replay(data, tmp_path, "90d")
    published = BENCH["windows"]["published"]["90d"]
    assert published["liq"] == 1 and r.counts["liq"] >= 10 * published["liq"]
    assert r.max_dd > Decimal(str(published["max_dd"])) * 3
