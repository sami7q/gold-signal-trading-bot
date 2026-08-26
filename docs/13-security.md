# Security

## Current state (internal build phase)

The dashboard and API run **open** by default (no login) — acceptable only for local development
against a paper broker with `AUTO_TRADING=false`. This is not acceptable for a deployed instance.
See the checklist below for what must change before deployment, and
[14-live-readiness-checklist.md](14-live-readiness-checklist.md) before Live mode specifically.

## What exists today

- **API key gate**: `app/core/security.py::require_api_key`. If `DASHBOARD_API_KEY` is set in the
  environment, `PATCH /api/settings` and every `POST /api/simulation/*` require a matching
  `X-API-Key` header (constant-time comparison via `hmac.compare_digest`); unset = open. This
  protects the two categories of "can change what the bot does" endpoints today.
- **Password hashing helpers**: `hash_password`/`verify_password` in the same module, PBKDF2-HMAC-
  SHA256 with 390k iterations, stdlib-only (no extra dependency). Not wired to any login flow yet
  — provided so a future login page doesn't need to add a hashing library later.
- **Audit trail on every settings change**: `SettingsService.update` always writes a
  `SETTINGS_CHANGED` audit event listing exactly what changed (PROJECT_SPEC.md section 27's
  specific requirement for Trading Mode / Auto Trading / Lot Size / Target Pips changes — this
  implementation covers *every* field, which is a superset).
- **LIVE mode confirmation**: switching `TradingSettings.mode` to `LIVE` requires the exact phrase
  `ENABLE LIVE TRADING` in the same request (`app/settings/schemas.py::LIVE_CONFIRMATION_PHRASE`),
  and that transition is logged at `WARNING` severity, not `INFO`.
- **Simulation endpoints disabled in LIVE mode**: enforced server-side
  (`app/api/simulation.py::require_not_live`), not just hidden in the UI.

## What's required before a deployed dashboard (not yet built)

- Real session-based login (the password hashing helpers above are ready for this) — a shared API
  key is a stopgap, not a login system.
- Session expiration.
- CSRF protection (only matters once cookie-based sessions exist — the current API-key scheme is
  header-based and not vulnerable to CSRF the way cookies are).
- Rate limiting on login attempts, once a login endpoint exists.
- HTTPS termination — handled at the reverse-proxy layer, see
  [11-deployment.md](11-deployment.md).

## Secrets

Never commit, and never pass to `logger.*(..., extra={...})`:

- `TELEGRAM_API_HASH`, the Telethon session file, a login code, a 2FA password
- `MT5_PASSWORD`
- `DASHBOARD_API_KEY`

`.env` is gitignored; only `.env.example` (no real values) is committed. `app/core/logging.py`'s
`JsonFormatter` logs whatever is passed via `extra=` verbatim — it does not attempt to redact
after the fact, so the discipline has to be at the call site. Grep for `extra=` before adding any
new log call near credential-handling code.

## Telegram credential handling (Phase 11)

- `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` come from the operator's own Telegram API application
  (my.telegram.org), read only via `pydantic-settings` from the environment — never hardcoded,
  never logged, never returned by any API endpoint.
- **First login is an interactive, one-time, operator/server-side action, not a dashboard
  feature.** Running the backend with `TELEGRAM_ENABLED=true` for the first time causes Telethon's
  `client.start()` to prompt (on that process's own console/terminal) for the phone number's
  confirmation code and, if enabled, the 2FA password. This must happen at a real terminal the
  operator controls — it is never scripted, never automated, and there is no code path that
  accepts a login code or 2FA password through the dashboard or any HTTP endpoint.
- The Telethon session file (`TELEGRAM_SESSION_PATH`, `.session` by default) is the artifact of
  that login — anyone with it can act as the logged-in account. It is `.gitignore`d
  (`*.session`, `*.session-journal`) and must be treated like a credential: never committed, never
  attached to a support ticket, backed up only alongside other secrets (see
  [12-operations.md](12-operations.md#backup--restore)).
- `TelegramSignalSource._sanitize_error` reduces every caught exception to
  `f"{type(exc).__name__}: {str(exc)[:300]}"` before it reaches `last_error` (returned by
  `/api/health/signal-source`) or an audit event — this bounds exposure even if a future Telethon
  exception message happened to embed connection details, and keeps the API/dashboard-facing
  surface free of raw tracebacks.
- `TELEGRAM_MESSAGE_RECEIVED` audit events record only identifiers (`channel_id`, `message_id`,
  `reply_to_message_id`, `is_edit`) — never the message's raw text — so channel content isn't
  duplicated into a second, less-access-controlled store. See
  [09-telegram-integration.md](09-telegram-integration.md#audit-events).

## Protected endpoints today

| Endpoint | Protection |
|---|---|
| `PATCH /api/settings` | API key (if configured) |
| `POST /api/simulation/*` | API key (if configured) + disabled entirely in LIVE mode |
| Everything else (`GET` endpoints) | None — read-only, no secrets returned |
