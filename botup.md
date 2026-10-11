# botup.md — Loco Copy Bot (standalone)

> **Scope:** one job only — watch Loco's Hyperliquid wallet in real time and copy his perp trades
> to one exchange account. Nothing else.
>
> **Leader (fixed):** `0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1` ("Loco").
>
> **Status:** plan only, nothing implemented yet. Revised 2026-10-10. It replaces the earlier
> "integrate into the gold bot" version.
>
> **Owner decisions (2026-10-10):**
> - Venue: **Bybit** (demo, then live).
> - Take profit: **TP50**.
> - Follow Loco's adds: **NO** (ignored, same as the research).
> - Per-coin cap and monthly withdrawal are deferred (not needed now).
>
> **Hard rule:** run in simulation first, then demo, and use real money last. Real money only after
> the targets in §11 are met. Not financial advice.

---

## 0. Decoupling — what this project is NOT connected to

`loco_copy` is a **fully independent project**. It is not a module of the gold bot. It has zero
runtime or code dependency on anything else in this folder or on the research project.

| Removed / not used | Why |
|---|---|
| Gold bot code (`backend/`, `dashboard/`, `worker.py`, rules, engine, services) | No imports from `backend.*` at all. The gold bot stays untouched and is not modified in any file. |
| Telegram signal channel + regex parser | Loco's orders come as structured fills from Hyperliquid, not text |
| MT5 / XAUUSD / lots / pips / Windows-only worker | Perpetual futures need qty steps, leverage and partial closes |
| Shared Postgres, FastAPI hub, auth, Next.js dashboard, Vercel, Cloudflare Tunnel | Own SQLite file + CLI. No web server, no users, no login. |
| Multi-account / multi-leader tables | One leader (Loco) → one exchange account per running instance |
| OKX, Binance, WEEX, Hyperliquid execution | Execution venue is **Bybit only**: Paper → Bybit demo → Bybit live. Hyperliquid is used only as the source of Loco's trades. |
| Research project at runtime | Its recorded Loco fills and candles are **copied once** into `tests/fixtures/` for the replay test. Nothing is read from it at runtime. |

The folder `loco_copy/` can be moved to its own git repo at any time with no changes.
Deleting it would not affect the gold bot, and the reverse is also true.

---

## 1. Pipeline

```text
Hyperliquid WebSocket  (userFills of Loco)  +  REST snapshot every 20 s (clearinghouseState)
        │  LeaderFill / LeaderSnapshot
        ▼
Tracker        — rebuilds Loco's position per coin → OPEN / ADD / REDUCE / CLOSE / FLIP
        ▼
Rules          — ordered safety checks, first failure wins
        ▼
Engine         — size (10 % equity × 20x), idempotent order, TP placement, follow exits
        ▼
Broker         — PaperBroker (real HL prices) → BybitBroker (demo, then live)
        │
Reconciler (30 s) · RiskGuard (kill switch, daily loss, leader breaker) · Store (SQLite) · Notifier
```

There is one asyncio process, `loco-copy run`. Latency target: Loco's fill → our order in under 2 s.

---

## 2. Strategy (exact definition — implement exactly this)

| Parameter | Value | Config key |
|---|---|---|
| Leader | `0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1` | `LEADER_ADDRESS` (single value) |
| Markets | Hyperliquid **main perp dex only**. Ignore spot (`@…`, `X/USDC`) and builder dexes (`xyz:…`). | hard rule |
| Margin per trade | **10 % of current equity** at entry (compounding) | `RISK_PER_TRADE=0.10` |
| Leverage | **20x isolated** | `LEVERAGE=20` |
| Take profit | **TP50 = +2.5 % price from our entry** (reduce-only). | `TAKE_PROFIT_ROI=0.50` |
| Stop | Exit **with Loco**. No own SL by default, so liquidation is at about −4.5 %. | `PROTECTIVE_SL_PCT=` (empty) |
| Loco opens | Open the same side | — |
| Loco adds | **Ignore** — our size is fixed at open (exactly as the research) | `FOLLOW_ADDS=false` |
| Loco reduces | Reduce the **same fraction** (reduce-only) | `FOLLOW_REDUCES=true` |
| Loco closes | Close 100 % and cancel TP | — |
| Loco flips | Close, then open the new side as a new episode | — |
| Positions Loco already holds at start-up | **Never copy** | hard rule |
| Capacity | Skip if free balance is below the required margin. Max 10 open positions. | `MAX_OPEN_POSITIONS=10` |

