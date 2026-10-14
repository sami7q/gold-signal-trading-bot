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

Groups (botup.md §V10): next to the trio, PORTFOLIO_GROUPS adds more sections -- e.g. 8bae + 8fc7 + 69b0 + 2ac4 --
each with its own ledgers "<group>:<scenario>:<capital>" (the trio keeps its v2 names) fed by the SAME wallet fills.
A group copies only trades that start after the group itself started (the ledger's WAIT rule does the rest), so a
group added today never inherits a trade the trio already holds.
"""

from __future__ import annotations

import asyncio
import json
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
#: botup.md §V11 (real-time): marks every second on the live `bbo` prices (in memory); the dashboard view is
#: written every RUNTIME_SECONDS and the full state every STATE_SECONDS (and after every fill, as before)
MARK_SECONDS = 1.0
RUNTIME_SECONDS = 2.0
STATE_SECONDS = 15.0
REST_MIDS_SECONDS = 10.0
EQUITY_HISTORY_SECONDS = 300
SNAPSHOT_SECONDS = 60
#: a mid older than this is not used as our price (the wallet's own price is, and the fill is flagged)
MID_MAX_AGE_MS = 15_000
#: best bid / ask younger than this is our price in the real scenario (buy at the ask, sell at the bid)
BBO_MAX_AGE_MS = 3_000
#: always priced live (bbo), next to the coins we hold and the coins our wallets traded lately
HOT_COINS = ("BTC", "ETH", "SOL", "HYPE")
HOT_DAYS = 30
#: the wallet's equity W may be this old before a new trade of that wallet is skipped
WALLET_EQUITY_MAX_AGE_MS = 6 * 3_600_000
SCENARIOS = ("base", "real")
TRIO = "trio"
GROUP_TITLES = {TRIO: "الثلاثي"}


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
                      received_at_ms=int(raw.get("_recv") or received_at_ms))


@dataclass(frozen=True, slots=True)
class Group:
    name: str
    labels: tuple[str, ...]
    research_year: float | None = None  # realistic research year multiple (hunt2)
    research_half: float | None = None  # Monte Carlo median with half the traders' edge

    @property
    def title(self) -> str:
        return GROUP_TITLES.get(self.name) or " + ".join(self.labels)


def parse_groups(raw: str) -> list[Group]:
    """`PORTFOLIO_GROUPS=mix2ac4=8bae+8fc7+69b0+2ac4:29.01:5.79;...` -- name=labels[:year[:half]]."""
    out = []
    for part in (p.strip() for p in raw.split(";") if p.strip()):
        name, eq, rest = part.partition("=")
        name = name.strip().lower()
        bits = rest.split(":")
        labels = tuple(x.strip().lower() for x in bits[0].split("+") if x.strip())
        if not eq or not re_group_name(name) or name == TRIO or not labels or len(set(labels)) != len(labels):
            raise ValueError(f"PORTFOLIO_GROUPS: {part!r} must be name=label+label[:year:half] (name a-z0-9, not trio)")
        nums = [float(x) for x in bits[1:3] if x.strip()]
        out.append(Group(name, labels, nums[0] if nums else None, nums[1] if len(nums) > 1 else None))
    if len({g.name for g in out}) != len(out):
        raise ValueError("PORTFOLIO_GROUPS: names must be unique")
    return out


def re_group_name(name: str) -> bool:
    return 2 <= len(name) <= 16 and name.isascii() and name.isalnum() and name == name.lower()


def parse_capitals(raw: str) -> list[int]:
    """`PORTFOLIO_START_EQUITIES=10000,50000,100000` -- one pair of ledgers (base / real) per starting balance."""
    out = sorted({int(float(x)) for x in raw.split(",") if x.strip()})
    if not out or any(c <= 0 for c in out):
        raise ValueError("PORTFOLIO_START_EQUITIES: one or more positive amounts, e.g. 10000,50000,100000")
    return out


def ledger_name(scenario: str, capital: int, group: str = TRIO) -> str:
    """The trio keeps its v2 names ("real:10000"); a group's are "<group>:real:10000"."""
    return f"{scenario}:{capital}" if group == TRIO else f"{group}:{scenario}:{capital}"


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
        trio = parse_wallets(s.portfolio_wallets)
        extra = parse_wallets(s.portfolio_extra_wallets) if s.portfolio_extra_wallets.strip() else []
        groups = parse_groups(s.portfolio_groups)
        known = {w.label: w for w in trio}
        for w in extra:
            if w.label in known or w.address in {x.address for x in known.values()}:
                raise ValueError(f"PORTFOLIO_EXTRA_WALLETS: {w.label} is already a simulator wallet")
            known[w.label] = w
        for g in groups:
            missing = [x for x in g.labels if x not in known]
            if missing:
                raise ValueError(f"PORTFOLIO_GROUPS: {g.name} uses {missing} -- not in PORTFOLIO_WALLETS / "
                                 "PORTFOLIO_EXTRA_WALLETS")
        ty = [float(x) for x in s.portfolio_trio_research.split(":") if x.strip()]
        self.groups: list[Group] = [Group(TRIO, tuple(w.label for w in trio), ty[0] if ty else None,
                                          ty[1] if len(ty) > 1 else None), *groups]
        used = {x for g in self.groups for x in g.labels}
        self.wallets = [w for w in known.values() if w.label in used]
        self.by_address = {w.address: w for w in self.wallets}
        self.group_started: dict[str, int] = {}
        #: a wallet removed from the config while a group still holds its trades -> those groups (exits only)
        self.exits_only: dict[str, set[str]] = {}
        self.maxlev_map: dict[str, float] = {}
        self.mids: dict[str, tuple[float, int]] = {}  # coin -> (mid, when)
        self.bbo: dict[str, tuple[float, float, int]] = {}  # coin -> (bid, ask, when) -- the live book top
        self.traded: dict[str, int] = {}  # coin -> last time one of our wallets traded it (bbo watch list)
        self._last_runtime = 0
        self._last_state = 0
        self._last_rest_mids = 0
        self.dexes: list[str] = [""]  # "" = main dex; HIP-3 dex names added from perpDexs
        self.wallet_equity: dict[str, tuple[float, int]] = {}  # address -> (value, when)
        self.last_seen: dict[str, int] = {}  # address -> newest fill time processed
        self.flat_seen: dict[str, int] = {}  # wallet|coin -> snapshots in a row showing the wallet flat
        self.last_fill_at: dict[str, int] = {}  # wallet|coin -> when we last processed a fill of it
        self.lock = asyncio.Lock()
        self.started_ms = 0
        self.capitals = parse_capitals(s.portfolio_start_equities)
        self.ledgers: dict[str, Ledger] = {}
        self.ledger_group: dict[str, Group] = {}
        for g in self.groups:
            for cap in self.capitals:  # "base:10000", "real:10000", ..., "mix2ac4:base:10000", ...
                for sc, fee in (("base", s.portfolio_fee_base), ("real", s.portfolio_fee_base + s.portfolio_slippage)):
                    name = ledger_name(sc, cap, g.name)
                    self.ledgers[name] = self._ledger(name, cap, float(fee))
                    self.ledger_group[name] = g
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

    def groups_of(self, label: str) -> list[Group]:
        extra = self.exits_only.get(label, set())
        return [g for g in self.groups if label in g.labels or g.name in extra]

    def wallet_since(self, label: str) -> int:
        """The oldest fill time any group of this wallet still copies (a group starts with its own start)."""
        starts = [self.group_started.get(g.name, self.started_ms) for g in self.groups_of(label)]
        return min(starts) if starts else self.started_ms

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
        self.group_started = {k: int(v) for k, v in saved.get("group_started", {}).items()}
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
            for name, x in self.ledgers.items():  # also a wallet that left one group but stays in another
                if old["label"] not in self.ledger_group[name].labels and \
                        any(split_key(k)[0] == old["label"] for k in (*x.P, *x.T)):
                    self.exits_only.setdefault(old["label"], set()).add(self.ledger_group[name].name)
        return True

    def save(self) -> None:
        self.store.set_state(STATE_KEY, {
            "ledgers": {name: x.to_dict() for name, x in self.ledgers.items()}, "last_seen": self.last_seen,
            "started_ms": self.started_ms, "group_started": self.group_started, "wallet_equity": self.wallet_equity,
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
        self.group_started.setdefault(TRIO, self.started_ms)
        for g in self.groups:  # a group added later starts now: only trades opened from now on
            self.group_started.setdefault(g.name, now)
        for name, ledger in self.ledgers.items():
            if not ledger.started_ms:
                ledger.started_ms = self.group_started[self.ledger_group[name].name]
        for w in self.wallets:
            self.last_seen.setdefault(w.address, now)  # a wallet added later starts from now
        await self.load_markets()
        for e in self.store.pf_events_since(now - HOT_DAYS * DAY_MS, limit=5000):  # the bbo watch list
            self.traded[e["coin"]] = max(self.traded.get(e["coin"], 0), int(e["leader_time_ms"]))
        for w in self.wallets:
            await self.refresh_equity(w)
        self.n.info("PF_STARTED", restored=restored, wallets={w.label: w.weight for w in self.wallets},
                    groups={g.name: "+".join(g.labels) for g in self.groups}, group_started=self.group_started,
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
            new = [f for f in new if f.time_ms >= self.wallet_since(w.label)]
            if not new:
                return
            for f in aggregate_burst(chain_order(new)):
                self.process(w, f)
            self.last_seen[address] = max(self.last_seen.get(address, 0), *(f.time_ms for f in new))
            self.save()
            self.write_runtime()  # the dashboard sees the new order at once, not at the next mark
            self._last_runtime = self.clock()

    def log_orders(self, ledger: Ledger) -> None:
        """Every simulated order of each section's smallest real account also goes to the bot log (dashboard
        «السجلات»): which section, trader, coin, what we did, size, price and the fee taken off it."""
        g = self.ledger_group[ledger.name]
        if ledger.name != ledger_name("real", self.capitals[0], g.name):
            return
        for o in ledger.orders:
            self.n.info("PF_ORDER", account=ledger.name, group=g.name, wallet=o["wallet"], coin=o["coin"],
                        kind=o["kind"],
                        side="BUY" if o["qty"] > 0 else "SELL", qty=round(abs(o["qty"]), 8), px=o["px"],
                        notional=round(o["notional"], 2), fee=round(o["fee"], 4),
                        equity=round(ledger.equity(), 2))

    def our_price(self, f: LeaderFill) -> tuple[float, bool]:
        """(the price the real ledger gets, whether it is live): the book top when we have it fresh -- a buy pays
        the ask, a sell gets the bid, as a market order would -- else the mid, else the wallet's own price."""
        book = self.bbo.get(f.coin)
        if book is not None and abs(f.received_at_ms - book[2]) <= BBO_MAX_AGE_MS:
            px = book[1] if f.side == "B" else book[0]
            if px > 0:
                return px, True
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
        mine = [g for g in self.groups_of(w.label) if f.time_ms >= self.group_started.get(g.name, self.started_ms)]
        if not mine:
            return out
        names = {g.name for g in mine}
        for name, ledger in self.ledgers.items():
            if self.ledger_group[name].name not in names:
                continue
            if name.startswith("base:") or ":base:" in name:
                out[name] = ledger.on_fill(w.label, w.weight, f.coin, s0, d, px, f.time_ms, wallet_eq)
            else:
                out[name] = ledger.on_fill(w.label, w.weight, f.coin, s0, d, real_px, f.time_ms, wallet_eq,
                                           allow_new=not stale)
        first = self.capitals[0]
        actions = {}
        for g in mine:
            b, r = out[ledger_name("base", first, g.name)], out[ledger_name("real", first, g.name)]
            actions[g.name] = [b.action, b.qty, r.action, r.qty]
        lead = mine[0]  # the trio when the wallet is one of the trio: the event's own columns stay the trio's
        out["base"] = out[ledger_name("base", first, lead.name)]
        out["real"] = out[ledger_name("real", first, lead.name)]
        lead_real = self.ledgers[ledger_name("real", first, lead.name)]
        self.last_fill_at[f"{w.label}|{f.coin}"] = self.clock()
        self.traded[f.coin] = max(self.traded.get(f.coin, 0), f.time_ms)
        self.store.pf_insert_event({
            "wallet": w.label, "tid": f.tid, "coin": f.coin, "side": f.side, "px": px, "sz": float(f.sz),
            "start_pos": s0, "leader_time_ms": f.time_ms, "detected_at_ms": f.received_at_ms,
            "real_px": real_px if live else None, "base_action": out["base"].action, "base_qty": out["base"].qty,
            "real_action": out["real"].action, "real_qty": out["real"].qty, "actions": json.dumps(actions)})
        if out["real"].action in ("OPEN", "CLOSE", "FLIP") or out["base"].action in ("OPEN", "CLOSE", "FLIP"):
            self.n.info("PF_FILL", wallet=w.label, coin=f.coin, base=out["base"].action, real=out["real"].action,
                        px=px, real_px=real_px, lag_ms=lag, wallet_equity=round(wallet_eq),
                        group=lead.name, equity_real=round(lead_real.equity(), 2))
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

    def on_bbo(self, data: dict[str, Any]) -> None:
        """A WS `bbo` frame: {coin, time, bbo: [bid level | null, ask level | null]}. Its mid also marks the coin."""
        coin = str(data.get("coin") or "")
        levels = data.get("bbo") or []
        try:
            bid = float(levels[0]["px"]) if len(levels) > 0 and levels[0] else 0.0
            ask = float(levels[1]["px"]) if len(levels) > 1 and levels[1] else 0.0
        except (KeyError, TypeError, ValueError):
            return
        if not coin or bid <= 0 or ask <= 0:
            return
        now = self.clock()
        self.bbo[coin] = (bid, ask, now)
        self.mids[coin] = ((bid + ask) / 2, now)

    def bbo_coins(self) -> set[str]:
        """What the feed keeps a live book top for: held coins, coins our wallets traded in HOT_DAYS, HOT_COINS
        (the main dex ones only when HIP-3 copying is off)."""
        since = self.clock() - HOT_DAYS * DAY_MS
        coins = self.held_coins() | {c for c, t in self.traded.items() if t >= since} | set(HOT_COINS)
        return {c for c in coins if self.s.portfolio_copy_hip3 or ":" not in c}

    def on_mids(self, data: dict[str, Any]) -> None:
        """A WS `allMids` frame (main dex or one HIP-3 dex) -- every ~5 s; a fresher bbo mid is kept."""
        now = self.clock()
        for coin, v in (data.get("mids") or {}).items():
            if coin and coin[0] not in "@#" and now - self.bbo.get(coin, (0.0, 0.0, 0))[2] > BBO_MAX_AGE_MS:
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
                await self.mark_once(force=False)
            except Exception as exc:  # noqa: BLE001 - never stop marking
                self.n.warning("PF_MARK_FAILED", error=f"{type(exc).__name__}: {exc}")

    async def mark_once(self, *, force: bool = True) -> None:
        """Marks every ledger on the freshest prices (liquidation and drawdown pause included). The loop calls it
        every second with force=False: the dashboard view is then written every RUNTIME_SECONDS and the state every
        STATE_SECONDS (a liquidation or a new pause is saved at once); force=True (tests, start-up) writes both."""
        held = self.held_coins()
        prices = self.fresh_prices()
        now = self.clock()
        if held - set(prices) and (force or now - self._last_rest_mids >= REST_MIDS_SECONDS * 1000):
            self._last_rest_mids = now
            await self.rest_mids(held - set(prices))
            prices = self.fresh_prices()
        now = self.clock()
        async with self.lock:
            urgent = False
            for name, ledger in self.ledgers.items():
                was_paused = ledger.paused(now)
                liquidated = ledger.mark({c: prices[c] for c in held if c in prices}, now)
                if liquidated:
                    urgent = True
                    self.n.critical("PF_LIQUIDATED", ledger=name, positions=liquidated,
                                    equity=round(ledger.equity(), 2))
                if ledger.paused(now) and not was_paused:
                    urgent = True
                    self.n.warning("PF_DD_PAUSE", ledger=name, equity=round(ledger.equity(), 2),
                                   until=datetime.fromtimestamp(ledger.paused_until / 1000, UTC).isoformat())
            if force or urgent or now - self._last_state >= STATE_SECONDS * 1000:
                self._last_state = now
                self.save()
            if force or urgent or now - self._last_runtime >= RUNTIME_SECONDS * 1000:
                self._last_runtime = now
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
        lines = []
        for g in self.groups:
            days = (now - self.group_started.get(g.name, self.started_ms)) / DAY_MS
            parts = [f"{'Trio' if g.name == TRIO else g.name} sim ({'+'.join(g.labels)}) day {days:.0f}"]
            for cap in self.capitals:
                b, r = self.ledgers[ledger_name("base", cap, g.name)], self.ledgers[ledger_name("real", cap, g.name)]
                parts.append(f"${cap:,}: real ${r.equity():,.0f} ({(r.equity() / r.start_equity - 1) * 100:+.2f}%, "
                             f"max DD {r.mdd * 100:.1f}%) base ${b.equity():,.0f} "
                             f"({(b.equity() / b.start_equity - 1) * 100:+.2f}%)")
            r0 = self.ledgers[ledger_name("real", self.capitals[0], g.name)]
            trades = self.store.pf_trades_since(now - DAY_MS, ledger=r0.name)
            parts.append(f"closed today {len(trades)} | open {len(r0.P)}")
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    def write_runtime(self) -> None:
        now = self.clock()
        self.store.set_state(RUNTIME_KEY, {
            "updated_ms": now, "started_ms": self.started_ms, "enabled": True, "capitals": self.capitals,
            "groups": [{"name": g.name, "title": g.title, "labels": list(g.labels),
                        "started_ms": self.group_started.get(g.name, self.started_ms),
                        "research_year": g.research_year, "research_half": g.research_half} for g in self.groups],
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
