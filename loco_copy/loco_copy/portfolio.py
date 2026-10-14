"""The trio live simulator (botup.md §V6): the wallets of `PORTFOLIO_WALLETS` (8bae + 8fc7 + 69b0, each sized on
the whole account as in v5 -- weight 1.0) copied into one virtual cross account at 3x -- every move they make,
crypto AND the HIP-3 markets (xyz: stocks / commodities ...), priced on Hyperliquid itself.
No order is ever sent anywhere.

Two ledgers per starting balance (PORTFOLIO_START_EQUITIES, default $10k / $50k / $100k) run side by side on the
same fills, named "<scenario>:<capital>" (the two scenarios of the spec):
    base  (أساسي) -- our fill at the wallet's own price, fee 0.045 % per side: the backtest, replayed live;
    real  (واقعي) -- our fill at Hyperliquid's live mid the moment the bot SAW the fill, plus 0.045 % fee and
                     0.05 % slippage (0.095 %); a fill seen later than PORTFOLIO_STALE_SECONDS (feed outage, bot
                     down) opens / adds nothing (exits are still followed, at the live price).

Inputs (all public Hyperliquid data, no keys):
    - the bot's WebSocket: `userFills` of every wallet (raw, every dex) and `allMids` of every perp dex;
    - REST `userFillsByTime` every PORTFOLIO_POLL_SECONDS per wallet (in turn) -- fills the WS gaps;
    - REST `portfolio` -> each wallet's equity W (the "day" account value, refreshed every 4 min);
    - REST `clearinghouseState` per wallet (main dex + the dexes we hold) -- an exit we missed is closed
      after two snapshots agree the wallet is flat (never on one snapshot, never while a fill is in flight).
State: `state['portfolio:state']` (both ledgers + feed position), tables `pf_seen` / `pf_events` / `pf_trades`,
equity history under `portfolio:<scenario>:<capital>`, dashboard view in `state['portfolio_runtime']`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loco_copy.config import Settings
from loco_copy.feed import HyperliquidClient
from loco_copy.models import LeaderFill
from loco_copy.notify import Notifier
from loco_copy.pf_ledger import DAY_MS, Ledger, Outcome, split_key
from loco_copy.store import Store
from loco_copy.tracker import aggregate_burst, chain_order

#: v2 (2026-10-14): wallets 8bae + 8fc7 + 69b0 and three capitals -- the v1 state (Loco trio, $10k) is left unused
STATE_KEY = "portfolio:state:v2"
RUNTIME_KEY = "portfolio_runtime"
MARK_SECONDS = 10
EQUITY_HISTORY_SECONDS = 300
SNAPSHOT_SECONDS = 60
#: a mid older than this is not used as our price (the wallet's own price is, and the fill is flagged)
MID_MAX_AGE_MS = 15_000
#: the wallet's equity W may be this old before a new trade of that wallet is skipped
WALLET_EQUITY_MAX_AGE_MS = 6 * 3_600_000
SCENARIOS = ("base", "real")


def _now() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True, slots=True)
class Wallet:
    label: str
    address: str
    weight: float


def parse_wallets(raw: str) -> list[Wallet]:
    """`PORTFOLIO_WALLETS=loco:0x...:0.50,8477:0x...:0.25` -- label:address:weight."""
    out = []
    for part in (p.strip() for p in raw.split(",") if p.strip()):
        bits = part.split(":")
        if len(bits) != 3:
            raise ValueError(f"PORTFOLIO_WALLETS: {part!r} must be label:0xaddress:weight")
        label, address, weight = bits[0].strip().lower(), bits[1].strip().lower(), float(bits[2])
        if not (address.startswith("0x") and len(address) == 42):
            raise ValueError(f"PORTFOLIO_WALLETS: {address!r} is not an address")
        if not 0 < weight <= 1:
            raise ValueError(f"PORTFOLIO_WALLETS: weight of {label} must be in (0, 1]")
        out.append(Wallet(label, address, weight))
    if len({w.label for w in out}) != len(out) or len({w.address for w in out}) != len(out):
        raise ValueError("PORTFOLIO_WALLETS: labels and addresses must be unique")
    return out


def parse_any_perp_fill(raw: dict[str, Any], received_at_ms: int, *, hip3: bool) -> LeaderFill | None:
    """Every perp dex (main + HIP-3 `dex:COIN`); spot (`@1`, `X/USDC`) and outcome contracts (`#1`) dropped."""
    coin = str(raw.get("coin", ""))
    if not coin or coin[0] in "@#" or "/" in coin or (":" in coin and not hip3):
        return None
    side = raw.get("side")
    if side not in ("B", "A"):
        return None
    return LeaderFill(coin=coin, side=side, px=Decimal(str(raw["px"])), sz=Decimal(str(raw["sz"])),
                      start_position=Decimal(str(raw.get("startPosition") or "0")), time_ms=int(raw["time"]),
                      tid=int(raw["tid"]), oid=int(raw["oid"]) if raw.get("oid") is not None else None,
                      received_at_ms=received_at_ms)


def parse_capitals(raw: str) -> list[int]:
    """`PORTFOLIO_START_EQUITIES=10000,50000,100000` -- one pair of ledgers (base / real) per starting balance."""
    out = sorted({int(float(x)) for x in raw.split(",") if x.strip()})
    if not out or any(c <= 0 for c in out):
        raise ValueError("PORTFOLIO_START_EQUITIES: one or more positive amounts, e.g. 10000,50000,100000")
    return out


def ledger_name(scenario: str, capital: int) -> str:
    return f"{scenario}:{capital}"


def dex_of(coin: str) -> str:
    return coin.split(":", 1)[0] if ":" in coin else ""


class PortfolioSim:
    def __init__(self, settings: Settings, store: Store, notifier: Notifier, client: HyperliquidClient,
                 clock: Callable[[], int] = _now) -> None:
        s = settings
        self.s = s
        self.store = store
        self.n = notifier
        self.client = client
        self.clock = clock
        self.wallets = parse_wallets(s.portfolio_wallets)
        self.by_address = {w.address: w for w in self.wallets}
        self.maxlev_map: dict[str, float] = {}
        self.mids: dict[str, tuple[float, int]] = {}  # coin -> (mid, when)
        self.dexes: list[str] = [""]  # "" = main dex; HIP-3 dex names added from perpDexs
        self.wallet_equity: dict[str, tuple[float, int]] = {}  # address -> (value, when)
        self.last_seen: dict[str, int] = {}  # address -> newest fill time processed
        self.flat_seen: dict[str, int] = {}  # wallet|coin -> snapshots in a row showing the wallet flat
        self.last_fill_at: dict[str, int] = {}  # wallet|coin -> when we last processed a fill of it
        self.lock = asyncio.Lock()
        self.started_ms = 0
        self.capitals = parse_capitals(s.portfolio_start_equities)
        self.ledgers: dict[str, Ledger] = {}
        for cap in self.capitals:  # "base:10000", "real:10000", "base:50000", ...
            self.ledgers[ledger_name("base", cap)] = self._ledger(ledger_name("base", cap), cap,
                                                                  float(s.portfolio_fee_base))
            self.ledgers[ledger_name("real", cap)] = self._ledger(ledger_name("real", cap), cap,
                                                                  float(s.portfolio_fee_base + s.portfolio_slippage))
        self.backfill_wanted = asyncio.Event()
        self._last_history = 0
        self._snap_turn = 0
        self._poll_turn = 0

    def _ledger(self, name: str, capital: int, fee: float) -> Ledger:
        s = self.s
        return Ledger(name, start_equity=float(capital), leverage=s.portfolio_leverage, fee=fee,
                      cap_pct=float(s.portfolio_cap_pct), min_order=float(s.portfolio_min_order_usd),
                      max_total_x=float(s.portfolio_max_total_x),
                      dd_pause_pct=float(s.portfolio_dd_pause_pct) if s.portfolio_dd_pause_pct else None,
                      dd_pause_days=float(s.portfolio_dd_pause_days), maxlev=self.maxlev)

    def maxlev(self, coin: str) -> float:
        return self.maxlev_map.get(coin, 10.0)

    @property
    def addresses(self) -> list[str]:
        return [w.address for w in self.wallets]

    # -- persistence ----------------------------------------------------------------------------------------

    def restore(self) -> bool:
        saved = self.store.get_state(STATE_KEY)
        if not saved:
            return False
        for name, ledger in self.ledgers.items():
            if name in saved.get("ledgers", {}):
                ledger.load(saved["ledgers"][name])
        self.last_seen = {k: int(v) for k, v in saved.get("last_seen", {}).items()}
        self.started_ms = int(saved.get("started_ms", 0))
        self.wallet_equity = {k: (float(v[0]), int(v[1])) for k, v in saved.get("wallet_equity", {}).items()}
        # A wallet taken out of PORTFOLIO_WALLETS while we still hold its trades (spec §8): followed for its exits
        # only -- weight 0 means any new trade of it gets a zero cap and never trades.
        held = {split_key(k)[0] for x in self.ledgers.values() for k in (*x.P, *x.T)}
        for old in saved.get("wallets", []):
            if old["label"] in held and old["address"] not in self.by_address \
                    and old["label"] not in {w.label for w in self.wallets}:
                w = Wallet(old["label"], old["address"], 0.0)
                self.wallets.append(w)
                self.by_address[w.address] = w
                self.n.warning("PF_WALLET_EXITS_ONLY", wallet=w.label, note="removed from PORTFOLIO_WALLETS: "
                               "its open trades are followed to their exits, no new trades")
        return True

    def save(self) -> None:
        self.store.set_state(STATE_KEY, {
            "ledgers": {name: x.to_dict() for name, x in self.ledgers.items()}, "last_seen": self.last_seen,
            "started_ms": self.started_ms, "wallet_equity": self.wallet_equity,
            "wallets": [{"label": w.label, "address": w.address, "weight": w.weight} for w in self.wallets]})
        for ledger in self.ledgers.values():
            if ledger.closed:
                self.store.pf_insert_trades(ledger.closed)
                ledger.closed = []
            if ledger.orders:
                self.log_orders(ledger)
                self.store.pf_insert_orders(ledger.orders)
                ledger.orders = []

    # -- start-up -------------------------------------------------------------------------------------------

    async def load_markets(self) -> None:
        """maxLeverage of every perp (main + every HIP-3 dex): the liquidation rule needs it."""
        try:
            dexs = await self.client.info({"type": "perpDexs"}, 20)
            self.dexes = [""] + [d["name"] for d in dexs or [] if d and d.get("name")]
        except Exception as exc:  # noqa: BLE001
            self.n.warning("PF_MARKETS_FAILED", error=str(exc))
        for dex in self.dexes:
            try:
                body: dict[str, Any] = {"type": "meta"} | ({"dex": dex} if dex else {})
                meta = await self.client.info(body, 20)
                for u in meta.get("universe", []):
                    self.maxlev_map[u["name"]] = float(u.get("maxLeverage") or 10)
            except Exception as exc:  # noqa: BLE001 - 10x is assumed for that dex
                self.n.warning("PF_META_FAILED", dex=dex or "main", error=str(exc))

    async def start(self) -> None:
        restored = self.restore()
        now = self.clock()
        if not restored or not self.started_ms:
            self.started_ms = now
            for ledger in self.ledgers.values():
                ledger.started_ms = now
        for w in self.wallets:
            self.last_seen.setdefault(w.address, now)  # a wallet added later starts from now
        await self.load_markets()
        for w in self.wallets:
            await self.refresh_equity(w)
        self.n.info("PF_STARTED", restored=restored, wallets={w.label: w.weight for w in self.wallets},
                    dexes=self.dexes, started_ms=self.started_ms,
                    equity={n: round(x.equity(), 2) for n, x in self.ledgers.items()})
        await self.backfill_all()
        self.save()

    # -- fills ----------------------------------------------------------------------------------------------

    async def on_raw_fills(self, address: str, raws: list[dict[str, Any]], is_history: bool) -> None:
        received = self.clock()
        fills = [f for f in (parse_any_perp_fill(r, received, hip3=self.s.portfolio_copy_hip3) for r in raws)
                 if f is not None]
        await self.on_fills(address.lower(), fills)

    async def on_fills(self, address: str, fills: list[LeaderFill]) -> None:
        w = self.by_address.get(address)
        if w is None or not fills:
            return
        async with self.lock:
            since = self.last_seen.get(address, self.started_ms)
            new = [f for f in fills if f.time_ms >= since - 60_000 and self.store.pf_mark_seen(w.label, f)]
            new = [f for f in new if f.time_ms >= self.started_ms]
            if not new:
                return
            for f in aggregate_burst(chain_order(new)):
                self.process(w, f)
            self.last_seen[address] = max(self.last_seen.get(address, 0), *(f.time_ms for f in new))
            self.save()

    def log_orders(self, ledger: Ledger) -> None:
        """Every simulated order of the smallest real account also goes to the bot log (dashboard «السجلات»):
        which trader, coin, what we did, size, price and the fee taken off it."""
        if ledger.name != ledger_name("real", self.capitals[0]):
            return
        for o in ledger.orders:
            self.n.info("PF_ORDER", account=ledger.name, wallet=o["wallet"], coin=o["coin"], kind=o["kind"],
                        side="BUY" if o["qty"] > 0 else "SELL", qty=round(abs(o["qty"]), 8), px=o["px"],
                        notional=round(o["notional"], 2), fee=round(o["fee"], 4),
                        equity=round(ledger.equity(), 2))

    def our_price(self, f: LeaderFill) -> tuple[float, bool]:
        """(the price the real ledger gets, whether it is the live mid)."""
        mid = self.mids.get(f.coin)
        if mid is not None and f.received_at_ms - mid[1] <= MID_MAX_AGE_MS:
            return mid[0], True
        return float(f.px), False

    def process(self, w: Wallet, f: LeaderFill) -> dict[str, Outcome]:
        """One wallet fill through every ledger: base ones at its price, real ones at the live mid (and no new
        trade when the fill is stale). The event row records what the smallest account did."""
        lag = f.received_at_ms - f.time_ms
        stale = lag > self.s.portfolio_stale_seconds * 1000
        weq = self.wallet_equity.get(w.address)
        wallet_eq = weq[0] if weq and self.clock() - weq[1] <= WALLET_EQUITY_MAX_AGE_MS else 0.0
        px = float(f.px)
        real_px, live = self.our_price(f)
        s0, d = float(f.start_position), float(f.signed_sz)
        out: dict[str, Outcome] = {}
        for name, ledger in self.ledgers.items():
            if name.startswith("base:"):
                out[name] = ledger.on_fill(w.label, w.weight, f.coin, s0, d, px, f.time_ms, wallet_eq)
            else:
                out[name] = ledger.on_fill(w.label, w.weight, f.coin, s0, d, real_px, f.time_ms, wallet_eq,
                                           allow_new=not stale)
        first = self.capitals[0]
        out["base"], out["real"] = out[ledger_name("base", first)], out[ledger_name("real", first)]
        self.last_fill_at[f"{w.label}|{f.coin}"] = self.clock()
        self.store.pf_insert_event({
            "wallet": w.label, "tid": f.tid, "coin": f.coin, "side": f.side, "px": px, "sz": float(f.sz),
            "start_pos": s0, "leader_time_ms": f.time_ms, "detected_at_ms": f.received_at_ms,
            "real_px": real_px if live else None, "base_action": out["base"].action, "base_qty": out["base"].qty,
            "real_action": out["real"].action, "real_qty": out["real"].qty})
        if out["real"].action in ("OPEN", "CLOSE", "FLIP") or out["base"].action in ("OPEN", "CLOSE", "FLIP"):
            self.n.info("PF_FILL", wallet=w.label, coin=f.coin, base=out["base"].action, real=out["real"].action,
                        px=px, real_px=real_px, lag_ms=lag, wallet_equity=round(wallet_eq),
                        equity_real=round(self.ledgers[ledger_name("real", first)].equity(), 2))
        if stale and out["real"].action == "SKIP_STALE":
            self.n.warning("PF_STALE_FILL", wallet=w.label, coin=f.coin, lag_s=lag // 1000)
        return out

    async def backfill_wallet(self, w: Wallet) -> None:
        since = self.last_seen.get(w.address, self.started_ms)
        raw = await self.client.info({"type": "userFillsByTime", "user": w.address, "startTime": since + 1,
                                      "aggregateByTime": False}, 20)
        if raw:
            await self.on_raw_fills(w.address, list(raw), False)

    async def backfill_all(self) -> None:
        for w in self.wallets:
            try:
                await self.backfill_wallet(w)
            except Exception as exc:  # noqa: BLE001 - the poll loop retries
                self.n.warning("PF_BACKFILL_FAILED", wallet=w.label, error=str(exc))

    # -- prices ---------------------------------------------------------------------------------------------

    def on_mids(self, data: dict[str, Any]) -> None:
        """A WS `allMids` frame (main dex or one HIP-3 dex)."""
        now = self.clock()
        for coin, v in (data.get("mids") or {}).items():
            if coin and coin[0] not in "@#":
                try:
                    self.mids[coin] = (float(v), now)
                except (TypeError, ValueError):
                    continue

    def fresh_prices(self) -> dict[str, float]:
        now = self.clock()
        return {c: px for c, (px, at) in self.mids.items() if now - at <= MID_MAX_AGE_MS * 4}

    async def rest_mids(self, coins: set[str]) -> None:
        """Mids by REST for held coins the WS has not priced lately (WS down, a dex not subscribed)."""
        for dex in sorted({dex_of(c) for c in coins}):
            try:
                body: dict[str, Any] = {"type": "allMids"} | ({"dex": dex} if dex else {})
                self.on_mids({"mids": await self.client.info(body, 2)})
            except Exception as exc:  # noqa: BLE001
                self.n.warning("PF_PRICES_FAILED", dex=dex or "main", error=str(exc))

    def held_coins(self) -> set[str]:
        return {split_key(k)[1] for x in self.ledgers.values() for k in (*x.P, *x.T)}

    # -- loops ----------------------------------------------------------------------------------------------

    async def mark_loop(self) -> None:
        while True:
            await asyncio.sleep(MARK_SECONDS)
            try:
                await self.mark_once()
            except Exception as exc:  # noqa: BLE001 - never stop marking
                self.n.warning("PF_MARK_FAILED", error=f"{type(exc).__name__}: {exc}")

    async def mark_once(self) -> None:
        held = self.held_coins()
        prices = self.fresh_prices()
        if held - set(prices):
            await self.rest_mids(held - set(prices))
            prices = self.fresh_prices()
        now = self.clock()
        async with self.lock:
            for name, ledger in self.ledgers.items():
                was_paused = ledger.paused(now)
                liquidated = ledger.mark({c: prices[c] for c in held if c in prices}, now)
                if liquidated:
                    self.n.critical("PF_LIQUIDATED", ledger=name, positions=liquidated,
                                    equity=round(ledger.equity(), 2))
                if ledger.paused(now) and not was_paused:
                    self.n.warning("PF_DD_PAUSE", ledger=name, equity=round(ledger.equity(), 2),
                                   until=datetime.fromtimestamp(ledger.paused_until / 1000, UTC).isoformat())
            self.save()
            self.write_runtime()
        if now - self._last_history >= EQUITY_HISTORY_SECONDS * 1000:
            self._last_history = now
            for name, ledger in self.ledgers.items():
                self.store.record_equity(f"portfolio:{name}", now, Decimal(str(round(ledger.equity(), 4))))

    async def refresh_equity(self, w: Wallet) -> None:
        try:
            value = await self.client.portfolio_equity(w.address)
        except Exception as exc:  # noqa: BLE001
            self.n.warning("PF_WALLET_EQUITY_FAILED", wallet=w.label, error=str(exc))
            return
        if value is not None and value > 0:
            self.wallet_equity[w.address] = (float(value), self.clock())

    async def equity_loop(self) -> None:
        refresh_ms = max(self.s.leader_equity_refresh_seconds - 60, 30) * 1000
        while True:
            await asyncio.sleep(60)
            for w in self.wallets:
                if self.clock() - self.wallet_equity.get(w.address, (0.0, 0))[1] >= refresh_ms:
                    await self.refresh_equity(w)

    async def poll_loop(self) -> None:
        """REST gap filler: one wallet every PORTFOLIO_POLL_SECONDS / n; at once after a WS reconnect."""
        step = max(self.s.portfolio_poll_seconds / max(len(self.wallets), 1), 2)
        while True:
            try:
                await asyncio.wait_for(self.backfill_wanted.wait(), timeout=step)
                self.backfill_wanted.clear()
                await self.backfill_all()
                continue
            except TimeoutError:
                pass
            w = self.wallets[self._poll_turn % len(self.wallets)]
            self._poll_turn += 1
            try:
                await self.backfill_wallet(w)
            except Exception as exc:  # noqa: BLE001 - the WS is still there
                self.n.info("PF_POLL_FAILED", wallet=w.label, error=str(exc))

    async def snapshot_loop(self) -> None:
        step = SNAPSHOT_SECONDS / max(len(self.wallets), 1)
        while True:
            await asyncio.sleep(step)
            w = self.wallets[self._snap_turn % len(self.wallets)]
            self._snap_turn += 1
            try:
                await self.check_missed_exits(w)
            except Exception as exc:  # noqa: BLE001
                self.n.info("PF_SNAPSHOT_FAILED", wallet=w.label, error=str(exc))

    async def check_missed_exits(self, w: Wallet) -> None:
        """Our share of a trade is closed only when two snapshots in a row, both taken after the last fill we
        processed for that coin, show the wallet flat (C-4 spirit: never act on one snapshot)."""
        mine = {split_key(k)[1] for x in self.ledgers.values() for k in (*x.P, *x.T) if split_key(k)[0] == w.label}
        if not mine:
            return
        asked = self.clock()
        positions: dict[str, float] = {}
        for dex in sorted({dex_of(c) for c in mine}):
            body: dict[str, Any] = {"type": "clearinghouseState", "user": w.address} | ({"dex": dex} if dex else {})
            raw = await self.client.info(body, 2)
            for ap in (raw or {}).get("assetPositions", []) or []:
                p = ap.get("position") or {}
                coin = str(p.get("coin", ""))
                if dex and ":" not in coin:
                    coin = f"{dex}:{coin}"
                positions[coin] = float(p.get("szi") or 0)
        async with self.lock:
            for coin in mine:
                key = f"{w.label}|{coin}"
                if abs(positions.get(coin, 0.0)) > 0 or self.last_fill_at.get(key, 0) >= asked - 2_000:
                    self.flat_seen.pop(key, None)
                    continue
                self.flat_seen[key] = self.flat_seen.get(key, 0) + 1
                if self.flat_seen[key] < 2:
                    continue
                self.flat_seen.pop(key, None)
                mid = self.mids.get(coin)
                now = self.clock()
                closed = []
                for name, ledger in self.ledgers.items():
                    k = f"{w.label}|{coin}"
                    px = mid[0] if mid else (ledger.M.get(coin) or (ledger.P[k]["a"] if k in ledger.P else 0.0))
                    if px > 0 and ledger.close_now(w.label, coin, px, now, "RECONCILED"):
                        closed.append(name)
                if closed:
                    self.n.critical("PF_MISSED_EXIT_CLOSED", wallet=w.label, coin=coin, ledgers=closed)
            self.save()

    async def daily_loop(self) -> None:
        """Once per UTC day: one line to the log, the dashboard and Telegram."""
        while True:
            await asyncio.sleep(60)
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            last = self.store.get_state("portfolio_daily_date")
            if last == today:
                continue
            self.store.set_state("portfolio_daily_date", today)
            self.store.pf_prune_seen(self.clock() - 3 * DAY_MS)
            if last is None:
                continue
            self.n.report("PF_DAILY", self.daily_line(), wallets=[w.label for w in self.wallets])

    def daily_line(self) -> str:
        now = self.clock()
        days = (now - self.started_ms) / DAY_MS
        parts = [f"Trio sim (Hyperliquid) day {days:.0f}"]
        for cap in self.capitals:
            b, r = self.ledgers[ledger_name("base", cap)], self.ledgers[ledger_name("real", cap)]
            parts.append(f"${cap:,}: real ${r.equity():,.0f} ({(r.equity() / r.start_equity - 1) * 100:+.2f}%, "
                         f"max DD {r.mdd * 100:.1f}%) base ${b.equity():,.0f} "
                         f"({(b.equity() / b.start_equity - 1) * 100:+.2f}%)")
        r0 = self.ledgers[ledger_name("real", self.capitals[0])]
        trades = self.store.pf_trades_since(now - DAY_MS, ledger=r0.name)
        parts.append(f"closed today {len(trades)} | open {len(r0.P)}")
        return " | ".join(parts)

    def write_runtime(self) -> None:
        now = self.clock()
        self.store.set_state(RUNTIME_KEY, {
            "updated_ms": now, "started_ms": self.started_ms, "enabled": True, "capitals": self.capitals,
            "wallets": [{"label": w.label, "address": w.address, "weight": w.weight,
                         "equity": self.wallet_equity.get(w.address, (None, None))[0],
                         "equity_at_ms": self.wallet_equity.get(w.address, (None, None))[1],
                         "last_fill_ms": self.last_seen.get(w.address)} for w in self.wallets],
            "settings": {"leverage": self.s.portfolio_leverage, "k": 0.8 * self.s.portfolio_leverage,
                         "cap_pct": float(self.s.portfolio_cap_pct), "fee_base": float(self.s.portfolio_fee_base),
                         "fee_real": float(self.s.portfolio_fee_base + self.s.portfolio_slippage),
                         "min_order": float(self.s.portfolio_min_order_usd),
                         "max_total_x": float(self.s.portfolio_max_total_x),
                         "dd_pause_pct": float(self.s.portfolio_dd_pause_pct or 0),
                         "dd_pause_days": float(self.s.portfolio_dd_pause_days),
                         "copy_hip3": self.s.portfolio_copy_hip3, "stale_seconds": self.s.portfolio_stale_seconds},
            "prices": {"coins": len(self.mids), "dexes": self.dexes},
            "ledgers": {name: x.summary(now) | {"positions": x.positions()} for name, x in self.ledgers.items()}})

    def tasks(self) -> list[asyncio.Task[None]]:
        return [asyncio.create_task(c) for c in (self.mark_loop(), self.equity_loop(), self.poll_loop(),
                                                 self.snapshot_loop(), self.daily_loop())]
