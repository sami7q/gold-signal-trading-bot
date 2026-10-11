# botup.md — loco-copy v3: copy Loco **and** 0x3ea004bb (multi-leader)

> **Audience:** whoever implements the next step inside `loco_copy/`. Read `loco_copy/README.md` first — it
> documents what is built today. This file is the plan for what comes next.
>
> **Revised:** 2026-10-11, after a full read of `loco_copy/` (code, tests, fixtures, git history) and of the
> research project. It supersedes the v2 plan ("Loco only"), which is in git history (commit `c3cba11`).
>
> **Hard rules (unchanged):**
> - simulation first, demo second, real money last;
> - exits are never blocked;
> - never guess (drift → resync + unsynced);
> - keys only in the OS keyring;
> - not financial advice.

---

## 0. Where the project is today (verified 2026-10-11)

| Area | State |
|---|---|
| `loco_copy/` package | P0–P3 **code complete**: config, SQLite store (schema v2), Hyperliquid WS feed + REST backfill + snapshots, tracker, 14 rules (+ `LATENCY_BREAKER`, `COIN_ALREADY_OPEN`), sizing, engine (idempotent `client_id`s), reconciler, risk guard, paper broker, Bybit REST adapter, replay, CLI, metrics/report. Quality gate `check.ps1` (ruff + mypy + tests). |
| Dashboard | The gold-bot dashboard was rebuilt for loco-copy. The pages are `/`, `/loco`, `/trades`, `/events`, `/analytics`, `/logs` and `/settings`. API: `loco-copy api` (FastAPI, `web.py`) on 127.0.0.1:8000 behind the Cloudflare tunnel. It has cookie sessions, a CSRF origin guard, and mode switching WATCH / OBSERVE / EXECUTE (EXECUTE needs `ENABLE EXECUTION`). Vercel builds only `dashboard/` (`.vercelignore`). |
| Uncommitted work | `chain_order()` in `tracker.py` (orders same-millisecond fill bursts by `startPosition` chain; fixes the BTC drift of 2026-10-11) plus `opened` flag / unsynced-exit fixes, `feed.py` / `replay.py` using it, and new tracker tests. **Commit these before starting v3.** |
| Local DB (`loco_copy/data/loco.db`) | Last heartbeat 2026-10-10 20:52 UTC: paper broker, mode **WATCH**, WS connected, 30 fills, 0 events, 0 copies. (If the bot runs elsewhere, e.g. the VPS, that DB is the source of truth — check it there.) |
| Gold bot (`backend/`) | Untouched by loco-copy. Still independent. |

---

## 1. Critical finding — read before adding any leader

The research priced each copied trade at the leader's **volume-weighted entry including his adds**. The bot
ignores adds (owner decision), so it enters at the leader's **first fill**. `loco_copy/README.md` proved this for
Loco. I re-ran the same research simulator for **both** leaders. Settings: same candles, TP50, 10 % margin,
fixed $1,000 margin for the "fixed" column, compounding for the "$10k →" column.

| Leader / window | Entry | TP | Liq | Fixed-margin profit | Fixed DD | $10k → (compounding) |
|---|---|---|---|---|---|---|
| Loco 90 d | avg incl. adds (published) | 86 | 1 | +$45,888 | 3.4 % | $799,369 |
| Loco 90 d | **first fill (bot)** | 54 | **22** | **+$6,612** | 34.7 % | $14,437 |
| Loco 365 d | first fill (bot) | 142 | **86** | **−$9,122** | 94.8 % | $2,286 |
| 0x3ea004bb 90 d | avg incl. adds (published) | 13 | 4 | +$16,553 | 9.4 % | $49,976 |
| 0x3ea004bb 90 d | **first fill (bot)** | 5 | **14** | **−$3,859** | 42.1 % | $6,166 |
| 0x3ea004bb 180 d | first fill (bot) | 18 | **32** | −$5,521 | 62.9 % | $4,570 |
| 0x3ea004bb 365 d | first fill (bot) | 61 | **68** | **−$9,106** | 91.2 % | $1,476 |

