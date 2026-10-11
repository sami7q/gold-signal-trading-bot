# loco-copy

Watches Loco's Hyperliquid wallet (`0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1`) in real time and copies his
main-dex perp trades to **one** exchange account: Paper → Bybit demo → Bybit live. Built from `../botup.md`.
Fully independent of the gold bot: no imports from it, own venv, own `.env`, own SQLite file. The folder can be
moved to its own repository unchanged.

*Not financial advice.*

---

## ⚠ Strategy finding — read before any demo or live step

The research numbers in botup.md (90 days: 130 trades, 1 liquidation, 9.9 % max drawdown) **cannot be reached
by the strategy as specified.**

The research priced every copied trade at Loco's *volume-weighted average entry, including his adds*. This bot
ignores adds (owner decision #3), so it enters at his *first* fill. Loco scales into about half of his positions,
often several percent lower — and at 20x, a −4.5 % move from our entry is a liquidation while he keeps adding.

Same simulator, same trades, same candles, only the entry price changed (research code, re-run 2026-10-10):

| Window | Entry used | TP | Liquidations | Max DD | $10,000 becomes |
|---|---|---|---|---|---|
| 90 d | Loco's average incl. adds (published) | 86 | **1** | 9.9 % | $799,369 |
| 90 d | Loco's first fill (**what this bot gets**) | 54 | **22** | 65.5 % | $14,437 |
| 180 d | published | 135 | 3 | 12.5 % | $9.5 M |
| 180 d | first fill | 75 | 47 | 84.7 % | **$2,985** |
| all | published | 248 | 10 | 18.8 % | $2.2 B |
| all | first fill | 145 | 87 | 93.4 % | **$2,389** |

Lower leverage does not rescue it (first-fill entry, TP50):

| Leverage | 90 d | 180 d | all (Oct 2025 →) |
|---|---|---|---|
| 20x | $14,437 (DD 66 %) | $2,985 (DD 85 %) | $2,389 (DD 93 %) |
| 10x | $8,906 (DD 65 %) | $3,025 (DD 71 %) | $5,934 (DD 71 %) |
| 5x | $12,759 (DD 26 %) | $8,717 (DD 34 %) | $14,183 (DD 41 %) |
| 3x | $11,946 (DD 14 %) | $10,321 (DD 15 %) | $12,552 (DD 26 %) |

The bot's own replay of the recorded fills reproduces the first-fill numbers exactly for 90 and 180 days
(`tests/test_replay_matches_backtest.py`). The edge in the research came from Loco's averaging-down, which this
strategy deliberately does not follow. **Decide the strategy before going past paper/observe mode** — for example
following adds (which also changes the risk per episode) needs new research first.

---

## Status by phase (botup.md §13)

| Phase | Code | Still to do (operations) |
|---|---|---|
| P0 skeleton | ✅ config, store, CLI, `check.ps1` / `make check` | — |
| P1 feed, observe only | ✅ WS + backfill + snapshots + tracker, live-tested against Hyperliquid | 72 h unattended run |
| P2 paper | ✅ paper broker, rules, sizing, engine, reconciler, risk guard, replay | 7 days paper; **strategy decision above** |
| P3 Bybit demo | ✅ adapter (REST) + mocked tests + demo contract tests | demo keys (`set-key`), run `pytest -m demo`; private WS not built (REST polling instead) |
| P4 30-day demo / P5 live | — | blocked by the strategy finding |

Quality gate: `.\check.ps1` (Windows) or `make check` — ruff, mypy, 92 tests.

---

## Setup

```powershell
cd loco_copy
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env          # safe defaults: nothing is traded
.\check.ps1
```

## Commands

| Command | Does |
|---|---|
| `loco-copy run` | start the bot (one instance per data folder — a lock file prevents two) |
| `loco-copy status` | process, WS, last frame/snapshot age, lag, equity, open copies, kill switch |
| `loco-copy loco` | Loco's live positions next to ours, and whether each is copyable |
| `loco-copy trades [--open] [--since 7d]` | copies: Loco price vs ours, slippage, latency, TP, exit, PnL |
| `loco-copy events [--since 1d]` | every Loco event and the decision (copied / rejected + reason) |
| `loco-copy kill [--close-all]` | kill switch (+ `data/STOP`); `--close-all` closes every copy reduce-only |
| `loco-copy resume` | release kill switch, remove `data/STOP`, reset the leader breaker |
| `loco-copy report --since 7d` | CSV + markdown of the §11 go/no-go metrics in `data/exports/` |
| `loco-copy replay --source fixtures` | recorded fills through the real pipeline (or `--source db --from 30d`) |
| `loco-copy set-key bybit-demo` | API key/secret into the OS keyring (hidden prompt; never in `.env`) |

Emergency without the CLI: create the file `data/STOP` — no new opens; exits continue.

## Modes (all set in `.env`)

| Goal | Settings |
|---|---|
| Watch Loco only (P1) | `COPY_ENABLED=false` (default) — events logged as `DISABLED` |
| See exactly what would be copied, no orders | `COPY_ENABLED=true`, `COPY_OBSERVE_ONLY=true` |
| Paper trading | `COPY_ENABLED=true`, `COPY_OBSERVE_ONLY=false`, `BROKER=paper` |
| Bybit demo | `BROKER=bybit`, `BROKER_ENV=demo`, keys via `set-key bybit-demo` |
| Bybit live | also `BROKER_ENV=live` + `LIVE_CONFIRM="I ACCEPT REAL MONEY RISK"`; start-up refuses otherwise |

Paper trading uses Bybit's public instrument list (real symbols, qty steps, listings) and Hyperliquid mid prices.

## Running as a service

Separate from every gold-bot task. Windows: a scheduled task `LocoCopy` that runs
`<path>\loco_copy\.venv\Scripts\loco-copy.exe run` at log-on with restart-on-failure. Linux: a systemd unit
with `Restart=always`. Daily: `loco-copy status` and `loco-copy events --since 1d`; weekly: `loco-copy report`.

## Choices made where botup.md left room

- **Opens** are a marketable LIMIT + IOC at mark × (1 ± `MAX_ORDER_SLIPPAGE`) with the TP attached
  (`tpslMode=Full`): fills like a market order but never worse than the cap.
- **Drift is never acted on from a single snapshot**: a disagreement must repeat on the next snapshot, and a coin
  that received a fill after the snapshot request is skipped — so a fill still in flight on the WS never causes a
  false resync. A fill whose `startPosition` disagrees with the tracker is also treated as drift.
- **Extra rejections**: `LATENCY_BREAKER` (rule 2) and `COIN_ALREADY_OPEN` (one-way mode can't hold two copies of
  one symbol). Rejections are recorded in `events`, not as `copies` rows.
- **Daily loss limit** is per UTC day and resets on a new day even before equity is refreshed.
- **Leader breaker** ignores an account value of 0 (unified wallets can report that).
- **Replay** treats Loco's own fills as traded prices for TP/liquidation checks, which also covers trades that
  open and close inside one coarse candle.
- Not implemented: `PROTECTIVE_SL_PCT` (start-up refuses a value), `FOLLOW_ADDS=true` (refused), Bybit private WS.

## Fixtures

`tests/fixtures/` holds Loco's recorded fills and per-trade candles, copied once from the research project by
`tools/build_fixtures.py`, plus `benchmark.json` with both benchmark variants. Nothing reads the research
project at runtime.
