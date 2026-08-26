# API

Base URL: `http://localhost:8000` in dev. All endpoints are prefixed `/api`. Interactive docs at
`/docs` (Swagger UI, from FastAPI automatically).

## Health

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | Always `{"status": "ok"}` if the process is up |
| GET | `/api/health/signal-source` | Health of the mock signal source singleton |
| GET | `/api/health/broker` | Health of the currently active broker (paper unless MT5 connected) |

## Dashboard

| Method | Path | Notes |
|---|---|---|
| GET | `/api/dashboard/summary` | `{ summary, settings }` — everything the Overview page needs in one call |
| GET | `/api/dashboard/recent-trades?limit=10` | Newest trades first |
| GET | `/api/dashboard/recent-events?limit=20` | Newest audit events first |
| GET | `/api/dashboard/analytics/profit-curve` | Cumulative profit over closed trades, in order |
| GET | `/api/dashboard/analytics/daily-performance` | Trades/wins/losses/profit grouped by close day |

## Settings

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/api/settings` | — | Creates a default row on first call if none exists |
| PATCH | `/api/settings` | `X-API-Key` if `DASHBOARD_API_KEY` is set | Partial update; switching `mode` to `LIVE` requires `confirm_live_phrase: "ENABLE LIVE TRADING"` or the request 400s |

## Trades

| Method | Path | Notes |
|---|---|---|
| GET | `/api/trades?status=&result=&date_from=&date_to=&limit=` | All filters optional |
| GET | `/api/trades/{id}` | 404 if not found |

## Signals

| Method | Path | Notes |
|---|---|---|
| GET | `/api/signals?status_filter=&limit=` | Includes `linked_trade_id` and the raw `messages` that formed the signal |
| GET | `/api/signals/{id}` | Same shape, single record |

## Simulation

Development/simulation only — every endpoint 403s once `TradingSettings.mode == LIVE`.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/simulation/state` | Current bid/ask/balance/equity/open positions |
| POST | `/api/simulation/messages` | `{ text, channel_id?, message_id?, sent_at? }` — feeds the real parser pipeline |
| POST | `/api/simulation/market-price` | `{ bid: "..." }` or `{ delta: "..." }`, exactly one |
| POST | `/api/simulation/trigger-tp` | Moves price to the (single) open position's TP |
| POST | `/api/simulation/trigger-sl` | Moves price to the (single) open position's SL |
| POST | `/api/simulation/reset` | `{ scope: "account" \| "trades" \| "all" }` |

## Logs

| Method | Path | Notes |
|---|---|---|
| GET | `/api/logs?limit=100` | Full audit trail, newest first |

## Auth

`app/core/security.py::require_api_key` protects `PATCH /api/settings` and every `/api/simulation`
mutation when `DASHBOARD_API_KEY` is set in the environment (unset = open, dev-only — see
[13-security.md](13-security.md)). Send it as `X-API-Key`. The dashboard reads a key from
`localStorage["goldbot_api_key"]` if present and attaches it automatically.

## Errors

Standard FastAPI/Pydantic shape: `{"detail": "..."}` for a single message, or a Pydantic
validation error array for a 422. HTTP status codes used: `400` (bad request, e.g. missing LIVE
confirmation phrase), `401` (missing/invalid API key), `403` (simulation endpoint in LIVE mode),
`404` (not found), `422` (validation).
