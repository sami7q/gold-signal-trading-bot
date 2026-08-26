# Gold Signal Trading Bot — Complete Project Specification

**Document purpose:**  
This file is the master implementation specification for building a production-structured gold trading automation platform that is fully testable **before** connecting a real Telegram account or MetaTrader 5 account.

The system must be developed in a way that allows the entire application — backend, trading engine, parser, database, dashboard, statistics, logs, safety rules, and automated tests — to work with mock/simulated integrations first. Real Telegram and MT5 integrations are connected only after the internal system is stable and verified.

---

# 1. Project Goal

The goal is to build an automated trading bot focused initially on **Gold / XAUUSD BUY signals**.

The bot will eventually listen to a specific Telegram channel. The Telegram channel may send signals in multiple messages, for example:

```text
BUY GOLD
```

followed by:

```text
4640 - 4637
SL 4623
```

and later:

```text
4644
4650
4663
4677
4700
```

The system must use only the information required by our strategy:

- Detect a valid **BUY GOLD** signal.
- Read the **Stop Loss** supplied by the Telegram channel.
- Ignore the Telegram channel's take-profit targets for execution.
- Use the **lot size** configured in our dashboard.
- Use the **profit target in pips** configured in our dashboard.
- Open a BUY trade.
- Apply the Telegram Stop Loss.
- Calculate our own Take Profit from the configured number of pips.
- Close automatically when the broker reaches TP or SL.
- Record the complete trade.
- Update all statistics automatically.

The application should be able to run continuously, including at night or when the owner is away from the computer.

The final architecture must allow:

```text
Telegram
    ↓
Signal Source Adapter
    ↓
Signal Parser
    ↓
Signal State Machine
    ↓
Trading Rules Engine
    ↓
Trading Engine
    ↓
Broker Adapter
    ↓
MT5
```

During development, Telegram and MT5 are replaced with:

```text
Mock Signal Source
    ↓
Signal Parser
    ↓
Trading Engine
    ↓
Paper Broker
```

This is mandatory. Do not connect live trading until the simulation system is complete.

---

# 2. Core Strategy Rules

The first version of the bot must implement a deliberately small and deterministic strategy.

## 2.1 Telegram provides

The signal source provides:

- Action: BUY
- Instrument: GOLD / XAU / XAUUSD
- Stop Loss
- Optional entry range
- Message time
- Message ID
- Channel ID
- Reply relationship if available

Telegram targets such as TP1, TP2, TP3, or messages such as:

```text
TP1 ✅ 80 pips
120 pips 🚀
```

must **not** control our trade exit.

They may be stored as informational channel updates, but they must not alter the active trade in version one.

## 2.2 Dashboard provides

The dashboard controls:

- Auto Trading ON/OFF
- Lot size
- Profit target in pips
- Maximum open trades
- BUY signals enabled/disabled
- Require Stop Loss
- Paper / Demo / Live mode
- Optional future safety limits

Example:

```text
Auto Trading: ON
Lot Size: 0.10
Take Profit: 100 pips
Maximum Open Trades: 1
BUY Signals: ON
Require Stop Loss: ON
```

## 2.3 Execution behavior

A BUY trade may only be created when:

1. Auto Trading is enabled.
2. BUY signals are enabled.
3. The signal is from the allowed source.
4. The parser recognizes a valid BUY GOLD signal.
5. A valid Stop Loss is available.
6. The signal has not already been executed.
7. The broker adapter is available.
8. Maximum open trade rules allow another position.
9. The configured lot size is valid.
10. The profit target is valid.

No trade should be opened if Stop Loss is missing when `require_stop_loss` is enabled.

---

# 3. Recommended Technology Stack

Use the following stack unless there is a strong technical reason to change it.

## Backend / Trading Engine

- Python 3.12+
- FastAPI
- Pydantic v2
- SQLAlchemy 2 or SQLModel
- Alembic
- PostgreSQL
- asyncio where useful
- pytest
- Ruff
- mypy or pyright

## Telegram Integration

Later:

- Telethon
- Telegram MTProto API

Do not add the real account credentials during the early development phases.

## MT5 Integration

Later:

- MetaTrader5 official Python package
- Windows host or Windows VPS
- MT5 terminal installed and logged in

## Dashboard

- Next.js
- TypeScript
- React
- Tailwind CSS
- shadcn/ui or another clean component library
- TanStack Query
- Recharts or equivalent for statistics

## Infrastructure

Development:

- Docker for PostgreSQL
- Backend locally
- Dashboard locally
- Mock Signal Source
- Paper Broker

Production later:

- Windows VPS for MT5 worker
- Backend service
- PostgreSQL
- Next.js dashboard
- Reverse proxy / HTTPS

---

# 4. Required Repository Structure

Use a clear repository layout so the code remains understandable for humans and coding agents.

