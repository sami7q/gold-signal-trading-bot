# Final Copy-Trading Strategy: "Loco 20% Cap, Cross" (Bybit Demo)

Version: 1.0 · 2026-10-11
Target win rate from the backtest: about 81% · Execution venue: Bybit USDT perpetuals (Demo account) · Source venue: Hyperliquid

This document is the complete specification. Implement it literally. Anything not listed here is not part of the strategy.

---

## 1. Parameters (config)

```json
{
  "source_wallet": "0xf97ad6704baec104d00b88e0c157e2b7b3a1ddd1",
  "source_api": "https://api.hyperliquid.xyz/info",
  "source_ws": "wss://api.hyperliquid.xyz/ws",
  "exchange": "bybit",
  "account": "demo",
  "category": "linear",
  "quote": "USDT",
  "margin_mode": "CROSS",
  "leverage": 5,
  "size_multiplier_K": "0.8 * leverage",
  "per_trade_margin_cap_pct": 20,
  "order_type": "Market",
  "take_profit": null,
  "stop_loss": null,
  "copy_xyz_dex": false,
  "copy_spot": false,
  "copy_positions_open_before_start": false,
  "min_order_usdt": "use Bybit instrument minNotionalValue (fallback 5)",
  "fill_poll_seconds": 2,
  "reconcile_seconds": 60,
  "wallet_equity_refresh_seconds": 300,
  "safety_max_total_notional_x_equity": 6.0
}
```

- `leverage`: 5 (aggressive) or 3 (calmer). Both had 0 liquidations in the backtest. Keep the same value for the whole run.
- `size_multiplier_K` = 0.8 × leverage, so K = 4.0 at 5x and K = 2.4 at 3x.

---

## 2. Data from the source wallet (Hyperliquid)

1. **New fills.** Subscribe to the WebSocket `{"method":"subscribe","subscription":{"type":"userFills","user":SOURCE}}`, or poll `{"type":"userFillsByTime","user":SOURCE,"startTime":last_ms}` every 2 s. De-duplicate by `tid`.
2. **Fill fields used:** `coin`, `px`, `sz`, `side` ("B" = buy, "A" = sell), `startPosition`, `time`, `tid`.
   - Wallet position after the fill: `wallet_pos_after = startPosition + (side=="B" ? sz : -sz)`. The sign gives the direction (+ long, − short).
3. **Ignore** any fill whose `coin` starts with `@`, contains `/` (spot), or contains `:` (xyz/HIP-3 stocks and commodities).
4. **Wallet equity W.** Use `{"type":"portfolio","user":SOURCE}`, the last value of the `"day"` entry → `accountValueHistory`. Refresh every 5 minutes and cache it.
5. **Reconciliation source.** Use `{"type":"clearinghouseState","user":SOURCE}` → `assetPositions[].position.szi` per coin, every 60 s.

---

## 3. Symbol mapping (Hyperliquid → Bybit)

- Default: `{COIN}USDT`, for example HYPE → HYPEUSDT, BTC → BTCUSDT, ETH → ETHUSDT, SOL → SOLUSDT.
- Prefix `k` means ×1000 on Hyperliquid: kPEPE → 1000PEPEUSDT, kSHIB → 1000SHIBUSDT, kBONK → 1000BONKUSDT, kFLOKI → 1000FLOKIUSDT. Convert size by ÷1000 and price by ×1000.
- Check every symbol against Bybit `GET /v5/market/instruments-info?category=linear`. If a symbol is not listed or not trading, **skip that whole trade** and log it.
- Round quantities down to `lotSizeFilter.qtyStep`, and respect `minOrderQty` and `minNotionalValue`.

---

## 4. Trade lifecycle (per coin)

A **trade** starts when the source wallet's position in a coin goes from 0 to non-zero. It ends when the position returns to 0.

### 4.1 Start of a trade (first fill of a new wallet position)

When `startPosition == 0` and `wallet_pos_after != 0`:

```
side         = sign(wallet_pos_after)            # +1 long, -1 short
E0           = our Bybit total equity now (USDT, unified account totalEquity)
W0           = cached source wallet equity
R            = K * E0 / W0                        # copy ratio, fixed for this whole trade
CAP_NOTIONAL = (per_trade_margin_cap_pct/100) * leverage * E0
                                                  # = 1.0 × E0 at 5x, 0.6 × E0 at 3x
set Bybit leverage for symbol = min(leverage, bybit_max_leverage)  (cross margin)
```

