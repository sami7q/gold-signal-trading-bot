# Trading Engine

`app/trading/engine.py` (execution), `app/trading/rules.py` (policy), `app/trading/service.py`
(orchestration glue between `app.signals` and `app.trading`).

## Validation order

`TradingRulesEngine.evaluate` runs every check from PROJECT_SPEC.md section 2.3, in this exact
order, and returns on the first failure — nothing is ever silently skipped:

```text
1. auto_trading enabled?                    → AUTO_TRADING_DISABLED
2. symbol == XAUUSD?                        → UNSUPPORTED_SYMBOL
3. side == BUY and signal_type == MARKET?   → UNSUPPORTED_SIGNAL_TYPE
4. buy_enabled?                             → BUY_DISABLED
5. signal.status != EXPIRED?                → SIGNAL_EXPIRED
6. stop loss present (if require_stop_loss)?→ STOP_LOSS_REQUIRED
7. stop loss > 0?                           → STOP_LOSS_INVALID
8. no existing trade for this signal?       → DUPLICATE_SIGNAL
9. broker healthy + account + symbol info?  → BROKER_UNAVAILABLE
10. open trades < max_open_trades?          → MAX_OPEN_TRADES_REACHED
11. lot size valid for broker symbol?       → INVALID_LOT_SIZE
12. target pips > 0?                        → INVALID_TARGET
```

Every one of these has a dedicated test in `backend/tests/trading/test_rules.py`.

## Execution lifecycle

```text
Signal READY
  → TradingEngine.execute()
      idempotency check (execution_key = "{signal_id}:{STRATEGY_VERSION}")
        already has a Trade? → return it, log SIGNAL_DUPLICATE, done
      rules.evaluate()
        rejected? → Trade(status=REJECTED, failure_reason=<reason code>), Signal → REJECTED, done
      Signal → VALIDATING
      compute take_profit_price from broker quote + settings.target_pips (app/trading/pips.py)
      Trade(status=SUBMITTING) persisted
      Signal → EXECUTION_PENDING
      broker.open_buy(...)
        success? → Trade → OPEN, Signal → EXECUTED, audit TRADE_OPENED
        failure? → Trade → FAILED, Signal → FAILED, audit TRADE_FAILED (never a fake OPEN trade)
```

A trade is never marked `OPEN` before the broker adapter confirms the fill
(PROJECT_SPEC.md section 12) — `execute()` only sets `TradeStatus.OPEN` inside the
`execution.success` branch, after `broker.open_buy()` returns.

## Signal-assembly ambiguity → EXECUTION_PENDING never even starts

Before a signal reaches `execute()` at all, `SignalService` can refuse to complete it if a
details/Stop-Loss message couldn't be uniquely attached to it (two signals waiting, no resolving
reply reference). That case never reaches the trading engine — `AMBIGUOUS_SIGNAL_DETAILS` is
logged and neither candidate signal's status changes. See
[02-signal-parser.md](02-signal-parser.md#multi-signal-ambiguity-assembly-not-parsing).

## Broker exceptions vs. graceful rejections

`broker.open_buy()` (and the `get_quote()` call just before it) run inside a `try/except` in
`TradingEngine.execute`. Two distinct failure shapes are both handled, and both always end in a
definitive `Trade.status = FAILED` — never a trade stuck in `SUBMITTING`/`EXECUTION_PENDING`, and
never a fake `OPEN`:

```text
Broker returns BrokerExecution(success=False, error=...)   [graceful rejection]
  → Trade → FAILED, failure_reason = execution.error
  → Signal → FAILED
  → audit TRADE_FAILED

Broker call raises an exception (network timeout, terminal unreachable, ...)
  → caught by TradingEngine._fail_from_exception
  → Trade → FAILED, failure_reason = "Broker error: <ExceptionType>" (sanitized —
    the raw exception text is never shown to the user, only logged server-side
    and placed in the audit event's metadata)
  → Signal → FAILED
  → audit BROKER_ERROR (full exception type/message in metadata) + TRADE_FAILED
  → idempotency unaffected: retrying execute() on the same signal returns the
    same FAILED trade via the execution_key check, never resubmits
```

If the exception happens before a `Trade` row was ever created (e.g. `get_quote()` itself raises),
`_fail_from_exception` still creates one — every failed attempt is auditable, with no gap where a
signal simply vanishes with no record. See
`backend/tests/trading/test_engine_exceptions.py`.

## Idempotency

`execution_key = f"{signal_id}:{STRATEGY_VERSION}"` (`STRATEGY_VERSION` in
`app/core/constants.py`). `TradingEngine.execute` checks for an existing `Trade` with that key
*before* doing anything else, and `trades.execution_key` also carries a unique DB constraint
(`uq_trade_execution_key`) as a last-resort guard against a race the application-level check
missed (PROJECT_SPEC.md section 8). Duplicate protection at the message level is a separate,
earlier guard — see [05-database.md](05-database.md).

## Rejected trades are recorded, not dropped

Even a `REJECTED` decision creates a `Trade` row (status `REJECTED`, `failure_reason` = the reason
code) — "never silently ignore an executable-looking signal" (PROJECT_SPEC.md section 11). This is
what the Trades page's `REJECTED` status filter shows.

## UNKNOWN trades still consume risk capacity

`_count_open_trades()` (the `max_open_trades` check) counts both `TradeStatus.OPEN` **and**
`TradeStatus.UNKNOWN` (`app.core.constants.OPEN_TRADE_STATUSES`). `UNKNOWN` means startup
reconciliation couldn't confirm a position is actually gone — see
[12-operations.md](12-operations.md#startup-reconciliation) — and until a human resolves it, the
engine treats it exactly like a still-open position for capacity purposes: better to block a new
trade unnecessarily than to risk two positions open when the system can only account for one.

## Pip calculation

Never hard-coded. `app/trading/pips.py::pip_size` derives the pip size from the broker's actual
`digits` (3 or 5 digits → pip = 10× point; otherwise pip = point), read from
`Broker.get_symbol()`. See [10-mt5-integration.md](10-mt5-integration.md) for why this matters
once a real MT5 symbol is connected.

`GET /api/settings/pip-preview` (used by the dashboard Settings page — see
[07-dashboard.md](07-dashboard.md)) calls this exact module to render a live "target pips →
price distance / calculated TP" preview. The frontend never reimplements this math — it only
displays what the backend returns, so the preview can never drift from what a real trade would
actually do.
