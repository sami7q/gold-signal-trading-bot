# Database

PostgreSQL in production/dev (`DATABASE_URL`), SQLite for the test suite. Models live in
`backend/app/database/models/`, migrations in `backend/alembic/versions/`.

## Tables

### `incoming_messages`

Every raw message from any signal source, before it's known whether it forms part of a signal.

| Column | Notes |
|---|---|
| `source`, `channel_id`, `message_id` | Unique together (`uq_incoming_message_identity`) — the duplicate-protection guard |
| `reply_to_message_id` | Nullable |
| `raw_text`, `normalized_text` | Exactly what the parser saw, and what it produced |
| `sent_at`, `received_at` | Sent = source's timestamp; received = when our process saw it |
| `classification` | `MessageClassification` — ENTRY_SIGNAL / SIGNAL_DETAILS / TARGET_UPDATE / RESULT_UPDATE / IGNORED |

### `signals`

One assembled trading setup, built from one or more `incoming_messages`.

| Column | Notes |
|---|---|
| `symbol`, `side`, `signal_type` | `side` = BUY/SELL, `signal_type` = MARKET/LIMIT/STOP (split from the parser's combined `SignalKind`) |
| `entry_min`, `entry_max`, `channel_stop_loss` | From the channel; `channel_stop_loss` is what gets sent to the broker, never Telegram's TP |
| `status` | `SignalStatus` — see [state machine in the spec, section 7] |
| `source`, `channel_id` | Used to group follow-up messages to the right open signal |
| `first_message_at`, `completed_at`, `expires_at` | `expires_at` drives the timeout sweep |
| `rejection_reason` | Set when the rules engine rejects the resulting trade |

### `signal_messages`

Join table: which `incoming_messages` fed which `signal`, and in what role (`MessageRole`: ENTRY,
DETAILS, STOP_LOSS, TARGETS, UPDATE, RESULT, OTHER). Exposed on `GET /api/signals` as `messages` —
the primary parser-debugging aid on the dashboard's Signals page.

### `trades`

| Column | Notes |
|---|---|
| `signal_id` | FK → `signals.id` |
| `execution_key` | Unique (`uq_trade_execution_key`) — `"{signal_id}:{strategy_version}"`, the idempotency guard |
| `broker`, `broker_order_id`, `broker_position_id` | Set once the broker adapter confirms |
| `requested_lot` vs `executed_lot` | What we asked for vs what the broker actually filled |
| `stop_loss` | Nullable — only required when `require_stop_loss` is on |
| `target_pips`, `take_profit_price` | Our own target, never Telegram's |
| `status` | `TradeStatus` |
| `result` | `TradeResult` — WIN/LOSS/BREAKEVEN, only set once closed |
| `failure_reason` | The rules-engine rejection reason code, or the broker's rejection message |

### `trading_settings`

Effectively a singleton — `SettingsService.get_active()` always reads the most recently updated
row and creates a default one (`SIMULATION`, `auto_trading=False`) if none exists.

### `audit_events`

Append-only. `metadata_json` is a JSON string, not a native `JSONB` column, so the exact same
schema works unchanged on SQLite and Postgres (PROJECT_SPEC.md section 13.6's design intent —
portability over a Postgres-only feature).

## Constraints that matter operationally

- `uq_incoming_message_identity (source, channel_id, message_id)` — a reconnect, restart, or
  duplicated delivery can never process the same message twice.
- `uq_trade_execution_key (execution_key)` — a signal can never produce two trades, even under a
  race between two requests.

## Datetime storage

Every datetime column uses `app/database/types.py::UTCDateTime`, a `TypeDecorator` that always
round-trips a timezone-aware UTC `datetime`. SQLite has no native timezone-aware timestamp type;
without this, a value read back from SQLite would silently become naive and crash the moment it's
compared against a fresh `datetime.now(UTC)` (which happens constantly — expiry checks, "is this
open trade..." comparisons, etc.). This is why every model imports `UTCDateTime` instead of
letting SQLModel infer a plain `DateTime` column.

## Migrations

Alembic is configured to read `DATABASE_URL` from the application's own `Settings`
(`alembic/env.py`) rather than a separate `sqlalchemy.url` in `alembic.ini`, so there is exactly
one place the connection string is defined. `render_as_batch=True` is on so SQLite-based dev/test
databases can run ALTER-style migrations too (Postgres ignores this and runs plain `ALTER`).

```bash
cd backend
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

The initial migration (`99dfbbc141c4_initial_schema.py`) was verified to apply cleanly to an empty
database and fully reverse with `alembic downgrade base`.
