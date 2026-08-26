# Signal Parser

`app/signals/parser.py`. Deterministic regex + explicit rules only — no LLM is ever used to decide
what counts as a tradeable signal (PROJECT_SPEC.md section 6 and 32).

## Pipeline

```text
raw_text
  → normalize_text()       uppercase, strip emoji/symbols, drop blank lines
  → detect_kind()          BUY / SELL / BUY_LIMIT / SELL_LIMIT / BUY_STOP / SELL_STOP / None
  → is_gold_message()      GOLD / XAU / XAUUSD / XAU/USD / XAU-USD → True
  → extract_stop_loss()    SL / STOP LOSS / STOPLOSS [:|-]? <number>
  → extract_entry_range()  <number> - <number> (SL segment excluded first)
  → extract_target_prices()  bare numeric lines, informational only
  → classify_message()     → MessageClassification
  → determine_role()       → MessageRole (for signal_messages.role)
```

## Recognized formats

| Input | kind | is_gold | classification |
|---|---|---|---|
| `BUY GOLD` | BUY | true | ENTRY_SIGNAL |
| `BUY XAUUSD` | BUY | true | ENTRY_SIGNAL |
| `BUY GOLD 4633 - 4631` | BUY | true | ENTRY_SIGNAL (entry range also captured) |
| `SELL LIMIT GOLD 4546 - 4545` | SELL_LIMIT | true | ENTRY_SIGNAL, but `is_executable_kind` is `False` |
| `4640 - 4637\nSL 4623` | None | — | SIGNAL_DETAILS (entry range + stop loss) |
| `SL 4623` / `SL: 4623` / `STOP LOSS 4623` / `STOPLOSS 4623` | None | — | SIGNAL_DETAILS |
| `TP1 ✅ 80 pips` / `120 pips 🚀` | None | — | TARGET_UPDATE (never affects execution) |
| `4644\n4650\n4663\n4677\n4700` | None | — | TARGET_UPDATE, `target_prices` populated |
| `Good morning` | None | — | IGNORED |

Only `SignalKind.BUY` is in `EXECUTABLE_KINDS` (`app/core/constants.py`) for v1. Every other kind
is parsed, classified, and stored — the Signals dashboard page shows it — but
`TradingRulesEngine.evaluate` rejects it with `UNSUPPORTED_SIGNAL_TYPE` before it can reach a
broker.

## What's deliberately NOT executed

Telegram's own take-profit chatter (`TP1 ✅ 80 pips`, bare pip-count lines, a bare list of target
prices) is captured as `MessageClassification.TARGET_UPDATE` / `MessageRole.TARGETS` and linked to
the signal for the dashboard's Signals page, but `SignalService` never lets it change
`Signal.status` or trigger the trading engine (PROJECT_SPEC.md section 2.1). Only an explicit
Stop Loss can move a signal from `WAITING_FOR_DETAILS` to `READY`.

## Multi-signal ambiguity (assembly, not parsing)

The parser itself has no concept of "which signal" a message belongs to — that's
`app/signals/service.py`'s job, once a message has already been classified `SIGNAL_DETAILS`.
When more than one signal from the same `(source, channel_id, symbol)` is simultaneously
`WAITING_FOR_DETAILS`, a details/Stop-Loss message is **only** attached when it can be resolved
unambiguously:

1. Exactly one signal is waiting → attach to it (the common case, unchanged).
2. More than one is waiting → try `reply_to_message_id`: if the incoming message is a reply to a
   message that belongs to exactly one of the waiting signals, attach to that one.
3. Otherwise (no reply reference, an invalid/unknown reply reference, or the reply resolves to a
   signal that isn't currently waiting) → **do not attach to anything**. The message is still
   stored (visible on the Signals page), an `AMBIGUOUS_SIGNAL_DETAILS` audit event is written, and
   no signal changes state — so no trade can result from it.

This directly implements PROJECT_SPEC.md section 32, rule 13 ("any ambiguity that could create a
trade must default to no trade"). See `docs/03-trading-engine.md` for the audit event and
`backend/tests/unit/test_signal_ambiguity.py` for every covered case.

Informational messages (`TARGET_UPDATE`/`RESULT_UPDATE`) follow the same "never guess" principle
for which signal to *link* them to (for Signals-page debugging), but since they never drive
execution, an unresolved case is simply left unlinked rather than logged as an audit event.

## Ignored input

Anything with no detected kind, no stop loss, no entry range, no target numbers, and no result
keyword (`CLOSED`, `HIT TP`, `HIT SL`, `WIN`, `LOSS`, ...) classifies as `IGNORED`. The message is
still persisted (visible on the Signals page for debugging) but produces no signal and no state
transition.

## Adding a new recognized format

1. Add a fixture-style test to `backend/tests/parser/test_parser.py` first.
2. Extend the relevant regex/function in `parser.py` — keep every function pure (no DB, no I/O) so
   it stays unit-testable in isolation.
3. If the new format should carry a new `MessageRole`, update `classifier.py::determine_role`.
4. Run `pytest backend/tests/parser` and add the new example to the table above.

Real channel examples should be added to this file once Telegram is connected (Phase 11) —
intentionally left out for now since no real channel has been read yet.

## 100+-scenario sweep

`backend/tests/parser/test_scenario_sweep.py` is a data-driven sweep (112 scenarios as of Phase
11), separate from the fixture-by-fixture tests above, required by PROJECT_SPEC.md section 19
before real Telegram traffic is trusted. Every scenario declares its own expected outcome up
front; the sweep fails loudly if any actual outcome drifts from it.

- `PARSER_SCENARIOS` (97 cases): single-message, calls `parse_message` directly — combinatorial
  coverage of BUY/SELL/LIMIT/STOP syntax, Gold aliases, case, spacing, emoji noise, SL separator
  variants (`:`, `-`, `=`, `@`), entry-range formats, target/result keyword messages, invalid/
  malformed numeric input, and word-boundary false-positive guards (`BUYING`/`SELLING`/`GOLDEN`
  must never be misread as `BUY`/`SELL`/`GOLD`).
- `FLOW_SCENARIOS` (15 cases): multi-message, drives the real `TradingService` against a
  `PaperBroker` — duplicate delivery, message ordering, multi-signal ambiguity (generic SL vs.
  `reply_to_message_id`), expiry, and non-`BUY` kinds reaching `TradingRulesEngine` and being
  rejected rather than silently dropped.

Run with `pytest backend/tests/parser/test_scenario_sweep.py -v`. When the real channel's grammar
turns out to include a format not already covered, add it here as a new `ParserCase`/
`FlowScenario` (with its expected outcome derived from actually running the parser, never assumed)
rather than only fixing it ad hoc.