Then treat this first fill as an **add** (4.2).

**Positions open before bot start:** take a snapshot of the source's open positions at startup. Do **not** copy them. Ignore all fills on those coins until the source position in that coin returns to 0. After that, the next new trade is copied normally.

### 4.2 Add (source position grows in absolute size)

```
dq_wallet = |wallet_pos_after| - |wallet_pos_before|      # > 0
pending  += dq_wallet * R
room_qty  = max(0, CAP_NOTIONAL - our_qty * our_avg_entry) / current_price
pending   = min(pending, room_qty)
if pending * price >= bybit_min_notional:
    MARKET order, same direction as side, qty = round_down(pending)
    pending = 0
```

- When `room_qty == 0`, the trade is **capped**: ignore all further adds for this trade, but keep following reductions.
- If free margin is not enough, place the largest order that the available balance allows.

### 4.3 Reduce (source position shrinks but is not zero)

```
fraction    = |wallet_pos_after| / |wallet_pos_before|
target_qty  = our_qty * fraction
reduce_qty  = our_qty - target_qty
MARKET reduce-only order, qty = round_down(reduce_qty)   (skip if below min; reconciliation will catch it)
pending     = pending * fraction
```

### 4.4 Full close (source position goes to 0)

When `wallet_pos_after == 0`: MARKET reduce-only order to close **100%** of our position in that symbol. The trade ends; clear `R`, `CAP_NOTIONAL` and `pending`.

### 4.5 Flip (source goes from long to short or the reverse in one fill)

Close our position 100% (as 4.4). Then start a new trade (4.1) with `wallet_pos_after` as the first add.

---

## 5. Reconciliation (every 60 s)

For each coin with an active copied trade:

```
target = min( |source_szi| * R , CAP_NOTIONAL / our_avg_entry )   (only if trade not capped;
         if capped: target = our_qty_at_cap * |source_szi| / |source_szi_at_cap|)
if source_szi == 0           -> close ours 100%
if |our_qty - target| * price > max(bybit_min_notional, 0.5% of E0) -> correct with a market order
```

Never open a new trade from reconciliation alone; only fills start trades.

---

## 6. What the strategy does NOT do (keep it this way to match the backtest)

- No stop loss, no take profit, no trailing stop. Exits happen **only** when the source wallet exits.
- No averaging beyond the 20% margin cap.
- No trading of xyz stocks or commodities, or of spot.
- No manual overrides during the test.

---

## 7. Safety limits (bot-level; never triggered in the backtest)

- If total open notional would exceed `6.0 × equity`, block new adds and log it. The backtest maximum was about 5.3× at 5x.
- If the Hyperliquid API or WebSocket is unavailable for more than 2 minutes: block new trades and adds, keep existing positions, resume and reconcile when it is back.
- If a Bybit order is rejected: retry once, then log and continue.
- Log every action: time, source fill `tid`, symbol, side, qty, price, R, `CAP_NOTIONAL`, equity, reason.

---

## 8. Expected behaviour (benchmarks for the demo)

From the backtest of 11/10/2025 to 11/10/2026, independently verified. Realistic Bybit assumptions: crypto only, taker fees plus slippage 0.095% per side, no funding.

| | 3x cross | 5x cross |
|---|---|---|
| $10,000 after 1 year | ≈ $17,841 (+78%) | ≈ $25,518 (+155%) |
| Win rate | ≈ 80–81% | ≈ 80–81% |
| Max drawdown (live equity) | ≈ 16% | ≈ 27% |
| Liquidations | 0 | 0 |

- About 690 trades a year (roughly 2 a day). Median holding time is about 7 hours.
- Losing trades mostly happen when a trade hits the 20% cap and the source keeps averaging down, then exits near its own break-even.
- Expect temporary open losses on single positions of up to about 20% of the account (HYPE, December 2025) before recovery.
- Funding payments are not included in the benchmark and will reduce results somewhat.

**Disclaimer:** this is a backtest-based specification for a demo account, not financial advice. Past performance does not guarantee future results.