Benchmarks the demo run is compared against (research, candle-checked, TP50, 20x):


| Window | Trades | Win / loss | Liquidations | Max DD |
|---|---|---|---|---|
| Last 90 days | 130 | 124 / 6 | 1 | 9.9 % |
| Last 180 days | 228 | 205 / 23 | 3 | 12.5 % |
| Full (Oct 2025 →) | 425 | 374 / 51 | 10 | 18.8 % |

Loco profile:

- About 1.4 trades per day.
- Median hold about 14 h.
- 98.5 % long.
- HYPE is about 59 % of profit.
- His current drawdown at data collection was 12.7 %; his historical max is 13.4 % on the perp curve.

---

## 3. Project layout

```text
بوت تداول/
└── loco_copy/                      # self-contained; own venv, own .env, own DB
    ├── pyproject.toml              # python 3.12; deps below
    ├── .env.example                # no secrets
    ├── .gitignore                  # .env, data/, *.db, logs/
    ├── README.md
    ├── loco_copy/
    │   ├── config.py               # pydantic-settings; validates everything at start-up
    │   ├── models.py               # dataclasses: LeaderFill, LeaderSnapshot, LeaderEvent, Decision, Copy, OrderResult
    │   ├── constants.py            # EventType, CopyStatus, Rejection
    │   ├── feed.py                 # HyperliquidFeed: WS userFills + REST backfill + snapshots
    │   ├── tracker.py              # pure state machine (no I/O)
    │   ├── symbols.py              # HL coin -> exchange symbol (+ listing check)
    │   ├── sizing.py               # margin / qty / TP math, rounding
    │   ├── rules.py                # ordered checks
    │   ├── engine.py               # open / reduce / close / flip, idempotent
    │   ├── reconcile.py            # DB vs exchange vs Loco snapshot
    │   ├── risk.py                 # kill switch, daily loss, leader breaker, latency breaker
    │   ├── store.py                # SQLite (WAL) repository
    │   ├── notify.py               # log file (+ optional outbound alert, see §9)
    │   ├── metrics.py              # latency / slippage / capture
    │   ├── brokers/
    │   │   ├── base.py             # Broker ABC
    │   │   ├── paper.py            # fills at live HL mid ± slippage; TP + liquidation simulated
    │   │   └── bybit.py            # v5, demo + live
    │   └── cli.py                  # entry point `loco-copy`
    ├── tests/
    │   ├── fixtures/               # Loco fills + candles copied ONCE from the research project
    │   └── test_*.py
    └── data/                       # loco.db, logs/, exports/ (git-ignored)
```

Dependencies:

- **Runtime:** `httpx`, `websockets`, `pydantic-settings`, `structlog`, `keyring`, `pybit`.
- **Dev:** `pytest`, `pytest-asyncio`, `ruff`, `mypy`.

---

## 4. CLI (the whole user interface)

