# Telegram Integration

Status: **adapter implemented and unit-tested (19 tests, mocked Telethon), disabled by default**
(`TELEGRAM_ENABLED=false`). Real-channel validation is Phase 11 (PROJECT_SPEC.md section 18) and
requires operator-supplied credentials this environment does not have — see "Phase 11 checklist"
below for exactly what remains.

## Contract

`app/integrations/signal_source/telegram.py::TelegramSignalSource` implements the same
`SignalSource` interface `MockSignalSource` implements
(`app/integrations/signal_source/base.py`). It emits the exact same `IncomingMessageData` shape —
`source="telegram"`, `channel_id`, `message_id`, `text`, `sent_at`, `received_at`,
`reply_to_message_id` — so the parser and everything downstream needs **zero** changes when this
is switched on. It contains no trading rules of its own; disambiguation, execution, and safety
gating all live in `app.trading.service.TradingService` and below, per PROJECT_SPEC.md section 5.1.

`telethon` is an optional dependency (`pip install -e ".[telegram]"`, currently pinned
`>=1.36`; `1.44.0` is the version this integration was implemented and tested against), imported
lazily inside `start()` so the rest of the application works without it installed.

## Read-only, always

This adapter only listens. It never sends, replies to, edits, or deletes a Telegram message, never
joins/leaves a chat, and never administers anything — there is no code path in `telegram.py` that
calls any Telethon *write* method. This is a design invariant, not a runtime flag.

## Configuration

```env
TELEGRAM_ENABLED=false
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_SESSION_PATH=
TELEGRAM_ALLOWED_CHANNEL_ID=
TELEGRAM_OBSERVE_ONLY=true
TELEGRAM_HEALTH_POLL_SECONDS=15
```

Never commit `TELEGRAM_API_HASH`, the session file, a login code, or a 2FA password — see
[13-security.md](13-security.md) for the full credential-handling rules.

### `TELEGRAM_OBSERVE_ONLY` (Phase 11 safety gate)

Defaults to `true`. While on, real Telegram messages (`source == "telegram"`) still flow all the
way through the parser and the signal state machine — so the real channel's grammar can be
validated end to end — but `TradingService.process_incoming_message` stops just short of calling
`TradingEngine.execute`: a signal that reaches `READY` from Telegram logs
`AuditEventType.SIGNAL_OBSERVED_ONLY` and is left exactly there, never producing a `Trade` row.
Messages from every other source (`mock`) are completely unaffected — this gate is keyed on
`data.source == "telegram"` specifically, checked in `TradingService`, not in the adapter itself.

Only flip this to `false` after the real channel's grammar has been observed and validated per the
checklist below — and remember `TRADING_MODE=SIMULATION` and the Paper Broker still apply even
then; this flag only controls whether Telegram-sourced signals reach the (still-simulated) engine.

## Safety boundaries

- `TelegramSignalSource.__init__` raises immediately if `TELEGRAM_ENABLED=true` but
  `TELEGRAM_ALLOWED_CHANNEL_ID` is unset — it refuses to start in a state where it might read from
  an arbitrary chat.
- The Telethon event handlers are registered with `events.NewMessage(chats=allowed_channel)` and
  `events.MessageEdited(chats=allowed_channel)` — only the configured channel is ever subscribed
  to. `_handle_event` then *defensively re-checks* `event.chat_id` against the allowed channel
  before dispatching anything, in case Telethon's own filter is ever bypassed by an edge case
  (e.g. a forwarded/linked-channel event) — a mismatch is logged and dropped, never processed.
- `app/lifecycle.py::assert_paper_broker_active_if_telegram_live` is a hard startup assertion: if
  `TELEGRAM_ENABLED=true` and `TRADING_MODE=SIMULATION`, the active broker *must* be `"paper"` — if
  anything ever made `MT5Broker` active in that combination, the app refuses to start rather than
  let a read-only integration phase reach a real broker.
- `app/lifecycle.py` starts this adapter inside a `try/except`: a failed connection at boot logs
  `ErrorCode.TELEGRAM_SOURCE_DISCONNECTED` as an audit event and the app keeps running without it
  (falling back to the mock source as the process-wide active signal source via
  `app/core/runtime.py::get_active_signal_source`), it never crashes the process.
- Every exception inside the Telethon event callback, the connection monitor, and `start()`/
  `stop()` is caught, sanitized (`type(exc).__name__: <first 300 chars>` — never a raw traceback or
  credential), and turned into a `TELEGRAM_ERROR` audit event plus `last_error` on the health
  object — a bad or unexpected event can never kill Telethon's dispatch loop or crash the backend.

## Edited messages