```text
gold-trading-bot/
│
├── README.md
├── PROJECT_SPEC.md
├── .env.example
├── .gitignore
├── docker-compose.yml
│
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   │
│   ├── app/
│   │   ├── main.py
│   │   ├── lifecycle.py
│   │   │
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── logging.py
│   │   │   ├── security.py
│   │   │   └── constants.py
│   │   │
│   │   ├── database/
│   │   │   ├── base.py
│   │   │   ├── session.py
│   │   │   └── models/
│   │   │
│   │   ├── signals/
│   │   │   ├── models.py
│   │   │   ├── parser.py
│   │   │   ├── state_machine.py
│   │   │   ├── classifier.py
│   │   │   └── service.py
│   │   │
│   │   ├── integrations/
│   │   │   ├── signal_source/
│   │   │   │   ├── base.py
│   │   │   │   ├── mock.py
│   │   │   │   └── telegram.py
│   │   │   │
│   │   │   └── broker/
│   │   │       ├── base.py
│   │   │       ├── paper.py
│   │   │       └── mt5.py
│   │   │
│   │   ├── trading/
│   │   │   ├── engine.py
│   │   │   ├── rules.py
│   │   │   ├── risk.py
│   │   │   ├── pips.py
│   │   │   ├── service.py
│   │   │   └── exceptions.py
│   │   │
│   │   ├── analytics/
│   │   │   ├── service.py
│   │   │   └── calculations.py
│   │   │
│   │   ├── settings/
│   │   │   ├── service.py
│   │   │   └── schemas.py
│   │   │
│   │   ├── audit/
│   │   │   └── service.py
│   │   │
│   │   └── api/
│   │       ├── router.py
│   │       ├── dashboard.py
│   │       ├── trades.py
│   │       ├── signals.py
│   │       ├── settings.py
│   │       ├── simulation.py
│   │       ├── health.py
│   │       └── logs.py
│   │
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   ├── parser/
│   │   ├── trading/
│   │   └── api/
│   │
│   └── alembic/
│
├── dashboard/
│   ├── package.json
│   ├── next.config.ts
│   ├── tsconfig.json
│   │
│   ├── app/
│   │   ├── page.tsx
│   │   ├── trades/
│   │   ├── signals/
│   │   ├── analytics/
│   │   ├── settings/
│   │   ├── logs/
│   │   └── simulation/
│   │
│   ├── components/
│   ├── lib/
│   ├── hooks/
│   └── types/
│
└── docs/
    ├── 01-architecture.md
    ├── 02-signal-parser.md
    ├── 03-trading-engine.md
    ├── 04-paper-broker.md
    ├── 05-database.md
    ├── 06-api.md
    ├── 07-dashboard.md
    ├── 08-testing.md
    ├── 09-telegram-integration.md
    ├── 10-mt5-integration.md
    ├── 11-deployment.md
    ├── 12-operations.md
    ├── 13-security.md
    └── 14-live-readiness-checklist.md
```

---

# 5. Architectural Principle: Adapters First

The project must use interfaces/adapters for all external integrations.

The trading engine must not know whether the signal originated from Telegram, a test form, a fixture, or an automated test.

Likewise, it must not know whether a trade was sent to a Paper Broker or MT5.

## 5.1 Signal Source interface

Example conceptual interface:

```python
class SignalSource:
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def health(self) -> SourceHealth: ...
```

The source emits normalized incoming messages:

```python
class IncomingMessage:
    source: str
    channel_id: str
    message_id: str
    text: str
    sent_at: datetime
    received_at: datetime
    reply_to_message_id: str | None
```

Implementations:

```text
MockSignalSource
TelegramSignalSource
```

The real Telegram implementation should be disabled until the integration phase.

## 5.2 Broker interface

Example conceptual interface:

```python
class Broker:
    async def get_account(self) -> BrokerAccount: ...
    async def get_symbol(self, symbol: str) -> SymbolInfo: ...
    async def open_buy(self, request: OpenTradeRequest) -> BrokerExecution: ...
    async def get_position(self, position_id: str) -> BrokerPosition: ...
    async def close_position(self, position_id: str) -> BrokerExecution: ...
    async def health(self) -> BrokerHealth: ...
```

Implementations:

```text
PaperBroker
MT5Broker
```

The trading engine must work against the abstract interface only.

---

# 6. Signal Parsing Design

The parser must be deterministic.

Do not use an LLM or generative AI to decide whether real money should be traded.

A parser may use regex, normalization, explicit grammar rules, and a state machine.

## 6.1 Recognized instrument aliases

Treat these as Gold:

```text
GOLD
XAU
XAUUSD
XAU/USD
```

Normalize all of them to:

```text
XAUUSD
```

## 6.2 Recognized BUY messages

Examples:

```text
BUY GOLD
GOLD BUY
BUY XAUUSD
BUY GOLD NOW
BUY GOLD 4633 - 4631
```

The first release should only execute BUY trades.

SELL, SELL LIMIT, BUY LIMIT, BUY STOP, and SELL STOP should be classified and stored but not executed unless explicitly enabled in a later version.

## 6.3 Stop Loss formats

Examples:

```text
SL 4623
SL: 4623
STOP LOSS 4623
STOPLOSS 4623
```

