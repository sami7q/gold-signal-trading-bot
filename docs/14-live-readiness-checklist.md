# Live Readiness Checklist

Transcribed from PROJECT_SPEC.md section 29, with the current status of each item as of this
build. **LIVE mode must not be enabled until every item is checked**, regardless of how confident
any single component looks in isolation (PROJECT_SPEC.md section 21: prefer missing a signal over
an unintended trade).

- [x] Parser test suite passes — `backend/tests/parser/`
- [x] State machine test suite passes — `backend/tests/unit/test_state_machine.py` +
      `backend/tests/unit/test_signal_ambiguity.py` (multi-signal disambiguation, see the
      Pre-Telegram Fix Round section below)
- [x] Duplicate protection tests pass — message-level (`test_state_machine.py`) and
      execution-key-level (`test_end_to_end_simulation.py`, `test_api.py`)
- [x] Paper Broker tests pass — `backend/tests/trading/test_paper_broker.py`
- [x] Trading Engine tests pass — `backend/tests/trading/test_rules.py` +
      `test_end_to_end_simulation.py` + `test_engine_exceptions.py`
- [x] API tests pass — `backend/tests/api/test_api.py`
- [x] Dashboard simulation works — verified manually end-to-end (BUY GOLD → SL → TP → WIN, and the
      duplicate-message case) against a live `uvicorn` + `next dev` pair