Lower leverage does not make 0x3ea004bb profitable when the bot enters at his first fill. Fixed-margin profit:

| Leverage | 90 d | 180 d | 365 d |
|---|---|---|---|
| 10x | +$604 | −$239 | −$9,400 |
| 5x | −$729 | +$242 | −$2,356 |
| 3x | −$91 | +$543 | +$135 |

How often each leader adds to his positions:

- Loco: 202 of 425 positions.
- 0x3ea004bb: 519 of 671 positions (**77 %**).

0x3ea004bb's edge *is* averaging down. Copying only his first fill at 20x keeps his losers and loses his
recoveries.

**Consequence for this plan:** the multi-leader code is worth building now. It is the same work whatever
strategy wins, and WATCH/OBSERVE data is free. But **neither leader may go to EXECUTE on demo or live until
decision D-1 below is made and backed by new research.** The code enforces this with a per-leader execution
gate (§5.3).

**Collision finding** (relevant to the account layout). Both leaders mostly trade HYPE long. On one Bybit
account in one-way mode, an open on a coin the other leader already holds would be blocked
(`COIN_ALREADY_OPEN`). Measured on recorded fills:

- **Last 90 d:** 54 of 197 0x3ea004bb opens would be blocked by an open Loco position on the same coin, and 27 of 130 Loco opens by 0x3ea004bb.
- **Last 365 d:** 167 of 654 and 155 of 420.

That is 25–37 % of opens lost. This is why §3 uses one Bybit **sub-account per leader**.

---

## 2. Decisions for the owner

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **D-1** | What to do with leader adds | (a) keep ignoring adds (research says it loses at 20x); (b) **follow adds with a cap** (same fraction as the leader, total episode margin ≤ `MAX_EPISODE_MARGIN_PCT`); (c) lower leverage only (≈ break-even in research) | Run research R-2 (§9) for (b) on both leaders **before** choosing. Until then: WATCH/OBSERVE only. |
| **D-2** | Account layout for EXECUTE | (a) one Bybit account shared by both leaders, so 25–37 % of opens collide; (b) **one Bybit sub-account per leader** (Bybit supports demo trading per sub-account, with a separate demo account and keys) | (b) |
| **D-3** | Capital split | e.g. 50/50, or by research result | Decide after R-2 |
| **D-4** | Label for 0x3ea004bb | It has **no public display name** (Hyperliquid leaderboard, Hyperdash, HypurrScan all show none). Internal label only. | `3ea` (internal label, not a real name) |

---

## 3. Target architecture: one process, N "lanes"

A **lane** is one leader copied into one account:

```text
                              ┌──────────── lane "loco" ────────────┐
Hyperliquid WS (1 conn) ──┬──▶│ tracker → rules → engine → broker A │──▶ Bybit sub-account A (or paper A)
  userFills × N leaders   │   │ risk(lane) · reconciler(lane)       │
  + snapshots per leader  │   └─────────────────────────────────────┘
                          │   ┌──────────── lane "3ea" ─────────────┐
                          └──▶│ tracker → rules → engine → broker B │──▶ Bybit sub-account B (or paper B)
                              │ risk(lane) · reconciler(lane)       │
                              └─────────────────────────────────────┘
Shared:
- one SQLite DB (every row carries `leader`);
- one notifier;
- one global kill switch;
- one dashboard API.
```

Why lanes, and not "two copies of the folder":

- one WS connection and one weight budget for Hyperliquid;
- one DB and one dashboard;
- one kill switch for everything, plus a per-lane pause;
- yet zero cross-talk: separate tracker, broker, risk state and positions per lane, so there are **no symbol collisions**.

