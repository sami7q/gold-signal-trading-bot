"""Inputs for `loco-copy replay --source db`: Loco's fills as this bot recorded them, and candles fetched from
Hyperliquid (cached under data/candles/). Hyperliquid serves only the latest ~5000 candles per interval, so the
finest interval that still reaches back to the trade is used."""

from __future__ import annotations

import json
import time
from decimal import Decimal

import httpx

from loco_copy.config import Settings
from loco_copy.models import LeaderFill
from loco_copy.replay import Candle, CandleSource, TripCandles, episode_ends
from loco_copy.store import Store

INTERVALS = [("1m", 60_000), ("5m", 300_000), ("15m", 900_000), ("1h", 3_600_000), ("4h", 14_400_000),
             ("1d", 86_400_000)]


def db_fills(store: Store, leader: str | None = None) -> list[LeaderFill]:
    q, args = "SELECT * FROM fills", []
    if leader is not None:
        q, args = q + " WHERE leader=?", [leader]
    rows = store._db.execute(q + " ORDER BY time_ms, tid", args)  # noqa: SLF001 - read-only export
    return [LeaderFill(coin=r["coin"], side=r["side"], px=Decimal(r["px"]), sz=Decimal(r["sz"]),
                       start_position=Decimal(r["start_position"]), time_ms=r["time_ms"], tid=r["tid"], oid=r["oid"],
                       received_at_ms=r["time_ms"]) for r in rows]


def hyperliquid_equity_history(settings: Settings, address: str) -> list[tuple[int, Decimal]]:
    """The leader's allTime accountValueHistory (W(t) for a cap20 replay)."""
    raw = httpx.post(settings.hl_info_url, json={"type": "portfolio", "user": address}, timeout=30).json()
    periods = {name: data for name, data in raw} if isinstance(raw, list) else {}
    return [(int(t), Decimal(str(v))) for t, v in (periods.get("allTime") or {}).get("accountValueHistory") or []]


def trip_candles(fills: list[LeaderFill], candles: CandleSource) -> TripCandles:
    """Candles per leader trade ("coin|t0"), from its open to its end."""
    return {f"{coin}|{t0}": candles(coin, t0, t1) for (coin, t0), t1 in episode_ends(fills).items()}


def pick_interval(start_ms: int, now_ms: int) -> tuple[str, int]:
    for name, step in INTERVALS:
        if now_ms - start_ms <= step * 4500:
            return name, step
    return INTERVALS[-1]


def hyperliquid_candle_source(settings: Settings) -> CandleSource:
    cache = settings.data_dir / "candles"
    cache.mkdir(parents=True, exist_ok=True)
    http = httpx.Client(timeout=30)

    def source(coin: str, t0: int, t1: int) -> list[Candle]:
        interval, step = pick_interval(t0, int(time.time() * 1000))
        path = cache / f"{coin}_{interval}_{t0}_{t1}.json"
        if path.exists():
            raw = json.loads(path.read_text())
        else:
            raw, t = [], t0 - step
            while t < t1:
                page = http.post(settings.hl_info_url, json={"type": "candleSnapshot", "req": {
                    "coin": coin, "interval": interval, "startTime": t, "endTime": t1}}).json()
                if not page:
                    break
                raw += [[int(c["t"]), c["o"], c["h"], c["l"], c["c"]] for c in page]
                nt = int(page[-1]["t"]) + step
                if nt <= t:
                    break
                t = nt
                time.sleep(0.2)
            path.write_text(json.dumps(raw))
        return [(int(c[0]), Decimal(str(c[1])), Decimal(str(c[2])), Decimal(str(c[3])), Decimal(str(c[4])))
                for c in raw]

    return source