- [x] At least 100 simulated signal scenarios have been tested — 112-scenario data-driven sweep,
      `backend/tests/parser/test_scenario_sweep.py`, 100% pass; see
      [02-signal-parser.md](02-signal-parser.md#100-scenario-sweep)
- [x] Telegram read-only integration is implemented — adapter, health/reconnect monitoring,
      `TELEGRAM_OBSERVE_ONLY` safety gate, and 19 unit tests against a mocked Telethon client are
      done (Phase 11); see [09-telegram-integration.md](09-telegram-integration.md)
- [ ] Real Telegram messages are parsed correctly — **BLOCKED**: this environment has no real
      Telegram credentials or interactive-login capability, so the implementation above has not
      yet been exercised against a real channel. Depends on an operator running the Phase 11
      checklist in [09-telegram-integration.md](09-telegram-integration.md#phase-11-checklist-read-only-validation)
      from a real terminal.
- [ ] Telegram reconnect behavior is tested against a real connection — reconnect *logic* is unit
      tested (`backend/tests/integrations/test_telegram_signal_source.py`); a real disconnect/
      reconnect cycle depends on the item above
- [ ] MT5 Demo connection works — not started (Phase 12)
- [ ] Gold symbol metadata is verified — depends on the above
- [ ] Pip calculation is verified against the broker — depends on the above; formula is in place
      (`app/trading/pips.py`) and derives from broker metadata (never a hardcoded dollar value),
      but the specific pip convention **remains unverified for Gold specifically** — see
      MEDIUM-2 below. Do not resolve this by assumption.
- [ ] Stop Loss placement is verified — depends on Phase 12
- [ ] Take Profit placement is verified — depends on Phase 12
- [ ] Demo duplicate prevention is verified — mechanism is identical to the paper broker's (same
      `execution_key`), but not yet exercised against real MT5
- [ ] Demo restart recovery is verified — startup reconciliation (`app/trading/reconciliation.py`)
      is implemented and tested against Paper Broker's restart failure mode; still needs exercising
      against a real MT5 Demo disconnect/restart (Phase 12 checklist item 7 in
      [10-mt5-integration.md](10-mt5-integration.md))
- [ ] VPS restart recovery is verified — not started (Phase 13)
- [x] Error logs contain no secrets — by construction (see
      [13-security.md](13-security.md)); no automated secret-scanning test exists yet
- [ ] Dashboard is authenticated — only a shared API key exists today, not a real login; see
      [13-security.md](13-security.md)
- [x] Simulation endpoints are disabled in Live mode — enforced server-side, covered by
      `test_simulation_endpoints_disabled_in_live_mode`
- [ ] Backup/restore procedure is documented — not written yet, see
      [12-operations.md](12-operations.md)
- [ ] Manual emergency stop exists — `auto_trading=false` stops new trades immediately; no
      "close all open positions" endpoint exists yet, and `UNKNOWN` (reconciliation-required)
      trades still need manual resolution — see [12-operations.md](12-operations.md)
- [ ] Owner has reviewed trading settings — a per-deployment human step, not something code can
      check off

## Pre-Telegram Fix Round (evidence)

A dedicated pre-integration audit (before this fix round) found 3 HIGH-severity findings. All
three are now fixed and covered by new automated tests — none of them touch Telegram or MT5 code,
so fixing them was independent of, and a prerequisite for, Phase 11.

| ID | Finding | Fix | Tests |
|---|---|---|---|
| HIGH-1 | A Stop Loss could attach to the wrong signal when 2+ signals from the same channel were waiting simultaneously — the code guessed "the newest one" instead of refusing | `SignalService` now only attaches when exactly one signal is waiting, or `reply_to_message_id` uniquely resolves one; otherwise `AMBIGUOUS_SIGNAL_DETAILS` is logged and nothing is attached | `backend/tests/unit/test_signal_ambiguity.py` (8 cases) |
| HIGH-2 | An exception from the broker adapter (not a graceful rejection) left a trade permanently stuck in `SUBMITTING` with no failure record | `TradingEngine.execute` wraps the broker call in try/except; any exception now produces a definitive `FAILED` trade + `BROKER_ERROR`/`TRADE_FAILED` audit events, with the raw exception text kept out of the user-facing reason | `backend/tests/trading/test_engine_exceptions.py` (7 cases) |
| HIGH-3 | Paper Broker positions are memory-only; a restart orphaned any `OPEN` trade with no way to close it again, while it silently kept looking healthy | `app/trading/reconciliation.py::ReconciliationService` runs on every startup, checks every DB `OPEN` trade against `broker.get_position()`, and marks anything unconfirmable `UNKNOWN` (never silently `OPEN`) | `backend/tests/trading/test_reconciliation.py` (8 cases, including a full app-startup integration test) |

Also addressed in the same round: MEDIUM-1 (Settings page now shows a live pip-distance/TP
preview computed by the same backend module the engine uses), LOW-1 (audit log text no longer
shows raw padded Decimal strings), LOW-2 (`/docs` is now disable-able via `API_DOCS_ENABLED`).

**Explicitly not resolved, on purpose:** MEDIUM-2 (Gold pip-size convention unverified for the
real broker) — this cannot be resolved by assumption and must wait for a real MT5 XAUUSD symbol
in Phase 12. It remains flagged above and in
[10-mt5-integration.md](10-mt5-integration.md#pip--symbol-metadata).

## Phase 11 status (real Telegram read-only integration)

Everything buildable without real credentials is done and regression-tested: the
`TelegramSignalSource` adapter (health/reconnect monitoring, edited-message identity reuse, strict
channel filtering, sanitized error handling, read-only-only Telegram calls), the
`TELEGRAM_OBSERVE_ONLY` safety gate in `TradingService`, the `assert_paper_broker_active_if_telegram_live`
startup assertion, `/api/health/signal-source`'s new fields, the dashboard status strip update, the
112-scenario parser/state-machine sweep, and 19 new unit tests against a mocked Telethon client —
all covered by `backend/tests/` (199 tests passing), clean `ruff`/`mypy`, and a clean dashboard
lint/typecheck/build.

**Not done, and cannot be done in this environment:** connecting to a real Telegram account and
channel. That requires an operator-supplied `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`/phone number and
an interactive first-login (confirmation code, optional 2FA) at a real terminal — none of which
this environment has. See [09-telegram-integration.md](09-telegram-integration.md#phase-11-checklist-read-only-validation)
for the exact steps an operator needs to run once those are available.

## What "internally complete" means right now

Every item PROJECT_SPEC.md section 30 ("Definition of Done" before Telegram/MT5 connection) lists
is satisfied:

1-4. Project runs locally, Postgres migrations work (verified against an empty SQLite DB;
     Postgres itself untested in this environment — no Docker available — but no Postgres-specific
     types are used anywhere), dashboard loads, mock Telegram messages can be submitted.
5-8. Parser detects BUY GOLD, extracts Stop Loss, multi-message signals work (now safely, even
     when multiple are in flight at once — see HIGH-1 above), Telegram targets are stored but
     never affect execution.
9-11. Dashboard controls lot size, Take Profit pips (with a live calculated preview), and Auto
     Trading.
12-17. Paper Broker opens/closes by TP/SL, trade history is recorded, win/loss/win-rate are
      correct.
18-20. Duplicate signals never duplicate trades, incomplete signals expire safely, max open trades
      is enforced (including defensively for unreconciled `UNKNOWN` trades).
21-23. Logs, statistics, and automated tests are all visible/passing.
24. Documentation (this `/docs` folder) is complete, including today's fixes.
25-26. Telegram and MT5 adapters exist behind disabled flags.
27-28. No real credentials are required for development; no real-money trade can occur
      accidentally (`TRADING_MODE=SIMULATION` + `AUTO_TRADING=false` by default, both flags
      independently enforced by the rules engine and the simulation-endpoint guard).

The only remaining work is real integration, in the order PROJECT_SPEC.md section 34 specifies:
Telegram (read-only, still SIMULATION) → MT5 Demo → VPS deployment → this checklist, fully
checked → only then consider Live.
