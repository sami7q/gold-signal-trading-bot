# Operations

## Startup

`app/lifecycle.py` runs as a FastAPI `lifespan`:

1. Configure structured logging.
2. Create tables if `DATABASE_URL` is SQLite (dev/test convenience only — Postgres always uses
   Alembic, never auto-create).
3. Ensure a default `TradingSettings` row exists (`SIMULATION`, `auto_trading=False`).
4. Write a `SERVICE_STARTED` audit event including the resolved trading mode and integration
   flags — the first thing to check in the Logs page after any deploy or restart.
5. Start the mock signal source unconditionally (used by `/api/health/signal-source` and available
   for programmatic use).
6. If `TELEGRAM_ENABLED=true`, start `TelegramSignalSource` — failure here is caught and logged,
   never fatal.
7. If `MT5_ENABLED=true`, connect `MT5Broker` and make it the active broker — failure here is
   caught and logged, the app falls back to the Paper Broker.
8. Run startup reconciliation against whichever broker just became active (see below) — always
   last, so it never reconciles against a broker that's about to be replaced by a successful MT5
   connection a moment later.

## Startup reconciliation

`app/trading/reconciliation.py::ReconciliationService.reconcile_open_trades()` runs on every
startup, right after step 7 above. It checks every database `Trade` with `status=OPEN` against
`broker.get_position(...)` and never leaves a mismatch looking silently healthy:

```text
broker confirms it's still open        → left as OPEN                    (POSITION_CONFIRMED)
broker has real close data for it      → resolved to CLOSED_TP/SL/MANUAL (POSITION_CONFIRMED)
broker has no record of it, or its own
  query fails                          → TradeStatus.UNKNOWN,
                                          close_reason=RECONCILIATION_REQUIRED
                                                                          (POSITION_MISSING +
                                                                           RECONCILIATION_REQUIRED)
```