Normalize to a numeric stop-loss price.

## 6.4 Multi-message signals

Telegram may send:

Message A:

```text
BUY GOLD
```

Message B:

```text
4640 - 4637
SL 4623
```

Message C:

```text
4644
4650
4663
4677
```

The parser/state machine should behave as follows:

After Message A:

```text
signal_status = WAITING_FOR_DETAILS
```

After Message B:

```text
signal_status = READY
```

Message C may be stored as channel targets but must not affect our execution.

A configurable timeout should exist for incomplete signals.

Example:

```text
signal_completion_timeout_seconds = 120
```

If Stop Loss never arrives:

```text
signal_status = EXPIRED
```

and the signal must never execute.

---

# 7. Signal State Machine

Use explicit states.

Recommended states:

```text
RECEIVED
CLASSIFIED
WAITING_FOR_DETAILS
READY
VALIDATING
REJECTED
EXECUTION_PENDING
EXECUTED
FAILED
EXPIRED
IGNORED
DUPLICATE
```

Example:

```text
BUY GOLD
    ↓
RECEIVED
    ↓
CLASSIFIED
    ↓
WAITING_FOR_DETAILS
    ↓
SL 4623 received
    ↓
READY
    ↓
VALIDATING
    ↓
EXECUTION_PENDING
    ↓
EXECUTED
```

If Auto Trading is OFF:

```text
READY
    ↓
REJECTED
reason = AUTO_TRADING_DISABLED
```

If the same Telegram message is processed twice:

```text
DUPLICATE
```

---

# 8. Duplicate Protection

Duplicate protection is a critical safety feature.

A Telegram signal must never be executed twice because of reconnection, application restart, queue retry, or duplicated event delivery.

Create a unique database constraint on:

```text
source
channel_id
message_id
```

If multiple Telegram messages form one signal, create a stable internal signal identifier and track every message attached to it.

A trade must also have a unique execution key.

Example:

```text
execution_key = signal_id + strategy_version
```

Before opening a trade, the engine must check whether a trade already exists with that execution key.

This safety rule must be tested.

---

# 9. Paper Broker

The Paper Broker must be fully functional before MT5 integration.

It should simulate:

- Account balance
- Equity
- Gold bid/ask price
- Spread
- BUY order execution
- Stop Loss
- Take Profit
- Position state
- Trade closing
- Profit/loss
- Trade history

The Paper Broker does not need to perfectly reproduce every MT5 behavior, but it must accurately test the system's business logic.

## 9.1 Simulation controls

Add API endpoints and a dashboard page that allow:

```text
Set market price
Send mock Telegram message
Advance simulated price
Trigger TP
Trigger SL
Reset simulation account
```

Example test:

1. Account balance = 10,000.
2. Gold ask = 4640.00.
3. Dashboard target = 100 pips.
4. Lot size = 0.10.
5. Mock message: `BUY GOLD`.
6. Mock message: `SL 4623`.
7. Bot opens a paper trade.
8. Raise gold price until configured TP.
9. Paper Broker closes the trade.
10. Dashboard shows WIN.
11. Statistics update.

---

# 10. Pip and Price Calculation

Do not hard-code a universal assumption for Gold pips.

Different brokers may expose different:

- digits
- point values
- contract sizes
- minimum price changes

Internally, the broker adapter must return symbol metadata.

Example:

```python
class SymbolInfo:
    symbol: str
    digits: int
    point: Decimal
    contract_size: Decimal
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
```

The strategy configuration should allow the system to translate the user's requested target into price distance using the connected broker's actual symbol specification.

During paper trading, symbol metadata must be configurable to imitate the future broker.

Never assume that “100 pips” always equals a specific dollar movement until the actual MT5 symbol has been inspected.

The dashboard should eventually show both:

```text
Target: 100 pips
Calculated price distance: X
Calculated Take Profit: Y
```

This makes the result transparent.

---

# 11. Trading Rules Engine

All safety and strategy conditions should be centralized in a rules module.

Example decision:

```python
decision = rules.evaluate(signal, settings, account, open_positions)
```

Result:

```python
class TradingDecision:
    allowed: bool
    reason_code: str | None
    explanation: str
```

Required reason codes include:

```text
AUTO_TRADING_DISABLED
BUY_DISABLED
STOP_LOSS_REQUIRED
STOP_LOSS_INVALID
DUPLICATE_SIGNAL
MAX_OPEN_TRADES_REACHED
BROKER_UNAVAILABLE
INVALID_LOT_SIZE
INVALID_TARGET
UNSUPPORTED_SYMBOL
UNSUPPORTED_SIGNAL_TYPE
SIGNAL_EXPIRED
```

Every rejected trade must be recorded.

Never silently ignore an executable-looking signal.

---

# 12. Trade Lifecycle

A trade record should have explicit states.

Recommended:

```text
CREATED
SUBMITTING
OPEN
CLOSED_TP
CLOSED_SL
CLOSED_MANUAL
REJECTED
FAILED
UNKNOWN
```