| Command | Does |
|---|---|
| `loco-copy run` | Start the bot (feed + engine + reconciler) |
| `loco-copy status` | Shows: WS connected, last frame age, lag p50/p95, equity, open copies, kill switch |
| `loco-copy loco` | Loco's live positions (last snapshot) next to ours |
| `loco-copy trades [--open] [--since 7d]` | Copy trades with entry vs Loco price, slippage, latency, result, PnL |
| `loco-copy events [--since 1d]` | Every Loco event and the decision taken (copied / rejected + reason) |
| `loco-copy kill [--close-all]` | Engage the kill switch (no new opens; `--close-all` closes everything reduce-only) |
| `loco-copy resume` | Release the kill switch |
| `loco-copy report --since 7d` | CSV + markdown: live vs backtest metrics (§11) |
| `loco-copy replay --from … --to …` | Run recorded Loco fills through the real pipeline with PaperBroker |
| `loco-copy set-key bybit-demo` | Store API key/secret in the OS keyring (prompted, never echoed) |

The kill switch is the DB flag plus the file `data/STOP`. Creating that file stops new opens even
if the CLI is unavailable.

---

## 5. Interfaces

### 5.1 Feed (`feed.py`)

```python
@dataclass(frozen=True, slots=True)
class LeaderFill:
    coin: str                 # "HYPE", "kPEPE" …
    side: Literal["B", "A"]   # B buy, A sell
    px: Decimal
    sz: Decimal
    start_position: Decimal   # signed size before this fill
    time_ms: int
    tid: int                  # dedupe key
    oid: int | None
    received_at_ms: int

@dataclass(frozen=True, slots=True)
class LeaderSnapshot:
    positions: dict[str, Decimal]   # coin -> signed size
    entry_px: dict[str, Decimal]
    account_value: Decimal
    taken_at_ms: int
```

Requirements:

1. Connect to `wss://api.hyperliquid.xyz/ws` and subscribe to `userFills` for `LEADER_ADDRESS`. The first frame (`isSnapshot: true`) is stored and **never traded**.
2. Ping every 30 s. On any error, reconnect with backoff from 1 s to a 30 s cap.
3. After every (re)connect, backfill with `POST /info {"type":"userFillsByTime","startTime":last_seen_ms+1}`. Dedupe on `tid`.
4. Every `LEADER_SNAPSHOT_SECONDS` (default 20), fetch `clearinghouseState` as a `LeaderSnapshot` (safety net).
5. Drop coins that start with `@` or contain `/` or `:`.
6. Stay under the 1200 weight/min limit.

### 5.2 Tracker (`tracker.py`, pure)

```text
per coin: position (signed Decimal), episode_id, synced

seed from first snapshot: non-zero coin → synced = False (never copied)

on fill:
  new = start_position ± sz
  if not synced:
      if start_position == 0: synced = True (treat normally)
      else: if new == 0 or sign flipped: synced = True ; emit nothing ; continue
  start == 0 and new != 0          → OPEN   (episode_id += 1)
  same sign, |new| > |start|        → ADD    (logged only, never traded)
  same sign, 0 < |new| < |start|    → REDUCE (fraction = (|start|-|new|)/|start|)
  new == 0                          → CLOSE
  sign flipped                      → FLIP   (CLOSE old + OPEN new)
```

On every snapshot, if the tracker disagrees with the snapshot:

- write a `LEADER_STATE_DRIFT` log entry;
- resync that coin from the snapshot;
- mark the coin unsynced, so it gets no new copies until it is flat once. Never guess.

Port the episode logic and the 4 reconstruction test cases from the research project's
`hl_common.reconstruct_trades()`. Copy the code; do not import it.

### 5.3 Broker (`brokers/base.py`)

```python
class Broker(ABC):
    name: str
    def instruments(self) -> dict[str, InstrumentInfo]: ...     # qty_step, min_qty, tick_size, max_leverage
    def account(self) -> Account: ...                            # equity, available
    def mark_price(self, symbol: str) -> Decimal: ...
    def ensure_isolated_leverage(self, symbol: str, leverage: int) -> None: ...
    def market_open(self, symbol, side, qty, *, client_id, max_slippage, take_profit) -> OrderResult: ...
    def market_reduce(self, symbol, position_side, qty, *, client_id) -> OrderResult: ...
    def set_take_profit(self, symbol, price) -> None: ...
    def cancel_take_profit(self, symbol) -> None: ...
    def position(self, symbol) -> Position | None: ...
    def positions(self) -> list[Position]: ...
    def order_by_client_id(self, client_id) -> OrderResult | None: ...   # recovery after crash
    def health(self) -> Health: ...
```