**Fallback if v3 slips:** run a second instance with its own data folder and env overrides. Variables:
`LEADER_ADDRESS=0x3ea…`, `DATA_DIR=data_3ea`, `API_PORT=8001`, plus a separate keyring service. This works
today for WATCH/OBSERVE, but it needs a second dashboard backend. Use it only as a stopgap.

---

## 4. Configuration (`.env`)

```env
# v3: leaders as label:address pairs. LEADER_ADDRESS (v2) still works and means "loco:<address>".
LEADERS=loco:0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1,3ea:0x3ea004bbb1004936e526bf97cbd796f0c78273d7

# Optional per-lane overrides: <SETTING>__<LABEL>. Anything not overridden uses the global value.
RISK_PER_TRADE__3EA=0.10
LEVERAGE__3EA=20
PAPER_START_EQUITY__3EA=10000
MAX_OPEN_POSITIONS__3EA=10
LEADER_DD_BREAKER_PCT__3EA=0.20
ALLOW_EXECUTE__LOCO=false        # §5.3 execution gate -- EXECUTE refused until true (D-1)
ALLOW_EXECUTE__3EA=false
```

Rules:

- **Labels:** `[a-z0-9]{2,12}`, unique.
- **Addresses:** validated like today. A new label's mode defaults to **WATCH**.
- **Global-only settings:** `BROKER`, `BROKER_ENV` and `LIVE_CONFIRM` (live requires the phrase for every lane). The kill switch, `DATA_DIR`, API and alerts are global too.
- **Keys per lane:** keyring service `loco-copy-bybit-<env>-<label>`. Lane `loco` falls back to the v2 name `loco-copy-bybit-<env>`, so existing keys keep working.
- **Setting keys:** `loco-copy set-key bybit-demo --leader 3ea`.

---

## 5. Code changes, file by file

### 5.1 `config.py`

- `Leader(label, address)` dataclass.
- `Settings.leaders: list[Leader]` is parsed from `LEADERS`. If it is empty, it is built from `leader_address` as `loco`.
- `Settings.for_lane(label) -> Settings` returns a copy with the `__<LABEL>` overrides applied.
- Per-lane override parsing reads `os.environ` and the `.env` file for keys matching `^(.+)__([A-Z0-9]+)$`.
- Validation:
  - unique labels and addresses;
  - an unknown override key fails start-up (no silent typos);
  - `follow_adds` stays refused until D-1.
- `with_mode_override(base, override, label)`: the mode is stored **per lane** (`mode_override:<label>`).

### 5.2 Store: schema v3 (`store.py`), in-place migration

| Table | Change |
|---|---|
| `fills` | add `leader TEXT NOT NULL`; **PK becomes (leader, tid)**, because a trade between the two leaders has the same `tid` on both sides |
| `events` | add `leader TEXT NOT NULL`; index `(leader, detected_at_ms)` |
| `copies` | add `leader TEXT NOT NULL`; UNIQUE becomes `(leader, coin, episode_id)` |
| `orders` | unchanged (`client_id` already unique; it now starts with the label) |
| `state` | keys become lane-scoped where they are per lane, see the list below |

Lane-scoped state keys:

- `tracker:<label>`
- `last_fill_ms:<label>`
- `leader_snapshot:<label>`
- `leader_peak:<label>`
- `leader_breaker_tripped:<label>`
- `paper_broker:<label>`
- `mode_override:<label>`
- `day_start_*:<label>`
- `runtime` gets a `lanes` map

Global state keys: `kill_switch` and `close_all_requested`, plus a new `close_all_requested:<label>` per lane.

Migration (v2 → v3), one transaction at start-up:

1. Create the `*_v3` tables.
2. Copy rows with `leader='loco'`.
3. Drop the old tables, then rename.
4. Rename the state keys (`tracker` → `tracker:loco`, …).
5. Set `schema_version=3`.

Back up `loco.db` first (`loco-copy backup`, new command: SQLite online backup to `data/backups/`). The test
migrates a v2 fixture DB and checks the row counts and the state keys.

All store methods that read or write those tables get a `leader` argument:

- `insert_fill(leader, f)`
- `has_fill(leader, tid)`
- `max_fill_time(leader)`
- `get_copy(leader, coin, ep)`
- `live_copies(leader=None)`
- `open_copy_for_coin(leader, coin)`
- `events_since(since, leader=None)`

### 5.3 Execution gate (new, in `engine.py` / `rules.py`)

- New rejection `EXECUTION_NOT_ALLOWED`. It applies when the lane mode is EXECUTE and `ALLOW_EXECUTE__<LABEL>` is false.
- In that case the open is recorded as `OBSERVED_ONLY` with `detail="execution gate"`. It is **not** silently traded.
- The dashboard refuses EXECUTE for that lane with an Arabic message pointing to D-1.
- Exits are never gated.

### 5.4 `feed.py`

- One WS connection subscribes `userFills` for every leader: one `subscribe` message per user.
  - Dispatch by `data["user"]` (lower-cased) to that lane's handler.
  - Unknown users are ignored and logged.
  - *Verify against the official WS docs that `userFills` frames carry `user`.* If not, use one WS per leader.
- Backfill after (re)connect runs **per lane** from `last_fill_ms:<label>`.
- `HyperliquidClient` is shared, so one weight budget covers all lanes. Snapshots run per lane, staggered (`LEADER_SNAPSHOT_SECONDS / N` apart).
- The `chain_order()` fix applies per lane: group by (leader, time, coin).

### 5.5 `tracker.py`

- No logic change. One `LeaderTracker` per lane.
- Also exclude `#NNN` coins (Hyperliquid outcome contracts) in `is_main_perp()`. The research project found them on 2026-10-11: no candles, not on Bybit.

### 5.6 `engine.py`

- `CopyEngine` gets `leader: str`.
- `client_id`s become `f"{label}:{coin}:{episode}:open|close|reduce:{tid}"`. Lane `loco` keeps the current `loco:` prefix, so existing orders and recovery keep working.
- Bybit `orderLinkId` is a hash of the `client_id` (already the case), so labels need no extra room.
- Every store call passes the lane label.
- `COIN_ALREADY_OPEN` stays per lane. With separate sub-accounts there is no cross-lane collision.
  - In the stopgap single-account layout, add a **global** check: an open on a symbol held by another lane is rejected with `COIN_HELD_BY_OTHER_LEADER` (new rejection).

### 5.7 `risk.py`

- `RiskGuard(lane)` owns the per-lane state:
  - daily loss on that lane's equity;
  - the leader breaker on that leader's account value;
  - a per-lane pause (`kill:<label>`).
- The global kill switch and `data/STOP` still block every lane.
- The latency breaker is shared, because it watches one WS.
- `resume --leader <label>` releases only that lane. `resume` with no argument releases everything (as today).

### 5.8 `reconcile.py`

- Per lane: positions and closed PnL come from that lane's broker.
- The missed-exit check uses that lane's tracker.
- `ORPHAN_POSITION` is per account.

### 5.9 `app.py`

