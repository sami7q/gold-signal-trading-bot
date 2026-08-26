# Testing

## Commands

```bash
cd backend
pytest                    # full suite
pytest tests/parser        # parser fixtures only
pytest tests/unit          # state machine
pytest tests/trading       # rules engine + paper broker
pytest tests/integration   # end-to-end simulation scenarios
pytest tests/api           # HTTP-level smoke tests via FastAPI TestClient

ruff check app tests       # lint
ruff format app tests      # format
mypy app                   # type check
```

```bash
cd dashboard
npx tsc --noEmit           # type check
npx eslint .                # lint
npm run build               # production build (also type-checks)
```

## Test database

The whole backend test session runs against one throwaway SQLite file
(`backend/tests/conftest.py` sets `DATABASE_URL` before any `app.*` module is imported, so
`get_settings()`/`get_engine()` resolve to it naturally — no dependency overrides needed). An
autouse `clean_state` fixture drops and recreates every table, and resets the in-memory
`PaperBroker`/`MockSignalSource` singletons, before each test.

## Categories

- **Parser** (`tests/parser/test_parser.py`) — every fixture from PROJECT_SPEC.md section 17.1:
  BUY GOLD variants, entry ranges, Stop Loss formats, informational TP/pip updates, ignored text,
  SELL LIMIT classified-but-not-executable.
- **State machine** (`tests/unit/test_state_machine.py`) — exercised through `SignalService` since
  it's the only thing that persists a transition: BUY alone → `WAITING_FOR_DETAILS`; BUY + SL →
  `READY`; no SL before timeout → `EXPIRED`; a repeated `(source, channel_id, message_id)` →
  flagged duplicate, no second signal.
- **Rules engine** (`tests/trading/test_rules.py`) — one test per reason code in
  PROJECT_SPEC.md section 11, plus the "valid BUY + SL → allowed" happy path and the
  require-stop-loss-disabled edge case.
- **Paper Broker** (`tests/trading/test_paper_broker.py`) — open, TP close, SL close, stays open
  between SL/TP, profit calculation, double-close rejection, opening without a Stop Loss.
- **End-to-end simulation** (`tests/integration/test_end_to_end_simulation.py`) — the exact
  scenario from PROJECT_SPEC.md section 31: BUY GOLD → entry+SL → trade opens → TP hit → WIN →
  stats update → same message resent → still exactly one trade. A second test does the same for
  an SL hit (LOSS), and a third confirms `auto_trading=False` produces a recorded `REJECTED` trade
  instead of silently doing nothing.
- **API** (`tests/api/test_api.py`) — the same end-to-end scenario driven entirely over HTTP
  through `TestClient`, plus settings PATCH/LIVE-confirmation behavior and the "simulation
  endpoints are 403 in LIVE mode" guarantee.

## What "100 simulated signal scenarios" means here

PROJECT_SPEC.md section 29's live-readiness checklist asks for at least 100 simulated signal
scenarios before Telegram is connected. The current suite covers every *distinct* code path
(happy path, every rejection reason, expiry, duplicate at both the message and execution-key
level, TP, SL, missing-SL-allowed). Before Phase 11, extend `tests/integration/` with a
property-style or table-driven test that runs many BUY/SL/price-path combinations to satisfy the
literal count — the scaffolding (`_broker()`, `_msg()`, `_configure()` helpers already in
`test_end_to_end_simulation.py`) is built for exactly that.

## Adding a test for a new trading rule

Every new rule must ship with a test (PROJECT_SPEC.md section 32, rule 6). Add the reason code to
`RejectionReason` in `app/core/constants.py`, the check to `TradingRulesEngine.evaluate` in the
correct position (see [03-trading-engine.md](03-trading-engine.md) for the required order), and a
case to `tests/trading/test_rules.py` asserting both the rejection and the `reason_code`.
