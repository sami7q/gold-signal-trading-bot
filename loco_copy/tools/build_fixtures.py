"""One-off: copy Loco's recorded fills + candles out of the research project into tests/fixtures.

Run ONCE (already done); the runtime and the test-suite never read the research project.

    .venv\\Scripts\\python tools\\build_fixtures.py "<path to hyperliquid_copy_research>"

It also records the research's own TP50 results per window (computed with the research code, exactly as
hl_loco.py does) so test_replay_matches_backtest.py can compare against them without importing anything.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

LEADER = "0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1"
DAY = 86_400_000
KEEP = ("coin", "side", "px", "sz", "startPosition", "time", "tid", "oid", "fee", "closedPnl")


def main(research: Path) -> None:
    sys.path.insert(0, str(research))
    import hl_common as H

    d = json.loads((research / "data" / "wallets" / f"{LEADER}.json").read_text(encoding="utf-8"))
    now = int(d["collected"])
    trades = H.reconstruct_trades(d["fills"], d.get("state"), now)

    candles: dict[str, list] = {}
    for t in trades:
        c = H.cached_candles(t["coin"], t["t0"], t["t1"] or now)
        if c:
            candles[f"{t['coin']}|{t['t0']}"] = c

    # The research priced every trade at Loco's volume-weighted entry INCLUDING his adds. A copier that ignores
    # adds (this bot, by owner decision) gets his FIRST fill instead, so both variants are recorded:
    #   "published"  = the research's reported numbers (avg entry) -- not reachable with adds ignored;
    #   "first_fill" = same simulator, same candles, entry = Loco's first fill -- what this bot should match.
    first_px: dict[tuple[str, int], float] = {}
    for f in d["fills"]:
        if H.is_perp(f["coin"]) and float(f.get("startPosition") or 0) == 0:
            first_px.setdefault((f["coin"], int(f["time"])), float(f["px"]))

    def run(name: str, days: int | None, entry: str) -> dict:
        cutoff = now - days * DAY if days else 0
        idx = [i for i, t in enumerate(trades) if t["t0"] >= cutoff]
        sub = []
        for i in idx:
            t = dict(trades[i])
            if entry == "first_fill":
                t["entry"] = first_px.get((t["coin"], t["t0"]), t["entry"])
            sub.append(t)
        cmap = {j: candles[f"{trades[i]['coin']}|{trades[i]['t0']}"] for j, i in enumerate(idx)
                if f"{trades[i]['coin']}|{trades[i]['t0']}" in candles}
        r = H.simulate(sub, 0.5, now=now, candle_map=cmap)
        return dict(cutoff_ms=cutoff, trades=len(sub), copied=r["n"], tp=r.get("TP", 0), liq=r.get("LIQ", 0),
                    exits=r.get("EXIT", 0), open=r.get("OPEN", 0), wins=r["wins"], losses=r["n"] - r["wins"],
                    max_dd=round(r["dd"], 4), final=round(r["final"], 2))

    windows = {entry: {name: run(name, days, entry) for name, days in (("90d", 90), ("180d", 180), ("all", None))}
               for entry in ("published", "first_fill")}

    out = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    out.mkdir(parents=True, exist_ok=True)
    fills = [{k: f.get(k) for k in KEEP} for f in d["fills"]]
    with gzip.open(out / "loco_fills.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(fills, fh)
    with gzip.open(out / "loco_candles.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(candles, fh)
    bench = dict(leader=LEADER, collected_ms=now, strategy=H.CFG["strategy"], take_profit_roi=0.5,
                 candle_coverage=round(len(candles) / len(trades), 4), windows=windows)
    (out / "benchmark.json").write_text(json.dumps(bench, indent=1), encoding="utf-8")
    print("fills", len(fills), "trades", len(trades), "candle sets", len(candles))
    print(json.dumps(windows, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