Example lifecycle:

```text
Signal READY
    ↓
Trade CREATED
    ↓
SUBMITTING
    ↓
Broker confirms
    ↓
OPEN
    ↓
TP hit
    ↓
CLOSED_TP
```

If broker submission fails:

```text
SUBMITTING
    ↓
FAILED
```

Do not mark a trade as OPEN until the broker adapter has confirmed execution.

---

# 13. Database Design

Use PostgreSQL.

At minimum create the following tables.

## 13.1 `incoming_messages`

Fields:

```text
id
source
channel_id
message_id
reply_to_message_id
raw_text
normalized_text
sent_at
received_at
classification
created_at
```

Unique constraint:

```text
(source, channel_id, message_id)
```

## 13.2 `signals`

Fields:

```text
id
symbol
side
signal_type
entry_min
entry_max
channel_stop_loss
status
first_message_at
completed_at
expires_at
rejection_reason
created_at
updated_at
```

## 13.3 `signal_messages`

Links messages to signals.

```text
signal_id
message_id
role
```

Possible roles:

```text
ENTRY
DETAILS
STOP_LOSS
TARGETS
UPDATE
RESULT
OTHER
```

## 13.4 `trades`

Fields:

```text
id
signal_id
execution_key
broker
broker_order_id
broker_position_id
symbol
side
requested_lot
executed_lot
entry_price
stop_loss
target_pips
take_profit_price
opened_at
closed_at
exit_price
profit
profit_currency
status
result
close_reason
failure_reason
created_at
updated_at
```

## 13.5 `trading_settings`

Store active settings.

Fields:

```text
id
auto_trading
mode
buy_enabled
sell_enabled
lot_size
target_pips
require_stop_loss
max_open_trades
signal_completion_timeout_seconds
created_at
updated_at
```

Possible modes:

```text
SIMULATION
DEMO
LIVE
```

Default must be:

```text
SIMULATION
```

## 13.6 `audit_events`

Fields:

```text
id
event_type
severity
message
metadata_json
created_at
```

Example event types:

```text
SIGNAL_RECEIVED
SIGNAL_READY
SIGNAL_REJECTED
TRADE_SUBMISSION_STARTED
TRADE_OPENED
TRADE_CLOSED
BROKER_ERROR
SETTINGS_CHANGED
SERVICE_STARTED
SERVICE_STOPPED
```

---

# 14. Statistics

Statistics must always be derived from completed trade data.

Do not store a mutable win-rate value as the primary source of truth.

Calculate:

```text
closed_trades = wins + losses

win_rate =
wins / closed_trades * 100
```

If no closed trades exist:

```text
win_rate = 0
```

Required dashboard statistics:

- Total signals received
- Executable signals
- Ignored messages
- Rejected signals
- Trades executed
- Open trades
- Closed trades
- Wins
- Losses
- Win rate
- Net profit
- Gross profit
- Gross loss
- Average win
- Average loss
- Best trade
- Worst trade
- Today's trades
- Today's P/L
- Current balance
- Current equity when available

Open trades must not count as wins or losses.

---

# 15. Dashboard Requirements

The dashboard must be usable even before Telegram and MT5 are connected.

## 15.1 Home page

Show:

```text
Bot Status
Signal Source Status
Broker Status
Trading Mode
Auto Trading Status
```

Performance cards:

```text
Balance
Today's P/L
Total Trades
Open Trades
Wins
Losses
Win Rate
Net Profit
```

Show latest trades and latest events.

## 15.2 Trading Settings page

Controls:

```text
Auto Trading
Lot Size
Take Profit Pips
Maximum Open Trades
BUY Enabled
Require Stop Loss
Trading Mode
```

Dangerous settings should require confirmation in DEMO/LIVE modes.

## 15.3 Trades page

Columns:

```text
Trade ID
Signal Time
Open Time
Close Time
Symbol
Side
Lot
Entry
Stop Loss
Take Profit
Target Pips
Exit
Profit
Status
Result
```

Allow filtering by:

```text
Date
WIN
LOSS
OPEN
FAILED
```

## 15.4 Signals page

Display:

```text
Signal ID
Source message
Detected action
Detected symbol
Detected SL
Status
Execution result
Linked trade
```

This page is critical for debugging the parser.

## 15.5 Analytics page

Show:

- Win rate
- Profit curve
- Trades by day
- Wins vs losses
- Average P/L
- Performance by configured target
- Performance over time

## 15.6 Logs page

Show audit events.

Example:

```text
03:14:01 SIGNAL_RECEIVED
03:14:01 BUY signal detected
03:14:02 Waiting for Stop Loss
03:14:08 Stop Loss received
03:14:08 SIGNAL_READY
03:14:09 Trade opened
03:20:31 Take Profit reached
03:20:31 Trade closed
```

## 15.7 Simulation page

This is mandatory before real integrations.

Features:

```text
Mock Telegram Message input
Current simulated Gold price
Set price
Increase/decrease price
Send Signal
Send SL
Reset account
Reset trades
```