`events.MessageEdited` is registered alongside `events.NewMessage`. An edit is normalized to
`IncomingMessageData` with the **same** `message_id` as the original message (Telegram edits don't
change the message's id) — so it hits the exact same `(source, channel_id, message_id)` uniqueness
constraint `SignalService` already enforces for duplicate protection. An edited message can never
create a second signal or a second execution; no new schema or dedup logic was needed for this.

## Connection health — `/api/health/signal-source`

Returns whichever source is currently active
(`app/core/runtime.py::get_active_signal_source` — mock until Telegram successfully connects):

```json
{
  "source": "telegram",
  "healthy": true,
  "detail": "connected",
  "enabled": true,
  "connected": true,
  "authenticated": true,
  "allowed_channel_configured": true,
  "last_message_at": "2026-08-25T10:00:00Z",
  "last_error": null
}
```

`last_error` is always the sanitized `type(exc).__name__: <message>` form described above — never
a raw exception, stack trace, or credential. The dashboard status strip
(`dashboard/components/shell/status-strip.tsx`) reads this endpoint and shows, without a redesign:
**Telegram / Connected / Disconnected**, **Source / Real Telegram** (vs. `Mock`), alongside the
existing **Broker / Paper**, **Mode / SIMULATION**, and **Auto Trading / ON/OFF** items.

## Reconnect behavior

Telethon's own `auto_reconnect` (on by default) handles the actual reconnection. A lightweight
background task (`_monitor_connection`, polling `client.is_connected()` every
`TELEGRAM_HEALTH_POLL_SECONDS`) only **observes** that transition and turns it into audit events —
it never re-registers event handlers, so a reconnect cannot produce duplicate message dispatch:

```text
connected → disconnected   TELEGRAM_DISCONNECTED, then TELEGRAM_RECONNECTING (WARNING)
disconnected → connected   TELEGRAM_CONNECTED
```

Combined with the message-id-level duplicate protection above, a reconnect (or a full backend
restart) can never reprocess or duplicate a signal/trade from a message already seen.

## Audit events

| Event | When |
|---|---|
| `TELEGRAM_CONNECTING` | Immediately before `client.start()` |
| `TELEGRAM_CONNECTED` | After a successful connect (boot) or reconnect (monitor loop) |
| `TELEGRAM_DISCONNECTED` | `stop()` called, or the monitor observes the connection drop |
| `TELEGRAM_RECONNECTING` | Logged right after a disconnect is observed |
| `TELEGRAM_ERROR` | Any caught exception in `start()`, the monitor loop, or an event callback |
| `TELEGRAM_MESSAGE_RECEIVED` | Every accepted message/edit, metadata = `{channel_id, message_id, reply_to_message_id, is_edit}` |
| `SIGNAL_OBSERVED_ONLY` | A Telegram-sourced signal reached `READY` while `TELEGRAM_OBSERVE_ONLY=true` |

`TELEGRAM_MESSAGE_RECEIVED` deliberately never includes the raw message text in its metadata or
log line — only identifiers — so the audit log stays useful for operational debugging without
duplicating channel content into a second store.

## Phase 11 checklist (read-only validation)

1. Set `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_SESSION_PATH` /
   `TELEGRAM_ALLOWED_CHANNEL_ID` from a real account already subscribed to the channel. First login
   (entering the phone-number confirmation code / 2FA password) is an interactive, one-time,
   operator/server action — it happens at a real terminal, never through the dashboard, and is
   never scripted or automated by this codebase.
2. Set `TELEGRAM_ENABLED=true`. Leave `TELEGRAM_OBSERVE_ONLY=true` for this first run.
3. **Keep `TRADING_MODE=SIMULATION`.** Real messages will flow through the real parser and state
   machine — that is the point of this phase — but `TELEGRAM_OBSERVE_ONLY=true` stops them just
   short of the trading engine, so nothing executes, not even a paper trade, until that's
   deliberately turned off.
4. Watch the Signals page and `/api/health/signal-source`: confirm real channel messages classify
   identically to the mock fixtures already covered by `tests/parser/` and
   `tests/parser/test_scenario_sweep.py`. Any format the real channel uses that isn't recognized
   should be stored, classified safely (never guessed into a trade), and added to
   `docs/02-signal-parser.md` plus a new scenario in the sweep before moving on — never fixed by
   assumption without having actually seen the real message.
5. Exercise a reconnect (restart the backend process, or interrupt the network briefly) and
   confirm `TELEGRAM_DISCONNECTED`/`TELEGRAM_RECONNECTING`/`TELEGRAM_CONNECTED` appear in the audit
   log with no duplicate signal/trade — see "Reconnect behavior" above.
6. Only once the channel's real grammar is validated, consider setting `TELEGRAM_OBSERVE_ONLY=false`
   so Telegram-sourced signals reach the (still-`SIMULATION`, still-Paper-Broker) trading engine.

Only after this phase is stable does [10-mt5-integration.md](10-mt5-integration.md) start. This
environment does not have real Telegram credentials or interactive-login capability, so steps 1-6
above remain **BLOCKED** here — the code path is implemented and unit-tested against a mocked
Telethon client (`backend/tests/integrations/test_telegram_signal_source.py`), but has not been
exercised against a real channel.