- Adapter calls are synchronous. They run from asyncio via `asyncio.to_thread`.
- One-way mode: one position per symbol.

### 5.4 Bybit notes (verify against the official v5 docs before coding)

- **Endpoints:**
  - Demo: `https://api-demo.bybit.com` and private WS `wss://stream-demo.bybit.com`.
  - Demo keys come from *Demo Trading* on the main site.
  - Funds: `POST /v5/account/demo-apply-money` (max 100k USDT, 1 request per minute).
  - Demo orders are kept 7 days only, so the bot stores everything itself.
- **Instruments:** `GET /v5/market/instruments-info?category=linear`.
- **Leverage and margin:** `set-leverage`; isolated margin mode is confirmed on the demo account.
- **Orders:**
  - Open: `POST /v5/order/create` with `category=linear`, `orderType=Market`, `orderLinkId=<client_id>`, `takeProfit`, `tpslMode=Full`.
  - Exits: `reduceOnly=true`.
  - Recovery: `GET /v5/order/realtime?orderLinkId=`.
- **Symbols:** `HYPE → HYPEUSDT`, `kPEPE → 1000PEPEUSDT`. A coin that is not listed gets `SYMBOL_NOT_LISTED` (logged, never silent).

### 5.5 Bybit live (phase 5)

- Same adapter with `BROKER_ENV=live` (`https://api.bybit.com`, `wss://stream.bybit.com`), separate live keys.
- Live keys are trade-only, withdrawals disabled, with an IP whitelist.
- Start-up refuses live unless `LIVE_CONFIRM` matches (§9).
- Coins Loco trades that Bybit does not list are skipped. They are counted in the capture-rate metric.

---

## 6. Rules (first failure wins)

For an **OPEN** event:

| # | Check | Rejection |
|---|---|---|
| 1 | `COPY_ENABLED` | `DISABLED` |
| 2 | Kill switch off, daily-loss limit not hit, leader breaker not tripped | `KILL_SWITCH` / `DAILY_LOSS_LIMIT` / `LEADER_BREAKER` |
| 3 | Main-dex perp, coin synced | `UNSUPPORTED_MARKET` / `PRE_EXISTING_POSITION` |
| 4 | Side allowed | `SIDE_DISABLED` |
| 5 | Coin not blocked / in allow-list if set | `COIN_BLOCKED` |
| 6 | Symbol listed on the broker | `SYMBOL_NOT_LISTED` |
| 7 | `now − leader_time_ms ≤ MAX_SIGNAL_AGE_MS` (10 000) | `SIGNAL_TOO_OLD` |
| 8 | `abs(mark / leader_px − 1) ≤ MAX_ENTRY_DRIFT` (1 %), and price not already past our TP | `PRICE_DRIFTED` |
| 9 | No open copy for this coin + episode | `DUPLICATE_EPISODE` |
| 10 | Broker healthy | `BROKER_UNAVAILABLE` |
| 11 | Open copies < `MAX_OPEN_POSITIONS` | `MAX_OPEN_POSITIONS` |
| 12 | `available ≥ 10 % × equity` | `INSUFFICIENT_MARGIN` |
| 13 | Notional ≤ `MAX_NOTIONAL_PER_COIN` | `NOTIONAL_CAP` |
| 14 | Qty ≥ `min_qty` after rounding | `QTY_TOO_SMALL` |