The page should make it possible to test the entire system manually from the browser.

---

# 16. API Design

Suggested endpoints:

## Health

```text
GET /api/health
GET /api/health/signal-source
GET /api/health/broker
```

## Dashboard

```text
GET /api/dashboard/summary
GET /api/dashboard/recent-trades
GET /api/dashboard/recent-events
```

## Settings

```text
GET /api/settings
PATCH /api/settings
```

## Trades

```text
GET /api/trades
GET /api/trades/{id}
```

Do not expose a direct live trade endpoint in the first release unless required later.

## Signals

```text
GET /api/signals
GET /api/signals/{id}
```

## Simulation

Development/simulation only:

```text
POST /api/simulation/messages
POST /api/simulation/market-price
POST /api/simulation/reset
GET  /api/simulation/state
```

These endpoints must be disabled in LIVE production mode.

---

# 17. Automated Testing Requirements

Testing is a first-class requirement.

Do not connect real Telegram or real MT5 until the automated tests are strong.

## 17.1 Parser tests

Required examples:

```text
BUY GOLD
→ BUY XAUUSD
```

```text
BUY XAUUSD
→ BUY XAUUSD
```

```text
BUY GOLD 4633 - 4631
→ BUY + entry range
```

```text
SL 4618
→ Stop Loss = 4618
```

```text
TP1 ✅ 80 pips
→ informational update, no execution
```

```text
120 pips 🚀
→ informational update, no execution
```

```text
Good morning
→ ignored
```

```text
SELL LIMIT GOLD 4546 - 4545
→ classified but not executable in v1
```

## 17.2 State machine tests

Test:

```text
BUY → WAITING_FOR_DETAILS
BUY + SL → READY
BUY without SL until timeout → EXPIRED
duplicate message → DUPLICATE
```

## 17.3 Trading rules tests

Test:

```text
Auto Trading OFF → rejected
BUY disabled → rejected
No SL → rejected
Invalid lot → rejected
Max trades reached → rejected
Broker unhealthy → rejected
Valid BUY + SL → allowed
```

## 17.4 Paper Broker tests

Test:

```text
Open BUY
TP reached
SL reached
Position remains open while price is between SL/TP
Profit is calculated
Closed position cannot close twice
```

## 17.5 End-to-end simulation tests

Example test case:

```text
Given:
Auto Trading ON
Lot = 0.10
Target = 100 pips
Max open trades = 1

When:
Mock Telegram sends BUY GOLD
Mock Telegram sends SL 4623
Paper price is 4640

Then:
Exactly one trade opens

When:
Price reaches calculated TP

Then:
Trade closes
Result = WIN
Statistics = 1 trade, 1 win, 100% win rate
```

Another case:

```text
When:
Price reaches Stop Loss

Then:
Trade closes
Result = LOSS
Statistics update correctly
```

Another:

```text
Same Telegram message delivered twice

Then:
Only one trade exists
```

---

# 18. Development Phases

The project must be delivered phase by phase.

Do not jump directly to Telegram/MT5 integration.

## Phase 0 — Project Foundation

Tasks:

- Initialize repository.
- Create backend.
- Create dashboard.
- Create PostgreSQL container.
- Add linting.
- Add tests.
- Add `.env.example`.
- Create docs directory.
- Add CI if available.

Acceptance:

```text
Backend starts.
Dashboard starts.
Database connects.
Tests run.
Lint passes.
```

## Phase 1 — Database and Domain Models

Implement:

- Incoming messages
- Signals
- Trades
- Settings
- Audit events

Acceptance:

```text
Migrations work from an empty database.
Models have tests.
Unique constraints work.
```

## Phase 2 — Signal Parser

Implement:

- Normalization
- Gold aliases
- BUY detection
- SL extraction
- Entry range extraction
- Message classification
- Non-execution message detection

Acceptance:

All parser fixtures pass.

## Phase 3 — Signal State Machine

Implement multi-message signal assembly.

Acceptance:

```text
BUY followed by SL becomes READY.
Missing SL expires.
Target messages do not create new trades.
```

## Phase 4 — Trading Settings and Rules

Implement:

- Settings service
- Rule validation
- Rejection reasons

Acceptance:

Every safety rule has a unit test.

## Phase 5 — Paper Broker

Implement:

- Simulated account
- Simulated symbol
- Buy execution
- TP/SL
- Profit/loss
- Position lifecycle

Acceptance:

Paper trades behave correctly under controlled price movement.

## Phase 6 — Trading Engine

Connect:

```text
Signal → Rules → Broker → Database
```

Acceptance:

End-to-end simulated trade works without Telegram or MT5.

## Phase 7 — Backend API

Implement all required endpoints.

Acceptance:

API tests pass.

## Phase 8 — Dashboard

Build:

- Home
- Settings
- Trades
- Signals
- Analytics
- Logs
- Simulation

Acceptance:

A user can test a full fake trade from the dashboard.

## Phase 9 — Reliability

Implement:

- Structured logs
- Graceful startup/shutdown
- Health checks
- Reconnection abstractions
- Background worker lifecycle
- Idempotency
- Transaction-safe execution records

Acceptance:

Restarting the backend does not duplicate a trade.

## Phase 10 — Documentation

Complete all files inside `/docs`.

Acceptance:

A new developer should understand how to run, test, and extend the project without asking for undocumented architecture details.

## Phase 11 — Telegram Integration

Only after previous phases pass.

Implement:

```text
TelegramSignalSource
```

Connect to one approved Telegram channel.

First mode:

```text
READ ONLY
```

Telegram must feed messages into the existing parser, but trading remains in `SIMULATION`.

Acceptance:

Real Telegram messages produce the same normalized events as mock messages.

Do not enable MT5 at this phase.

## Phase 12 — MT5 Demo Integration

Implement:

```text
MT5Broker
```

Connect only to a DEMO account.

The adapter must retrieve real symbol metadata before calculating TP.

Acceptance:

```text
Real Telegram
→ parser
→ rules
→ MT5 DEMO
```

works successfully under controlled tests.

## Phase 13 — VPS Deployment

Deploy to a Windows VPS.

Requirements:

- Automatic restart
- MT5 terminal available
- Backend service startup
- Database connection
- Dashboard HTTPS
- Health monitoring
- Log retention

Acceptance:

System survives reboot and reconnects safely.

## Phase 14 — Live Readiness

Live mode must remain locked until a formal checklist is completed.

Do not automatically switch from DEMO to LIVE.

---

# 19. Telegram Integration Rules for Later

The final Telegram adapter should use the user's Telegram account through MTProto when required because the account is already subscribed to the private/premium signal channel.

Credentials must be stored outside source control.

Expected environment variables:

```text
TELEGRAM_ENABLED=false
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_SESSION_PATH=
TELEGRAM_ALLOWED_CHANNEL_ID=
```

Never commit:

```text
api_hash
session file
login code
2FA password
```

The adapter must only process messages from the configured channel ID.

Do not process arbitrary Telegram chats.

The real Telegram adapter should emit the exact same internal `IncomingMessage` object used by the mock source.

This means the parser must not need any modifications when Telegram is connected.

---

# 20. MT5 Integration Rules for Later

The MT5 adapter should remain isolated in:

```text
backend/app/integrations/broker/mt5.py
```

Expected environment configuration:

```text
MT5_ENABLED=false
MT5_ACCOUNT=
MT5_PASSWORD=
MT5_SERVER=
MT5_TERMINAL_PATH=
MT5_SYMBOL=XAUUSD
```

Never commit real credentials.

When connecting:

1. Initialize MT5.
2. Confirm account identity.
3. Confirm DEMO/LIVE environment.
4. Resolve the actual Gold symbol.
5. Read symbol metadata.
6. Confirm trading is allowed.
7. Confirm lot constraints.
8. Confirm point/digits.
9. Confirm market data exists.
10. Run a health check.

Before opening an order, validate:

```text
requested lot >= volume_min
requested lot <= volume_max
requested lot matches volume_step
SL is valid for a BUY
TP is valid for a BUY
```

The broker result must be persisted exactly.

If MT5 rejects an order, never create a fake OPEN trade.

---

# 21. Operational Safety

This is automated financial software.

The application must fail safely.

If something is unknown:

```text
DO NOT TRADE
```

Examples:

```text
Unknown signal → no trade
Missing SL → no trade
Broker unavailable → no trade
Invalid price → no trade
Invalid symbol metadata → no trade
Duplicate execution status uncertain → no new trade
Database unavailable → no trade
```

Avoid “best guess” behavior.

The system should prefer missing a signal over creating an unintended trade.

---

# 22. Error Handling

Every important failure must have:

- Error code
- Human-readable explanation
- Timestamp
- Signal ID if available
- Trade ID if available
- Context metadata

Examples:

```text
TELEGRAM_SOURCE_DISCONNECTED
SIGNAL_PARSE_FAILED
SIGNAL_EXPIRED
BROKER_CONNECTION_FAILED
ORDER_REJECTED
DATABASE_WRITE_FAILED
DUPLICATE_EXECUTION_BLOCKED
```

Dashboard should show the most recent critical events.

---

# 23. Logging

Use structured JSON logging in the backend.

Every log should include useful correlation IDs:

```text
request_id
signal_id
trade_id
message_id
```

Never log secrets.

Do not log:

```text
Telegram API hash
Telegram session secret
MT5 password
2FA codes
```

---

# 24. Documentation Requirements

The project is not complete until the `/docs` folder is maintained.

## `docs/01-architecture.md`

Explain:

- Services
- Data flow
- Adapters
- Process model
- Why mock integrations exist

## `docs/02-signal-parser.md`

Document every recognized message format and every ignored format.

Include examples from the real channel after integration.

## `docs/03-trading-engine.md`

Document:

- Validation order
- Execution lifecycle
- Rejection rules
- Idempotency behavior

## `docs/04-paper-broker.md`

Document simulation assumptions and controls.

