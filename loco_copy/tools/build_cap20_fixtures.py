"""One-off (botup.md v4 §5 replay.py + tools/build_fixtures.py): the inputs and the benchmark of the cap20 replay test.

    .venv\\Scripts\\python tools\\build_cap20_fixtures.py "<path to hyperliquid_copy_research>"

Writes into tests/fixtures (the runtime and the test-suite never read the research project):
  loco_equity.json.gz        Loco's allTime accountValueHistory: W(t), interpolated, sizes every trade
  loco_candles_cap20.json.gz the candles the research's cap20 simulator used, per trade ("coin|t0"), already bucketed
                             (4h before 2026-03-17, 1h after) -- drive marks and the cross-liquidation check
  benchmark_cap20.json       the research's own result (hl_verify_sim.run, crypto only, taker fee + slippage 0.095 %
                             per side, $10,000) at 3x and 5x -- botup.md §1: $17,841 / $25,518, 0 liquidations
Loco's fills come from the existing tests/fixtures/loco_fills.json.gz (same source file in the research).
"""

from __future__ import annotations

import gzip
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

FX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
FEE, START = 0.00095, 10000.0


def main(research: Path) -> None:
    os.chdir(research)
    sys.path.insert(0, str(research))
    touched = research / "data" / "hunt" / "verify_trades_2026-10-11.json"
    before = touched.read_bytes() if touched.exists() else None
    try:
        with redirect_stdout(io.StringIO()):
            import hl_verify_sim as S  # research code, imported from its own folder
    finally:
        if before is not None:
            touched.write_bytes(before)  # its import rewrites this file: leave the research untouched

    crypto = [e for e in S.VT.inwin if ":" not in e["coin"]]
    S.VT.inwin[:] = crypto
    candles = {}
    for e in crypto:
        t1 = e["t1"] or S.END
        c = [k for k in S.bucket(S.cached_candles(e["coin"], e["t0"], t1) or []) if e["t0"] <= k[0] <= t1]
        if c:
            candles[f"{e['coin']}|{e['t0']}"] = c
    with gzip.open(FX / "loco_candles_cap20.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(candles, fh, separators=(",", ":"))
    with gzip.open(FX / "loco_equity.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(S.AVH, fh, separators=(",", ":"))

    bench = {"source": "hyperliquid_copy_research hl_verify_sim.run(L, 'cross', 0.8, 0.2, fee, start), crypto only",
             "t0_ms": S.T0, "end_ms": S.END, "fee_per_side": FEE, "start_equity": START, "min_order_usdt": 10,
             "maintenance": "notional / (2 x 10)", "trips": len(crypto)}
    for lev in (3, 5):
        r = S.run(float(lev), "cross", 0.8, 0.2, FEE, START)
        bench[f"{lev}x"] = {k: r[k] for k in ("final", "closed", "win", "liq", "fees", "dd_live", "closest_maint")}
    (FX / "benchmark_cap20.json").write_text(json.dumps(bench, indent=1), encoding="utf-8")
    print(json.dumps(bench), len(candles), "candle sets")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