- `App` builds `lanes: dict[str, Lane]`. Each `Lane` holds:
  - `settings` (`for_lane`)
  - `tracker`
  - `broker` (`PaperBroker` or `BybitBroker` with that lane's keys)
  - `engine`
  - `reconciler`
  - `risk`
- One feed and one `paper_price_loop`, which pushes mids to every paper broker.
- Start-up order is unchanged but runs per lane. A **new lane starts by seeding from a snapshot**: everything the new leader already holds is pre-existing and is never copied.
- `control_loop` reads `mode_override:<label>` per lane.
- The heartbeat gets a per-lane block: equity, available, open copies, mode, snapshot age, breaker.

### 5.10 `cli.py`

- New `--leader <label>` filter on `status`, `trades`, `events`, `report`, `replay`, `kill`, `resume` and `set-key`.
- New command `leaders`: label, address, mode, execution gate, equity, open copies, last snapshot.
- `loco` becomes `positions --leader <label>`; `loco` stays as an alias for lane `loco`.
- New command `backup`.

### 5.11 `web.py`: API (keep v2 routes working)

| Route | Change |
|---|---|
| `GET /api/leaders` | **new**: per lane `{label, address, name, mode, allow_execute, equity, open_copies, breaker, snapshot_at}` |
| `GET /api/leaders/{label}/positions` | **new**; `/api/loco` stays as an alias for `loco` |
| `GET /api/overview?leader=` | totals for all lanes, or for one lane |
| `GET /api/copies?leader=`, `/api/events?leader=`, `/api/analytics?leader=` | filter; every row gains `leader` |
| `POST /api/control/mode` | body gains `leader` (required when more than one lane exists) |
| `POST /api/control/kill` / `resume` | optional `leader` (absent = global, as today) |
| `GET /api/settings` | per-lane strategy block + `allow_execute` |

### 5.12 Dashboard (`dashboard/`)

- **Types:** every `CopyTrade` and `LeaderEvent` gets `leader: string`, plus new `Leader` and `LeaderList` types.
- **Leader switcher** in the shell (status strip): "الكل / Loco / 3ea". It filters every page through the API's `leader=` parameter and is remembered per browser.
- **Pages:**
  - `/loco` becomes "مراكز المتداولين" with one tab per leader.
  - `/trades` and `/events` get a leader column and badge.
  - `/settings` shows one card per lane, with mode buttons and the execution gate state. The EXECUTE button is disabled with an explanation while the gate is closed.
- The 3ea lane is labelled **«3ea (لقب داخلي — لا يوجد اسم علني)»**.

### 5.13 Replay, fixtures, tests

- `tools/build_fixtures.py --leader 0x3ea… --label 3ea` writes:
  - `tests/fixtures/3ea_fills.json.gz`
  - `3ea_candles.json.gz`
  - `benchmark_3ea.json` (both variants: published and first_fill)
- `test_replay_matches_backtest.py` is parametrised over both leaders. The pipeline must reproduce `first_fill` for each.
- New tests:

| Test | Asserts |
|---|---|
| `test_config_lanes.py` | parsing, overrides, unknown key → error, v2 `LEADER_ADDRESS` compatibility |
| `test_store_migration_v3.py` | v2 DB → v3, rows and state keys preserved, `(leader, tid)` PK accepts the same tid for two leaders |
| `test_feed_multi.py` | frames dispatched by `user`, unknown user ignored, per-lane backfill |
| `test_lanes_isolation.py` | Loco CLOSE never touches a 3ea copy, per-lane pause, global kill blocks both, exits never blocked |
| `test_execution_gate.py` | EXECUTE + gate closed → `OBSERVED_ONLY` + `EXECUTION_NOT_ALLOWED`; exits unaffected |
| `test_web_leaders.py` | new routes, `leader` filter, mode per lane, CSRF unchanged |

- Existing tests run unchanged against lane `loco`. The quality gate stays `check.ps1`.

---

## 6. Rollout (Loco never stops being watched)

| Step | What | Exit criteria |
|---|---|---|
| M0 | Commit the pending tracker fix; `loco-copy backup`; tag `v2-final` | clean `check.ps1` |
| M1 | v3 code behind `LEADERS` (only `loco` configured), migration v2→v3 | all old tests + new tests green; local DB migrated; Loco lane behaves exactly as before for 24 h |
| M2 | Add `3ea` to `LEADERS` in **WATCH**, then **OBSERVE** | 72 h: no unreported WS gaps, tracker == snapshot for both lanes, dashboard shows both |
| M3 | Research R-1/R-2 (§9) → owner decides D-1, D-2, D-3 | written decision in this file |
| M4 | If D-1 passes: Bybit **demo sub-account per lane**; `set-key bybit-demo --leader …`; `ALLOW_EXECUTE__<LABEL>=true` for demo only | contract tests per lane; 30-day demo targets (§7) per lane |
| M5 | Small real money, one lane at a time | 30 days inside the demo envelope |

---

## 7. Go / no-go per lane (30-day demo)

| Metric | Target |
|---|---|
| Detection latency | p50 < 1 s, p95 < 3 s |
| Entry slippage | median < 10 bps |
| Capture rate | ≥ 90 % (excluding not-listed) |
| Missed exits | 0 |
| Liquidations | within ± 1 of the replay of the same fills (**first-fill** benchmark, or the D-1 variant) |
| Strategy DD | < 25 % |
| Tracking error | live PnL vs replay of the same fills: investigate > 20 % |

---

## 8. Operations

- **Sub-accounts (D-2):**
  - Create one Bybit sub-account per leader (Bybit → Sub-account).
  - Switch each to **Demo Trading**. Each sub-account has its own demo UID and needs its own demo API key.
  - Keys must be trade-only, without withdrawal rights, and IP-whitelisted.
  - A demo account untouched for 30 days is wiped, so keep the bot running or log in.
- **Pause one leader:** `loco-copy kill --leader 3ea` (exits continue). **Everything:** `loco-copy kill` or `data/STOP`.
- **Daily checks:**
  - `loco-copy leaders`
  - `loco-copy events --since 1d --leader 3ea`
  - rejections `EXECUTION_NOT_ALLOWED` / `SYMBOL_NOT_LISTED`
- **Weekly:** `loco-copy report --since 7d --leader <label>` for each lane.

---

## 9. Research that must happen before D-1 (in `hyperliquid_copy_research/`)

| ID | Task | Why |
|---|---|---|
| R-1 | Add `entry="first"` to `hl_common.simulate()` (first-fill entry), and make it the default for every copy-strategy number from now on. Re-run the deep hunt ranking with it. | All published research numbers (Loco, 0x3ea004bb, monthly tables, withdrawal and cap simulations, hunt ranking) used the avg-incl-adds entry and are **not reachable** by a copier that ignores adds. |
| R-2 | Fill-level **mirror simulator**: follow the leader's opens, adds, reduces and closes proportionally. Cap total episode margin at `MAX_EPISODE_MARGIN_PCT` (try 10/20/30 %), TP from our average entry, isolated liquidation per our average entry. Run on both leaders, 90/180/365 d, with candles. | Decides D-1(b). The bot's replay must later reproduce it, exactly as it reproduces `first_fill` today. |
| R-3 | Collision study (done above): 25–37 % of opens collide on a shared one-way account | Decides D-2 |

---

## 10. Security (unchanged + per lane)

- **Keys:**
  - one keyring entry per lane and environment;
  - never in `.env`, git, chats, the dashboard or logs;
  - demo and live keys separate;
  - sub-account keys without withdrawal rights.
- **Live** still requires `BROKER_ENV=live` + `LIVE_CONFIRM` + `ALLOW_EXECUTE__<LABEL>=true` + dashboard `ENABLE EXECUTION`.
- **API:** localhost only behind the tunnel; CSRF origin guard on every POST (unchanged).

---

## 11. References

- `loco_copy/README.md`: strategy finding, commands, current behaviour.
- `loco_copy/tests/test_replay_matches_backtest.py`, `tests/fixtures/benchmark.json`: first-fill benchmark.
- Research project `C:\Users\90539\Desktop\بحث عن متداولين\hyperliquid_copy_research\hyperliquid_copy_research`. First-fill numbers for both leaders were produced on 2026-10-11 with the research `simulate()` and `cached_candles()`. They use the same method as `tools/build_fixtures.py`.
- Bybit demo trading (works per sub-account; sub-account key → demo account under that sub-account): https://bybit-exchange.github.io/docs/v5/demo · https://www.bybit.com/en/help-center/article/FAQ-Demo-Trading
- Hyperliquid WebSocket subscriptions: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions

*Not financial advice. Past performance does not guarantee future results.*