## `docs/05-database.md`

Document tables, constraints, and relationships.

## `docs/06-api.md`

Document all backend endpoints.

## `docs/07-dashboard.md`

Document each screen.

## `docs/08-testing.md`

Explain test commands and test categories.

## `docs/09-telegram-integration.md`

Initially document the adapter contract.

After integration, document setup without including secrets.

## `docs/10-mt5-integration.md`

Initially document the broker contract.

After integration, document Demo setup and symbol verification.

## `docs/11-deployment.md`

Document local and VPS deployment.

## `docs/12-operations.md`

Explain normal startup, shutdown, restart, failure recovery, and monitoring.

## `docs/13-security.md`

Document secrets, authentication, permissions, and protected endpoints.

## `docs/14-live-readiness-checklist.md`

Contain the exact conditions required before enabling Live mode.

---

# 25. Suggested README

The root `README.md` should remain short.

It should include:

- Project purpose
- Development status
- Current mode
- Quick start
- Test command
- Dashboard URL
- Backend URL
- Link to `PROJECT_SPEC.md`
- Link to `/docs`

Do not duplicate this entire specification into README.

---

# 26. Environment Configuration

Example `.env.example`:

```env
APP_ENV=development
LOG_LEVEL=INFO

DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/goldbot

TRADING_MODE=SIMULATION
AUTO_TRADING=false

PAPER_STARTING_BALANCE=10000
PAPER_SYMBOL=XAUUSD
PAPER_POINT=0.01
PAPER_DIGITS=2

TELEGRAM_ENABLED=false
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_SESSION_PATH=
TELEGRAM_ALLOWED_CHANNEL_ID=

MT5_ENABLED=false
MT5_ACCOUNT=
MT5_PASSWORD=
MT5_SERVER=
MT5_TERMINAL_PATH=
MT5_SYMBOL=XAUUSD
```

Production secrets must use secure environment configuration and must not be committed.

---

# 27. Security for the Dashboard

The production dashboard must not be publicly open.

Before deployment, add authentication.

At minimum:

- Secure login
- Strong password hashing
- Session expiration
- CSRF protection where applicable
- Rate limiting for login
- Protected settings endpoints
- Audit log when settings change

Changing:

```text
Trading Mode
Auto Trading
Lot Size
Target Pips
```

must create an audit event.

---

# 28. Live Mode Protection

Do not make Live mode just a simple dropdown.

Recommended design:

```text
SIMULATION
DEMO
LIVE
```

To switch to LIVE:

1. User must explicitly choose LIVE.
2. System displays a warning.
3. User confirms.
4. Backend validates MT5 account type and configuration.
5. Audit log records the change.

Optionally require typing:

```text
ENABLE LIVE TRADING
```

before activation.

---

# 29. Live Readiness Checklist

Before connecting a real-money account, verify all of the following:

- [ ] Parser test suite passes.
- [ ] State machine test suite passes.
- [ ] Duplicate protection tests pass.
- [ ] Paper Broker tests pass.
- [ ] Trading Engine tests pass.
- [ ] API tests pass.
- [ ] Dashboard simulation works.
- [ ] At least 100 simulated signal scenarios have been tested.
- [ ] Telegram read-only integration works.
- [ ] Real Telegram messages are parsed correctly.
- [ ] Telegram reconnect behavior is tested.
- [ ] MT5 Demo connection works.
- [ ] Gold symbol metadata is verified.
- [ ] Pip calculation is verified against the broker.
- [ ] Stop Loss placement is verified.
- [ ] Take Profit placement is verified.
- [ ] Demo duplicate prevention is verified.
- [ ] Demo restart recovery is verified.
- [ ] VPS restart recovery is verified.
- [ ] Error logs contain no secrets.
- [ ] Dashboard is authenticated.
- [ ] Simulation endpoints are disabled in Live mode.
- [ ] Backup/restore procedure is documented.
- [ ] Manual emergency stop exists.
- [ ] Owner has reviewed trading settings.

Only then may LIVE mode be considered.

---

# 30. Definition of Done

The project is considered internally complete before Telegram/MT5 connection when all of the following work:

1. The project runs locally.
2. PostgreSQL migrations work.
3. Dashboard loads.
4. Mock Telegram messages can be submitted.
5. Parser correctly detects BUY GOLD.
6. Parser correctly extracts Stop Loss.
7. Multi-message signals work.
8. Telegram targets are ignored for execution.
9. Dashboard controls lot size.
10. Dashboard controls Take Profit pips.
11. Auto Trading can be enabled/disabled.
12. Paper Broker opens simulated BUY positions.
13. Paper Broker closes by TP.
14. Paper Broker closes by SL.
15. Trade history is recorded.
16. Win/loss status is correct.
17. Win rate is calculated correctly.
18. Duplicate signals never create duplicate trades.
19. Incomplete signals expire safely.
20. Maximum open trade limit works.
21. Logs are visible.
22. Statistics are visible.
23. Automated tests pass.
24. Documentation is complete.
25. Telegram adapter exists behind a disabled integration flag.
26. MT5 adapter exists behind a disabled integration flag.
27. No real credentials are required for development.
28. No real-money trade can occur accidentally.