- ADD events are recorded as `IGNORED_ADD` and never traded.
- REDUCE, CLOSE and FLIP events skip checks 2, 7, 8 and 11–14: **exits are never blocked**.
- They still need an open copy for that episode. If there is none, the result is `NO_MATCHING_COPY`, logged as INFO.
- `COPY_OBSERVE_ONLY=true` (the default) runs everything, including sizing, and records `OBSERVED_ONLY` rows. It never calls the broker.

---

## 7. Engine

**Sizing**

```text
margin   = equity × 0.10
notional = min(margin × 20, MAX_NOTIONAL_PER_COIN)
qty      = floor((notional / mark) / qty_step) × qty_step
tp       = entry × (1 + 0.50/20)  long   |  entry × (1 − 0.50/20)  short   → rounded to tick
```

After the fill, TP is recomputed from the actual fill price. It is re-sent if it differs by at
least 1 tick.

**OPEN**

1. The idempotency key is `client_id = f"loco:{coin}:{episode_id}:open"`, with a UNIQUE constraint in `orders`. If the key already exists, return the existing result.
2. Write the copy row as `SUBMITTING`.
3. Call `ensure_isolated_leverage`, then `market_open(+TP)`.
4. On success, set status to `OPEN`. On failure, set `FAILED`; a phantom OPEN is never written.
5. If the bot crashes after submitting, at restart it calls `order_by_client_id`.

**REDUCE**

- `qty = floor(qty_open × fraction / step) × step`.
- If the remainder would be below `min_qty`, close everything.
- `client_id = loco:{coin}:{episode}:reduce:{tid}`.

**CLOSE / FLIP**

- CLOSE sends a reduce-only close of everything, cancels TP and sets status `CLOSED_LEADER`.
- FLIP closes, then runs a new OPEN through the full rules.

**Exchange-side exits**

- These are `CLOSED_TP`, `CLOSED_LIQ` and `CLOSED_MANUAL`, detected by the reconciler.
- Later Loco exits for that episode are no-ops.

**Reconciler** (runs at start-up and every 30 s):

| Situation | Action |
|---|---|
| DB says `OPEN`, exchange has no position | Resolve to TP / LIQ / MANUAL from closed PnL, otherwise `UNKNOWN` |
| Exchange position with no DB copy | `ORPHAN_POSITION` critical alert; never auto-closed |
| Loco is flat on a coin but our copy is still open for more than 60 s | **Auto-close** (`CLOSED_RECONCILED`) — the only automatic correction |

---

## 8. Storage (`data/loco.db`, SQLite WAL, Decimal stored as TEXT)

| Table | Columns |
|---|---|
| `fills` | tid (PK), coin, side, px, sz, start_position, time_ms, oid, received_at_ms — append-only |
| `events` | id, coin, episode_id, type, side, fraction, leader_px, leader_time_ms, detected_at_ms, tid, decision, rejection |
| `copies` | id, coin, symbol, episode_id, side, status, qty_initial, qty_open, entry_price, tp_price, exit_price_avg, realized_pnl, fees, funding, opened_at, closed_at, close_reason, leader_entry_px, slippage_bps, latency_detect_ms, latency_order_ms — UNIQUE(coin, episode_id) |
| `orders` | id, copy_id, client_id (UNIQUE), kind, requested_qty, filled_qty, avg_price, fee, status, error, sent_at_ms, filled_at_ms |
| `state` | key / value: kill_switch, day_start_equity, last_fill_ms, tracker positions |
| `log` | id, ts, level, type, payload (json) — every decision and every setting change |

Statuses: `OBSERVED_ONLY, REJECTED, SUBMITTING, OPEN, CLOSED_LEADER, CLOSED_TP, CLOSED_LIQ,
CLOSED_MANUAL, CLOSED_RECONCILED, FAILED, UNKNOWN`.

The schema is created by `store.py` with a simple `schema_version` table. There is no Alembic.

---

## 9. Configuration (`.env`, safe defaults)