`RECONCILIATION_STARTED`/`RECONCILIATION_COMPLETED` bracket the whole pass in the audit log with a
summary count — check the Logs page after any restart that follows a period with open trades. A
trade marked `UNKNOWN` still blocks `max_open_trades` (see
[03-trading-engine.md](03-trading-engine.md#unknown-trades-still-consume-risk-capacity)) and has
no automated resolution path yet — it needs a human to look at the broker/account directly and
close it out (see "Manual emergency stop" below), matching PROJECT_SPEC.md section 21: when
something is unknown, the system's job is to stop and say so, not to guess.

This is the mechanism that makes Paper Broker restarts safe to test against (see
[04-paper-broker.md](04-paper-broker.md)) and is written to need zero changes once MT5 is active.

## Shutdown

Reverse order: stop the mock source, stop Telegram if running, disconnect MT5 if connected, write
a `SERVICE_STOPPED` audit event.

## Restart safety

Restarting the backend does not duplicate a trade: the idempotency key
(`execution_key = "{signal_id}:{strategy_version}"`) is checked against the database before any
new order is submitted, regardless of what was in memory before the restart
(PROJECT_SPEC.md section 9 acceptance criteria). The in-memory Paper Broker's state (open
positions, balance) *does* reset on restart — expected for a paper broker used in dev/test, and no
longer silent: startup reconciliation (above) catches exactly this and marks the affected trade
`UNKNOWN` instead of leaving it looking like a healthy `OPEN` position. A real MT5 connection has
no such memory-loss gap since positions live on the broker's server, not in this process — but
reconciliation still runs against it too, as a general safety net.

## Failure recovery

Every external dependency failure degrades instead of crashing the process:

| Failure | Behavior |
|---|---|
| Database unreachable at a request | The request 500s; no trade is created (SQLAlchemy raises before any `Trade` row is written) |
| Broker unhealthy when a signal is READY | `TradingRulesEngine` returns `BROKER_UNAVAILABLE`, a `REJECTED` trade is recorded, nothing is sent anywhere |
| Broker rejects an order (graceful) | `Trade` → `FAILED`, never a fake `OPEN` |
| Broker raises an exception mid-submission (timeout, disconnect) | Caught by `TradingEngine._fail_from_exception` → `Trade` → `FAILED`, `BROKER_ERROR` + `TRADE_FAILED` audit events, never stuck in `SUBMITTING` forever — see [03-trading-engine.md](03-trading-engine.md#broker-exceptions-vs-graceful-rejections) |
| A details/SL message can't be uniquely matched to one waiting signal | `AMBIGUOUS_SIGNAL_DETAILS` logged, no signal changes, no trade — see [02-signal-parser.md](02-signal-parser.md#multi-signal-ambiguity-assembly-not-parsing) |
| An `OPEN` trade's position can't be found on the broker after a restart | Startup reconciliation marks it `UNKNOWN` — see above — instead of leaving it looking healthy |
| Telegram disconnects | `TELEGRAM_DISCONNECTED` + `TELEGRAM_RECONNECTING` logged, `/api/health/signal-source` reports `connected: false`; Telethon auto-reconnects and `TELEGRAM_CONNECTED` is logged when it does — see [09-telegram-integration.md](09-telegram-integration.md#reconnect-behavior) |
| MT5 connection drops mid-session | Not yet auto-reconnected — see the note in [10-mt5-integration.md](10-mt5-integration.md); `health()` will start reporting unhealthy, which the monitoring in [11-deployment.md](11-deployment.md) should catch |

## Manual emergency stop

There is no dedicated "kill switch" endpoint yet. The fastest manual stop today is
`PATCH /api/settings {"auto_trading": false}` — the rules engine's very first check
(`AUTO_TRADING_DISABLED`) blocks every new trade immediately; open positions are unaffected and
still close normally on TP/SL. Stopping the process entirely also works (no order can be placed
with the process down), but does not close any already-open real-money position — that must be
done from the broker/terminal directly. A dedicated "close all open positions" endpoint is a
recommended addition before Live mode (see
[14-live-readiness-checklist.md](14-live-readiness-checklist.md)).

A `Trade` marked `UNKNOWN` (`close_reason=RECONCILIATION_REQUIRED`) has no automated resolution —
check the broker/terminal directly for that position's actual status, then correct the `Trade` row
by hand (there is no dashboard action for this yet; a manual DB update or a future dedicated
endpoint). It will keep consuming a `max_open_trades` slot until then, which is the safe default,
not a bug.

## Telegram connection lifecycle (Phase 11)

When `TELEGRAM_ENABLED=true`, `TelegramSignalSource` becomes the active signal source
(`app/core/runtime.py::get_active_signal_source`) once it connects successfully; until then, and
if it never connects, the mock source remains active — `/api/health/signal-source` always reflects
whichever one is actually in use, never a hardcoded assumption.

A background task polls the underlying Telethon client's connection state every
`TELEGRAM_HEALTH_POLL_SECONDS` (default 15s) purely to turn connect/disconnect transitions into
audit events (`TELEGRAM_CONNECTED` / `TELEGRAM_DISCONNECTED` / `TELEGRAM_RECONNECTING`) and update
`/api/health/signal-source`. It never re-registers Telethon's message handlers, so a reconnect
cannot cause duplicate message dispatch — actual reconnection is handled by Telethon's own
`auto_reconnect`. See [09-telegram-integration.md](09-telegram-integration.md#reconnect-behavior)
for the full event sequence and the dashboard status strip fields this drives.

`TELEGRAM_OBSERVE_ONLY=true` (default) additionally stops any Telegram-sourced signal one step
short of the trading engine while the real channel's message grammar is still being validated —
see [09-telegram-integration.md](09-telegram-integration.md#telegram_observe_only-phase-11-safety-gate).

## Backup / restore

Not yet documented — this must be written before Live readiness. At minimum: routine `pg_dump` of
the Postgres database (all trading history and settings live there) and a copy of the Telegram
session file (`TELEGRAM_SESSION_PATH`) stored somewhere other than the VPS itself.
