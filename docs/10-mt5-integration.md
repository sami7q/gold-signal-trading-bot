# MT5 Integration

Status: **adapter written, disabled by default (`MT5_ENABLED=false`)**. Not yet connected to any
account. Phase 12, and must not start until Phase 11 (Telegram read-only) is stable.

## Contract

`app/integrations/broker/mt5.py::MT5Broker` implements the same `Broker` interface `PaperBroker`
implements (`app/integrations/broker/base.py`). The official `MetaTrader5` package is an optional,
Windows-only dependency (`pip install -e ".[mt5]"`), imported lazily inside `connect()`.

## Configuration

```env
MT5_ENABLED=false
MT5_ACCOUNT=
MT5_PASSWORD=
MT5_SERVER=
MT5_TERMINAL_PATH=
MT5_SYMBOL=XAUUSD
```

Never commit real credentials.

## Connection sequence (`MT5Broker.connect()`)

Follows PROJECT_SPEC.md section 20 exactly:

```text
1. mt5.initialize(path=MT5_TERMINAL_PATH)
2. mt5.login(account, password, server)
3. mt5.account_info() — confirm identity, and confirm trade_mode matches TRADING_MODE
   (DEMO must connect to a demo account; LIVE must connect to a real account — refuses otherwise)
4. mt5.symbol_info(MT5_SYMBOL) — resolve the actual Gold symbol
5. symbol_select() if not already visible
6. confirm symbol_info.trade_mode allows trading
7. mt5.symbol_info_tick() — confirm market data exists
8. (health() thereafter re-checks account_info() is still reachable)
```

Any failure raises `MT5ConnectionError`. `app/lifecycle.py` catches this at boot, logs
`ErrorCode.BROKER_CONNECTION_FAILED`, and leaves `app.core.runtime.get_broker()` returning the
Paper Broker — the app never silently trades against a half-connected MT5.

## Order validation before every submission

`MT5Broker.open_buy` re-validates independently of the rules engine (defense in depth):

- `volume_min <= lot <= volume_max`
- `(lot - volume_min) % volume_step == 0`
- Stop Loss (if provided) below the current ask; Take Profit above it
- A live tick is available

If MT5 rejects the order (`retcode != TRADE_RETCODE_DONE`), `open_buy` returns
`BrokerExecution(success=False, error=...)` — `TradingEngine.execute` then marks the `Trade`
`FAILED`, never `OPEN`. **A rejected broker order can never become a fake open trade**
(PROJECT_SPEC.md section 20, final line).

## Pip / symbol metadata

`MT5Broker.get_symbol()` reads `digits`, `point`, `trade_contract_size`, `volume_min/max/step`
directly from `mt5.symbol_info()` — nothing about Gold's pip size is assumed ahead of time. See
`app/trading/pips.py` and [03-trading-engine.md](03-trading-engine.md).

**Still unverified (do not assume — verify against the real symbol before trusting a number):**
`app/trading/pips.py::pip_size` uses the standard MT5 forex convention (`digits` 3 or 5 → pip =
10× point; otherwise pip = point). This is *derived from the broker's real metadata*, never a
hardcoded dollar value, which satisfies PROJECT_SPEC.md section 10's letter — but there is no
universal industry standard for what "1 pip" means for **Gold** specifically across brokers the
way there is for standard forex pairs. This must be checked against the actual connected MT5
XAUUSD symbol during the Phase 12 checklist below before any calculated Take Profit distance is
trusted with real money. Do not resolve this by assumption — confirm it empirically.

## Broker exceptions during order submission

`MT5Broker.open_buy()` (and `get_quote()`) currently have no internal try/except of their own — if
`mt5.order_send()`, `mt5.symbol_info_tick()`, or similar raise instead of returning a normal
result (a genuinely possible MT5 failure mode: terminal not responding, connection dropped
mid-call), the exception propagates up to `TradingEngine.execute()`, which now catches it
(`_fail_from_exception` — see [03-trading-engine.md](03-trading-engine.md#broker-exceptions-vs-graceful-rejections)).
The trade is marked `FAILED` with a sanitized reason, never left stuck and never a fake `OPEN`.
This was verified with fake broker doubles (`backend/tests/trading/test_engine_exceptions.py`);
it has not yet been exercised against a real MT5 disconnect, which the Phase 12 checklist below
should include.

## Startup reconciliation

`app/trading/reconciliation.py::ReconciliationService` runs on every backend startup, after the
MT5 connection attempt (so it reconciles against whichever broker actually ended up active — see
`app/lifecycle.py`). For every DB `Trade` with `status=OPEN`, it calls
`broker.get_position(trade.broker_position_id)`:

- Position still open → confirmed, left as `OPEN`.
- Position found but closed, with real close data (price/profit/reason) → resolved properly using
  that broker-provided data (not a guess).
- Position not found, or the broker call itself raises → `TradeStatus.UNKNOWN`,
  `close_reason="RECONCILIATION_REQUIRED"`. Still counts toward `max_open_trades`
  (`OPEN_TRADE_STATUSES` in `app/core/constants.py`) until a human resolves it.

The service was built and tested against `PaperBroker`'s restart-loses-everything failure mode
(see [04-paper-broker.md](04-paper-broker.md)) but only ever calls `Broker.get_position()`, so it
needs zero changes for MT5 — `MT5Broker.get_position()` already queries the live terminal via
`mt5.positions_get()`. Add a Phase 12 test that kills the backend mid-trade against the demo
account and confirms reconciliation resolves it correctly on restart.

## Phase 12 checklist (demo only)

1. Set `MT5_ACCOUNT` / `MT5_PASSWORD` / `MT5_SERVER` / `MT5_TERMINAL_PATH` for a **DEMO** account.
2. Set `MT5_ENABLED=true`. Keep `TRADING_MODE=DEMO` (never `LIVE` at this stage).
3. Confirm `GET /api/health/broker` reports healthy and `broker: "mt5"`.
4. Manually verify `get_symbol("XAUUSD")` matches what the MT5 terminal shows for digits/point/
   contract size — record the real numbers in this file once confirmed.
5. Run the same BUY GOLD → SL → TP scenario used in
   `tests/integration/test_end_to_end_simulation.py`, this time against the demo account, and
   confirm SL/TP placement and pip math match what the terminal shows.
6. Restart the backend mid-session and confirm no duplicate order is placed (idempotency via
   `execution_key`, same guarantee as the paper broker).
7. Restart the backend while a demo position is genuinely still open and confirm
   `ReconciliationService` reports `POSITION_CONFIRMED` (not `UNKNOWN`) — proves reconciliation
   correctly recognizes a real MT5 position across a restart, not just a Paper Broker's absence
   of one.
8. Deliberately disconnect the MT5 terminal (or network) during a submission and confirm the
   result is a clean `FAILED` trade with a `BROKER_ERROR` audit event, not a hang or a crash.

Only after Phase 12 is stable does VPS deployment ([11-deployment.md](11-deployment.md)) and
eventually Live readiness ([14-live-readiness-checklist.md](14-live-readiness-checklist.md)) begin.