```env
LEADER_ADDRESS=0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1
COPY_ENABLED=false
COPY_OBSERVE_ONLY=true
BROKER=paper                     # paper | bybit
BROKER_ENV=demo                  # demo | live  (live also needs LIVE_CONFIRM below)
LIVE_CONFIRM=                    # must equal "I ACCEPT REAL MONEY RISK" for live
RISK_PER_TRADE=0.10
LEVERAGE=20
TAKE_PROFIT_ROI=0.50
PROTECTIVE_SL_PCT=
FOLLOW_ADDS=false                # adds are ignored (owner decision)
FOLLOW_REDUCES=true
MAX_OPEN_POSITIONS=10
MAX_NOTIONAL_PER_COIN=           # deferred (empty = no cap)
MAX_ENTRY_DRIFT=0.01
MAX_SIGNAL_AGE_MS=10000
DAILY_LOSS_LIMIT_PCT=0.15
LEADER_DD_BREAKER_PCT=0.20
LEADER_SNAPSHOT_SECONDS=20
RECONCILE_SECONDS=30
PAPER_START_EQUITY=10000
ALERT_TELEGRAM_TOKEN=            # optional, outbound alerts only, separate bot
ALERT_TELEGRAM_CHAT_ID=
```

- **API keys are never in `.env`.** They live in the OS keyring (`loco-copy set-key`).
- **Alerts** are optional and outbound only (a send-only bot). They do not read any channel.

---

## 10. Risk guard

- **Kill switch.** Covered in §4.
- **Daily loss limit.** If equity falls more than `DAILY_LOSS_LIMIT_PCT` below the UTC day start, new opens stop until the next day.
- **Leader breaker.** If Loco's account value drops more than `LEADER_DD_BREAKER_PCT` from its peak since start (computed from snapshots), new opens pause and an alert is sent.
- **Latency breaker.** If WS frames are older than 60 s, or the snapshot fails 3 times, new opens pause. Exits continue through reconciliation.

---

## 11. Go / no-go metrics (30-day demo)

| Metric | Definition | Target |
|---|---|---|
| Detection latency | `detected_at_ms − leader_time_ms` | p50 < 1 s, p95 < 3 s |
| Order latency | `filled_at_ms − detected_at_ms` | p50 < 1 s |
| Entry slippage | `side × (our_px/leader_px − 1)` | median < 10 bps |
| Capture rate | copied OPENs / Loco main-dex OPENs (excluding not-listed) | ≥ 90 % |
| Missed exits | Loco closed, our copy still open > 60 s | 0 |
| Liquidations | vs backtest on the same Loco trades | within ± 1 |
| Strategy DD | peak-to-trough equity | < 25 % |
| Tracking error | live PnL vs `replay` of the same fills | gaps above 20 % are investigated |

---

## 12. Tests (`tests/`)

| File | What it tests |
|---|---|
| `test_tracker.py` | open/add/reduce/close/flip, pre-existing position never copied, snapshot drift resync, duplicate tid, out-of-order fills |
| `test_sizing.py` | 10 % × 20x, step floor, min qty, notional cap, TP rounding long/short |
| `test_adds.py` | Loco ADD → logged as `IGNORED_ADD`, no order, our qty and TP unchanged |
| `test_rules.py` | one test per rejection, ordering, exits never blocked |
| `test_engine.py` | same event twice → one order; crash after submit recovered; failure never OPEN; reduce maths; flip |
| `test_reconcile.py` | missed close auto-closed, orphan flagged, TP/LIQ resolution |
| `test_symbols.py` | `kPEPE → 1000PEPEUSDT`, not-listed rejection |
| `test_replay_matches_backtest.py` | Recorded Loco fills + candles (fixtures) through tracker → rules → engine → PaperBroker. Trade, TP and liquidation counts must match the research result for the same window. **This is the most important test.** |
| `test_bybit_demo.py` | Contract tests, `@pytest.mark.demo`, skipped unless demo keys are in the keyring |