At this point, the only remaining work is integration:

```text
Connect Telegram
→ verify read-only behavior
→ run against simulation

Connect MT5 Demo
→ verify broker behavior
→ run controlled Demo tests

Deploy to VPS
→ verify reliability

Only then:
consider Live mode
```

---

# 31. Example Full Simulation Scenario

Use the following scenario as an acceptance test.

Dashboard:

```text
Trading Mode: SIMULATION
Auto Trading: ON
BUY Enabled: ON
Lot: 0.10
Target: 100 pips
Max Open Trades: 1
Require Stop Loss: ON
```

Mock channel message:

```text
BUY GOLD
```

Expected:

```text
Signal created
Status = WAITING_FOR_DETAILS
No trade
```

Second message:

```text
4640 - 4637
SL 4623
```

Expected:

```text
Signal updated
Entry range stored
Stop Loss = 4623
Status = READY
```

Trading engine validates the signal.

If current paper Ask is:

```text
4638.50
```

the Paper Broker opens:

```text
BUY XAUUSD
Lot = 0.10
Entry = 4638.50
SL = 4623
TP = calculated from 100 configured pips
```

The database records the trade as:

```text
OPEN
```

The simulation price moves upward.

When TP is reached:

```text
status = CLOSED_TP
result = WIN
```

Dashboard changes:

```text
Total Trades = 1
Closed Trades = 1
Wins = 1
Losses = 0
Win Rate = 100%
```

Now send the original `BUY GOLD` message again with the same source/message ID.

Expected:

```text
DUPLICATE
No second trade
```

This scenario must pass automatically before real integration.

---

# 32. Important Implementation Rule for Coding Agents

When using coding agents such as Claude Code, Codex, or similar tools, instruct them to follow these rules:

1. Read `PROJECT_SPEC.md` before changing architecture.
2. Read the relevant file under `/docs`.
3. Do not connect real Telegram or MT5 unless explicitly instructed.
4. Keep `TELEGRAM_ENABLED=false` and `MT5_ENABLED=false` by default.
5. Keep `TRADING_MODE=SIMULATION` by default.
6. Add tests for every new trading rule.
7. Never bypass duplicate protection.
8. Never execute without Stop Loss when required.
9. Never hard-code Gold pip conversion without symbol metadata.
10. Never store secrets in source code.
11. Update documentation when behavior changes.
12. Keep the trading engine independent from Telegram and MT5 implementations.
13. Any ambiguity that could create a trade must default to no trade.
14. Do not silently change strategy behavior.
15. All production-sensitive changes must be logged and tested.

---

# 33. Final Product Vision

When development is complete, the owner should be able to open the dashboard and see:

```text
TELEGRAM
Connected

MT5
Connected

Trading Mode
DEMO or LIVE

Auto Trading
ON

Lot Size
0.10

Profit Target
100 pips

Open Trades
1

Total Trades
184

Wins
137

Losses
47

Win Rate
74.46%

Today's P/L
+$82.40
```

If a valid Telegram signal arrives while the owner is asleep:

```text
BUY GOLD
```

and then:

```text
SL 4623
```

the final system should:

1. Receive the messages automatically.
2. Normalize and store them.
3. Assemble the complete signal.
4. Confirm it is not duplicated.
5. Confirm all trading rules.
6. Read the configured lot size.
7. Read the configured target pips.
8. Open one BUY trade.
9. Apply the Telegram Stop Loss.
10. Apply our calculated Take Profit.
11. Record the broker execution.
12. Continue running without manual intervention.
13. When TP or SL occurs, update the trade.
14. Recalculate statistics.
15. Show the result in the dashboard.
16. Write the full lifecycle to the audit log.

The user should not need to manually copy Telegram signals into MT5.

However, the development architecture must make the Telegram and MT5 connections the **last integration step**, not the first.

That design keeps the system testable, safer, easier to debug, and much easier to extend later.

---

# 34. Initial Implementation Priority

Start development in this exact order:

```text
1. Repository and local infrastructure
2. Database
3. Domain models
4. Parser
5. Signal state machine
6. Settings
7. Trading rules
8. Paper Broker
9. Trading Engine
10. Trade persistence
11. Statistics
12. Backend API
13. Dashboard
14. Simulation tools
15. Automated tests
16. Reliability and logging
17. Documentation completion
18. Telegram adapter integration
19. Telegram read-only testing
20. MT5 Demo adapter integration
21. Demo validation
22. VPS deployment
23. Live readiness review
24. Optional Live activation
```

Do not reorder this list merely to obtain a quick live demo.

The first milestone should prove the strategy logic in simulation.

The second milestone should prove the dashboard and full trade lifecycle.

The third milestone should prove Telegram data ingestion.

The fourth milestone should prove MT5 Demo execution.

Only after these milestones are stable should the application be considered ready for real-money evaluation.

---

**End of master specification.**
