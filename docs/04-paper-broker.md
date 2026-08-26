# Paper Broker

`app/integrations/broker/paper.py`. A fully in-memory simulation implementing the same `Broker`
interface `MT5Broker` implements — the trading engine cannot tell the difference.

## What it simulates

- One symbol, with fully configurable `SymbolInfo` (digits, point, contract size, volume
  min/max/step) — set from `PAPER_*` env vars, so the paper broker can imitate whatever the real
  future MT5 symbol looks like.
- A single bid price plus a fixed spread (`PAPER_SPREAD_POINTS`); ask = bid + spread.
- BUY execution at the current ask.
- Stop Loss / Take Profit stored on the position; crossing either closes it automatically on the
  next `set_price()` call.
- Balance and equity — equity includes unrealized P/L on open positions, computed from the current
  bid.
- Full position history via `get_position()` / `open_positions()`.

## What it does not simulate

- Slippage, partial fills, requotes, margin calls, swap/rollover.
- Multiple symbols at once.
- Order book depth — one bid/ask pair only.
- **Persistence.** All state (`_positions`, `_balance`, the current price) lives in a single
  Python object in process memory. A backend restart creates a brand-new `PaperBroker` with none
  of it — see the next section.

None of these are needed to validate the *business logic* (parser → rules → engine → statistics),
which is the paper broker's only job (PROJECT_SPEC.md section 9).

## Restart behavior: positions do not survive, and that's by design

Because `PaperBroker` keeps state only in memory, **a process restart silently loses every open
position** — `get_position()` for a position that was open before the restart returns `None`
afterward, exactly as if it had never existed. Left unhandled, this would mean a `Trade` row the
database still calls `OPEN` can never close again (no TP/SL trigger will ever find it) — a
permanently orphaned "phantom open" trade.

This is now caught automatically: `app/trading/reconciliation.py::ReconciliationService` runs on
every backend startup (`app/lifecycle.py`), checks every DB `Trade` with `status=OPEN` against
`broker.get_position()`, and — since the Paper Broker genuinely cannot prove what happened —
marks anything it can't find as `TradeStatus.UNKNOWN` with
`close_reason="RECONCILIATION_REQUIRED"` rather than leaving it looking `OPEN` and healthy. See
[12-operations.md](12-operations.md#startup-reconciliation) for the full mechanism, which is
written generically enough that it needs zero changes once `MT5Broker` is the active broker (a
real MT5 position is a legitimately different case — see
[10-mt5-integration.md](10-mt5-integration.md)).

This was a deliberate choice over the alternative (persisting Paper Broker positions to disk/DB):
the Paper Broker exists to validate business logic, not to be a production-grade trading backend
in its own right — detecting and surfacing the loss honestly was judged simpler and safer than
adding real persistence to a tool that only needs to survive a single process's lifetime. See
`backend/tests/trading/test_reconciliation.py::test_paper_broker_restart_orphan_is_caught_by_reconciliation_and_still_blocks_new_trades`
for the exact scenario, end to end.

## Simulation controls

All exposed through `app/api/simulation.py`, mirrored on the dashboard's Simulation page:

| Control | Endpoint | Broker method |
|---|---|---|
| Set market price | `POST /api/simulation/market-price {"bid": "..."}` | `set_price()` |
| Advance price | `POST /api/simulation/market-price {"delta": "..."}` | `move_price()` |
| Send mock Telegram message | `POST /api/simulation/messages` | — (goes through `TradingService`) |
| Trigger TP | `POST /api/simulation/trigger-tp` | `set_price(open_position.take_profit)` |
| Trigger SL | `POST /api/simulation/trigger-sl` | `set_price(open_position.stop_loss)` |
| Reset account | `POST /api/simulation/reset {"scope": "account"}` | `reset()` |
| Reset trades/signals | `POST /api/simulation/reset {"scope": "trades"}` | deletes `Trade`/`Signal`/`SignalMessage`/`IncomingMessage` rows |

Every simulation endpoint 403s once `TradingSettings.mode == LIVE`
(`app/api/simulation.py::require_not_live`) — see PROJECT_SPEC.md section 16.

## Stop Loss is optional

`stop_loss` on `OpenTradeRequest` / `Trade` is `Decimal | None`. It is only *required* to reach
execution when `TradingSettings.require_stop_loss` is `True` (the default) — the rules engine
enforces that, not the broker. If an operator disables `require_stop_loss`, the Paper Broker (and
`MT5Broker`) will happily open a position with no Stop Loss and it will simply never close on an
SL condition (see `test_open_buy_without_stop_loss_never_closes_on_sl` in
`backend/tests/trading/test_paper_broker.py`).

## Assumptions worth knowing before trusting a number

- Profit = `(exit_price - entry_price) * lot * contract_size`. No currency conversion — assumes
  the account currency matches the quote currency.
- A BUY always fills instantly at the current ask with the exact requested lot; there is no
  partial-fill or rejection path except the two validation checks in `open_buy` (lot outside
  broker volume range, SL/TP on the wrong side of the entry price).