Quality gate: `pytest`, `ruff`, `mypy` clean.

---

## 13. Phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0 — Skeleton** (1 d) | `loco_copy/` project, config, store, CLI shell, CI-style `make check` | `loco-copy status` runs; tests green |
| **P1 — Loco feed, observe only** (2 d) | feed, tracker, `fills`/`events` persisted, `loco-copy loco`/`events` | 72 h unattended: no unreported gaps, tracker == snapshot |
| **P2 — Paper** (3 d) | PaperBroker, rules, sizing, engine, reconciler, risk guard, replay | replay test passes; 7 days paper with metrics |
| **P3 — Bybit demo** (3 d) | BybitBroker, private WS, symbol mapping | contract tests pass; first real-time copies on demo |
| **P4 — 30-day demo** | operations + weekly `report` | all §11 targets met |
| **P5 — Small real money** (only after P4) | Bybit live, `LIVE_CONFIRM`, tiny capital | 30 days inside the demo envelope |

---

## 14. Running it

- **Where it runs:** any machine with Python 3.12 — the current VPS or a small Linux box. It is a separate service (`LocoCopy` scheduled task on Windows or a systemd unit on Linux) with restart-on-failure. It is independent from all gold bot tasks.
- **Daily:** run `loco-copy status` and `loco-copy events --since 1d`. Look for rejections such as `SYMBOL_NOT_LISTED`, `PRICE_DRIFTED`, `SIGNAL_TOO_OLD`, and any `UNKNOWN` or `ORPHAN_POSITION`.
- **Weekly:** run `loco-copy report --since 7d`.
- **Emergency:** run `loco-copy kill --close-all`, or create `data/STOP`.

---

## 15. Security

- **Exchange keys:** trade-only, withdrawals disabled, with an IP whitelist. Use separate keys for demo and live. Store them in the OS keyring only; never in `.env`, git, chats or tickets.
- **Live mode** needs `BROKER_ENV=live` **and** the exact `LIVE_CONFIRM` phrase. Without both, start-up refuses.

---

## 16. Risks

| Risk | Mitigation |
|---|---|
| Loco is ~98 % long and concentrated in HYPE | `MAX_NOTIONAL_PER_COIN`, daily-loss limit, leader breaker |
| 20x isolated liquidates at about −4.5 % while Loco holds | This is the researched strategy. TP50 had fewer liquidations than TP100 (10 vs 22). `PROTECTIVE_SL_PCT` is available as an experiment. |
| Missed close (WS gap) | Backfill + 20 s snapshots + reconciler auto-close |
| Coins not listed on Bybit (PONS, CASHCAT, PURR …) | Accepted: those trades are skipped and counted (`SYMBOL_NOT_LISTED`, capture rate) |
| Compounding sizes the order book can't absorb | `MAX_NOTIONAL_PER_COIN` |
| Loco changes behaviour or stops | Leader breaker + weekly report |
| Demo ≠ live | Measure fees and slippage on demo; P5 starts tiny |

---

## 17. Decisions

| # | Decision | Answer |
|---|---|---|
| 1 | Execution venue | **Bybit** (demo → live) |
| 2 | Take profit | **TP50** |
| 3 | Follow Loco's adds | **No** — ignored |
| 4 | `MAX_NOTIONAL_PER_COIN` | Deferred — empty for now |
| 5 | Monthly withdrawal | Deferred — handled manually, outside the bot |

---

## 18. References

- Hyperliquid WebSocket subscriptions — https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions
- Hyperliquid info endpoint — `https://api.hyperliquid.xyz/info`
- Bybit demo trading — https://bybit-exchange.github.io/docs/v5/demo
- Research results (for comparison only, not a dependency): `Desktop\بحث عن متداولين\hyperliquid_copy_research\…\reports\2026-10-10_*`

*Not financial advice. Past performance does not guarantee future results.*
