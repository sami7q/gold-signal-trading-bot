# Architecture

## Services

The system is one FastAPI backend process plus one Next.js dashboard process. There is no
separate worker/scheduler process yet — signal ingestion, the trading engine, and the API all run
inside the same Python process, sharing the same broker/signal-source singletons
(`app/core/runtime.py`).

```text
backend/  FastAPI app — parser, state machine, rules engine, trading engine, broker adapters, API
dashboard/  Next.js app — reads the same REST API a human would test against
```

## Data flow

```text
Telegram / Simulation API
        │  IncomingMessageData
        ▼
SignalSource (mock.py / telegram.py)
        │
        ▼
SignalService.ingest_message      — dedupe → parse → classify → state machine
        │  (Signal becomes READY)
        ▼
TradingService.process_incoming_message
        │
        ▼
TradingRulesEngine.evaluate       — every check in PROJECT_SPEC.md section 2.3, in order
        │  (allowed)
        ▼
TradingEngine.execute             — idempotency check → compute TP → Broker.open_buy → persist Trade
        │
        ▼
Broker (paper.py / mt5.py)
```

Price ticks flow the other way: `PaperBroker.set_price()` closes any position whose TP/SL was
crossed and returns the closed `BrokerPosition`s; `TradingService.on_positions_closed()` turns
those into `CLOSED_TP` / `CLOSED_SL` `Trade` rows and audit events. The Simulation API's
`/market-price`, `/trigger-tp`, and `/trigger-sl` endpoints are the only callers of this today —
a live MT5 integration will need to poll or subscribe to position changes the same way.

## Adapters

Two interfaces exist so the trading engine never imports Telegram or MT5 directly
(PROJECT_SPEC.md section 5):

- `app/integrations/signal_source/base.py` — `SignalSource`, implemented by `MockSignalSource`
  and `TelegramSignalSource`.
- `app/integrations/broker/base.py` — `Broker`, implemented by `PaperBroker` and `MT5Broker`.

Both interfaces are **synchronous**, not async as the spec's conceptual snippets show. This was a
deliberate deviation: the official `MetaTrader5` Python package is a synchronous C-binding with no
async equivalent, and the whole ingestion → parser → rules → broker path is fast, local,
CPU-bound work — there is no I/O to overlap. Keeping it synchronous avoids a mixed async/sync
codebase for no benefit. The one exception is `SignalSource.start/stop/health`, which stay
`async def` because Telethon's connection lifecycle genuinely is async; its message callback still
calls the synchronous handler directly (see `app/lifecycle.py::_message_handler`).

## Why mock integrations exist

PROJECT_SPEC.md is explicit that Telegram and MT5 must be the *last* integration step, not the
first. `MockSignalSource` and `PaperBroker` let every other layer — parser, state machine, rules,
statistics, dashboard — be built and tested with zero real credentials and zero risk of an
accidental real-money trade. `TRADING_MODE=SIMULATION` and `AUTO_TRADING=false` are the defaults
everywhere (`.env.example`, `TradingSettings` model default, `Settings` class default) for the
same reason.

## Process model

- Backend: one `uvicorn` process. `app/lifecycle.py` (a FastAPI `lifespan`) creates tables on
  SQLite (dev/test convenience only — Postgres uses Alembic), ensures a default `TradingSettings`
  row exists, starts the mock signal source unconditionally, and starts Telegram/MT5 only if their
  `*_ENABLED` flag is set — each guarded so a bad connection at boot degrades to "paper broker,
  logged error" instead of crashing the app.
- Dashboard: a Next.js App Router client app. Every page is a Client Component that talks to the
  backend over `NEXT_PUBLIC_API_BASE_URL` via TanStack Query (polling, not websockets, for this
  phase).

## Database

SQLAlchemy 2 / SQLModel, synchronous session per request (`app/database/session.py`). Postgres is
the target for real deployments (`DATABASE_URL`); the test suite points the same code at a SQLite
file — no Postgres-only types are used anywhere in `app/database/models/`, and datetime columns go
through `app/database/types.py::UTCDateTime` specifically so SQLite (which has no timezone-aware
timestamp type) round-trips `datetime` values identically to Postgres. See
[05-database.md](05-database.md).
