"""botup.md §V6: the simulator's ledger == the research simulator (`portfolio_sim.psim`, the code behind the
spec's numbers) on 20 recorded days of the three wallets (crypto + HIP-3), at both fee levels. Fixture built
once from Hyperliquid by the research harness; the expected numbers are psim's own output on it."""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from loco_copy.pf_ledger import Ledger

FIXTURE = Path(__file__).parent / "fixtures" / "portfolio_psim_20d.json.gz"


def interp(series: list[list[float]], t: int) -> float:
    """wallet_scanner.interp: the wallet's equity at t, linear between portfolio points."""
    if not series:
        return 0.0
    if t <= series[0][0]:
        return series[0][1]
    if t >= series[-1][0]:
        return series[-1][1]
    lo, hi = 0, len(series) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if series[mid][0] <= t else (lo, mid)
    (t0, v0), (t1, v1) = series[lo], series[hi]
    return v0 + (v1 - v0) * (t - t0) / max(t1 - t0, 1)


@pytest.mark.parametrize("fee", ["0.00045", "0.00095"])
def test_ledger_matches_research_psim(fee: str) -> None:
    fx: dict[str, Any] = json.loads(gzip.decompress(FIXTURE.read_bytes()))
    ev: list[tuple[int, int, str | None, Any]] = []
    for a, fills in fx["fills"].items():
        ev += [(f["time"], 0, a, f) for f in fills]
    bars: dict[int, list[Any]] = defaultdict(list)
    for coin, rows in fx["candles"].items():
        for t, h, low, close in rows:
            bars[t].append((coin, h, low, close))
    ev += [(t, 1, None, b) for t, b in bars.items()]
    ev.sort(key=lambda x: (x[0], x[1]))
    maxlev = fx["maxlev"]
    led = Ledger("t", start_equity=10_000.0, leverage=3, fee=float(fee), maxlev=lambda c: float(maxlev.get(c, 10)))
    for t, kind, a, x in ev:
        if kind == 0:
            assert a is not None
            d = float(x["sz"]) if x["side"] == "B" else -float(x["sz"])
            led.on_fill(a[:6], fx["weights"][a], x["coin"], float(x["startPosition"]), d, float(x["px"]), t,
                        interp(fx["equity"][a], t))
        else:
            led.mark({c: close for c, _h, _low, close in x}, t)
    want = fx["expected"][fee]
    assert led.equity() == pytest.approx(want["final"], abs=0.01)
    assert led.trades == want["trades"] and led.liquidations == want["liq"]
    assert led.mdd * 100 == pytest.approx(want["mdd"], abs=0.001)
    assert any(":" in c for c in fx["candles"])  # HIP-3 markets are part of the run
