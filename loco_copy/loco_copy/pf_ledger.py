"""The «new trio» portfolio ledger (botup.md §V6 = Crypto project `portfolio-strategy-v1.md`): several Hyperliquid
wallets copied into ONE virtual cross-margin account, each wallet with a weight w_i of that account.

Pure -- no I/O, no clock: the live simulator (`portfolio.py`) and the tests feed it fills and prices. It is a line
-by-line port of the research simulator `portfolio_sim.psim` (the code that produced the spec's numbers), so the
live run can be compared with the backtest fill for fill:

    trade start (the wallet's position leaves 0):
        E = our equity, W_i = the wallet's equity, R = K x w_i x E / W_i, CAP = cap% x L x w_i x E (fixed)
    grow  (|position| up):   pending += |dq| x R, limited by the CAP room and by 6 x equity in total;
                             one order once pending x price >= $10
    shrink (|position| down, not 0): same fraction as the wallet; pending scaled too
    close (position 0):      100 %          flip: close + a new trade with the rest
    positions open before we started (first fill seen starts from != 0): ignored until the wallet is flat.

The account: cross margin, liquidation when equity < sum(notional / (2 x maxLeverage)) (Hyperliquid's rule, as in
the research); a drawdown of `dd_pause_pct` from the peak blocks new trades for `dd_pause_days` (exits continue).

Floats on purpose (not Decimal like the rest of the bot): same arithmetic as the research code it mirrors.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

DAY_MS = 86_400_000
FLAT_START = 1e-12
FLAT_END = 1e-9
DUST_NOTIONAL = 0.01


def key_of(wallet: str, coin: str) -> str:
    return f"{wallet}|{coin}"


def split_key(key: str) -> tuple[str, str]:
    wallet, _, coin = key.partition("|")
    return wallet, coin


@dataclass(slots=True)
class Outcome:
    """What one wallet fill did on this ledger (logged per fill, shown on the dashboard)."""

    action: str  # OPEN ADD REDUCE CLOSE FLIP PENDING CAPPED EXPOSURE WAIT IGNORED SKIP_PAUSED SKIP_STALE SKIP_NO_EQUITY
    qty: float = 0.0  # signed quantity we traded (0 = nothing)
    px: float = 0.0


class Ledger:
    def __init__(self, name: str, *, start_equity: float, leverage: int, fee: float, cap_pct: float = 20.0,
                 min_order: float = 10.0, max_total_x: float = 6.0, dd_pause_pct: float | None = 25.0,
                 dd_pause_days: float = 7.0, maxlev: Callable[[str], float] | None = None,
                 started_ms: int = 0) -> None:
        self.name = name
        self.start_equity = start_equity
        self.leverage = leverage
        self.k = 0.8 * leverage
        self.fee = fee
        self.cap_pct = cap_pct
        self.min_order = min_order
        self.max_total_x = max_total_x
        self.dd_pause = dd_pause_pct / 100 if dd_pause_pct else None
        self.dd_pause_days = dd_pause_days
        self.maxlev = maxlev or (lambda _coin: 10.0)
        self.started_ms = started_ms
        self.cash = start_equity
        self.P: dict[str, dict[str, float]] = {}  # (wallet|coin) -> {"q": signed qty, "a": average entry}
        self.T: dict[str, dict[str, Any]] = {}  # (wallet|coin) -> the trade in progress
        self.WAIT: dict[str, bool] = {}  # (wallet|coin) -> ignore until the wallet is flat
        self.M: dict[str, float] = {}  # coin -> last price
        self.peak = start_equity
        self.mdd = 0.0
        self.cb_peak = start_equity
        self.paused_until = 0
        self.pauses = 0
        self.liquidations = 0
        self.trades = 0
        self.wins = 0
        self.fees_paid = 0.0
        self.per_wallet: dict[str, dict[str, float]] = {}
        #: closed trades not yet written to the database (the simulator drains it)
        self.closed: list[dict[str, Any]] = []

    # -- account --------------------------------------------------------------------------------------------

    def mark_of(self, key: str) -> float:
        return self.M.get(split_key(key)[1], self.P[key]["a"])

    def equity(self) -> float:
        return self.cash + sum(p["q"] * (self.mark_of(k) - p["a"]) for k, p in self.P.items())

    def notional(self) -> float:
        return sum(abs(p["q"]) * self.mark_of(k) for k, p in self.P.items())

    def maintenance(self) -> float:
        return sum(abs(p["q"]) * self.mark_of(k) / (2 * self.maxlev(split_key(k)[1])) for k, p in self.P.items())

    def liquidation_distance(self) -> float | None:
        mm = self.maintenance()
        return self.equity() / mm if mm > 0 else None

    def paused(self, now_ms: int) -> bool:
        return now_ms < self.paused_until

    # -- fills ----------------------------------------------------------------------------------------------

    def _wallet(self, wallet: str) -> dict[str, float]:
        return self.per_wallet.setdefault(wallet, {"pnl": 0.0, "trades": 0, "wins": 0, "fees": 0.0})

    def _trade(self, k: str, dq: float, px: float) -> None:
        if dq == 0:
            return
        fe = abs(dq) * px * self.fee
        self.cash -= fe
        self.fees_paid += fe
        tr = self.T.get(k)
        if tr is not None:
            tr["pnl"] -= fe
            tr["fees"] += fe
        p = self.P.get(k)
        if p is None or p["q"] == 0 or (p["q"] > 0) == (dq > 0):
            if p is None:
                p = self.P[k] = {"q": 0.0, "a": px}
            p["a"] = (p["a"] * abs(p["q"]) + px * abs(dq)) / (abs(p["q"]) + abs(dq))
            p["q"] += dq
            if tr is not None:
                tr["max_ntl"] = max(tr["max_ntl"], abs(p["q"]) * px)
        else:
            q = min(abs(dq), abs(p["q"]))
            r = q * (px - p["a"]) * (1 if p["q"] > 0 else -1)
            self.cash += r
            if tr is not None:
                tr["pnl"] += r
            p["q"] += dq
            if abs(p["q"]) * px < DUST_NOTIONAL:
                del self.P[k]

    def _close_trade(self, k: str, px: float, t: int, reason: str) -> bool:
        """Closes our share 100 % and books the trade. True if we held something."""
        held = k in self.P
        if held:
            self._trade(k, -self.P[k]["q"], px)
        tr = self.T.pop(k, None)
        if tr is not None and tr["opened"]:
            wallet, coin = split_key(k)
            self.trades += 1
            self.wins += tr["pnl"] > 0
            w = self._wallet(wallet)
            w["pnl"] += tr["pnl"]
            w["trades"] += 1
            w["wins"] += tr["pnl"] > 0
            w["fees"] += tr["fees"]
            self.closed.append({"ledger": self.name, "wallet": wallet, "coin": coin, "side": tr["side"],
                                "opened_ms": tr["t0"], "closed_ms": t, "pnl": tr["pnl"], "fees": tr["fees"],
                                "max_notional": tr["max_ntl"], "cap": tr["cap"], "r": tr["R"], "adds": tr["adds"],
                                "capped": tr["capped"], "reason": reason, "exit_px": px})
        return held

    def on_fill(self, wallet: str, weight: float, coin: str, s0: float, d: float, px: float, t: int,
                wallet_equity: float, *, allow_new: bool = True) -> Outcome:
        """One fill of `wallet`: its position went from s0 to s0 + d at price px (our price on this ledger).
        allow_new=False (feed outage / a fill seen too late): no new trade and no add -- exits still follow."""
        k = key_of(wallet, coin)
        s1 = s0 + d
        self.M[coin] = px
        flat0, flat1 = abs(s0) < FLAT_START, abs(s1) < FLAT_END
        flip = not flat0 and not flat1 and (s0 > 0) != (s1 > 0)
        if k not in self.WAIT:
            self.WAIT[k] = not flat0
        if self.WAIT[k]:
            if flat1:
                self.WAIT[k] = False
            if not flip:
                return Outcome("WAIT")
            self.WAIT[k] = False
        if flat1:
            q = self.P[k]["q"] if k in self.P else 0.0
            had = k in self.T
            self._close_trade(k, px, t, "CLOSE")
            return Outcome("CLOSE" if had else "IGNORED", -q, px)
        base, dd = s0, d
        action = ""
        closed_q = 0.0
        if flip:
            closed_q = -self.P[k]["q"] if k in self.P else 0.0
            self._close_trade(k, px, t, "FLIP")
            base, dd = 0.0, s1
            action = "FLIP"
        if abs(base) < FLAT_START:
            e = self.equity()
            skip = ("SKIP_STALE" if not allow_new else "SKIP_NO_EQUITY" if wallet_equity <= 0 or e <= 0
                    else "SKIP_PAUSED" if t < self.paused_until else "")
            if skip:
                self.WAIT[k] = True
                return Outcome(skip, closed_q, px)
            cap = self.cap_pct / 100 * self.leverage * weight * e
            self.T[k] = {"R": self.k * weight * e / wallet_equity, "cap": cap,
                         "pend": 0.0, "pnl": 0.0, "fees": 0.0, "opened": False, "side": 1 if s1 > 0 else -1, "t0": t,
                         "adds": 0, "capped": 0, "max_ntl": 0.0, "w_eq": wallet_equity, "e0": e, "weight": weight}
            action = action or "OPEN"
        tr = self.T.get(k)
        if tr is None:
            return Outcome("IGNORED")
        if abs(s1) > abs(base):
            if not allow_new:
                return Outcome("SKIP_STALE")
            tr["pend"] += abs(dd) * tr["R"]
            p = self.P.get(k)
            cur = abs(p["q"]) * p["a"] if p else 0.0
            room_cap = max(0.0, tr["cap"] - cur) / px
            room_exp = max(0.0, self.max_total_x * self.equity() - self.notional()) / px
            limit = "CAPPED" if room_cap <= room_exp else "EXPOSURE"
            limited = tr["pend"] > min(room_cap, room_exp)
            tr["pend"] = min(tr["pend"], room_cap, room_exp)
            if limited and limit == "CAPPED":
                tr["capped"] += 1
            if tr["pend"] * px >= self.min_order:
                q = tr["pend"] * (1 if s1 > 0 else -1)
                was_open = tr["opened"]
                if was_open:
                    tr["adds"] += 1
                self._trade(k, q, px)
                tr["opened"] = True
                tr["pend"] = 0.0
                return Outcome("FLIP" if action == "FLIP" else "ADD" if was_open else "OPEN", q + closed_q, px)
            return Outcome("FLIP" if action == "FLIP" else limit if limited else "PENDING", closed_q, px)
        frac = abs(s1) / abs(base)
        p = self.P.get(k)
        q = 0.0
        if p:
            rq = abs(p["q"]) * (1 - frac)
            if rq * px >= self.min_order:
                q = -rq * (1 if p["q"] > 0 else -1)
                self._trade(k, q, px)
        tr["pend"] *= frac
        return Outcome(action or "REDUCE", q, px)

    def close_now(self, wallet: str, coin: str, px: float, t: int, reason: str) -> bool:
        """The wallet is flat but we missed its exit (reconciliation): our share is closed at px."""
        k = key_of(wallet, coin)
        if k not in self.P and k not in self.T:
            return False
        self.M[coin] = px
        self._close_trade(k, px, t, reason)
        self.WAIT[k] = False
        return True

    # -- marks ----------------------------------------------------------------------------------------------

    def mark(self, prices: dict[str, float], t: int) -> list[str] | None:
        """New prices (only coins we hold matter). Applies the cross liquidation and the drawdown pause.
        Returns the liquidated keys, or None."""
        worst, maint = self.cash, 0.0
        for k, p in self.P.items():
            coin = split_key(k)[1]
            wp = prices.get(coin, self.M.get(coin, p["a"]))
            worst += p["q"] * (wp - p["a"])
            maint += abs(p["q"]) * wp / (2 * self.maxlev(coin))
        liquidated = None
        if self.P and worst < maint:
            self.liquidations += 1
            liquidated = list(self.P)
            for k in liquidated:
                self.WAIT[k] = True
                tr = self.T.pop(k, None)
                if tr is not None and tr["opened"]:
                    wallet, coin = split_key(k)
                    self.trades += 1
                    w = self._wallet(wallet)
                    w["trades"] += 1
                    self.closed.append({"ledger": self.name, "wallet": wallet, "coin": coin, "side": tr["side"],
                                        "opened_ms": tr["t0"], "closed_ms": t, "pnl": None, "fees": tr["fees"],
                                        "max_notional": tr["max_ntl"], "cap": tr["cap"], "r": tr["R"],
                                        "adds": tr["adds"], "capped": tr["capped"], "reason": "LIQUIDATED",
                                        "exit_px": prices.get(coin)})
            self.P.clear()
            self.cash = max(worst, 0.0)
        held = {split_key(k)[1] for k in (*self.P, *self.T)}
        for coin, px in prices.items():
            if coin in held:
                self.M[coin] = px
        e = self.equity()
        self.peak = max(self.peak, e)
        self.mdd = max(self.mdd, 1 - e / self.peak if self.peak > 0 else 0.0)
        self.cb_peak = max(self.cb_peak, e)
        if self.dd_pause and e < self.cb_peak * (1 - self.dd_pause) and t >= self.paused_until:
            self.paused_until = t + int(self.dd_pause_days * DAY_MS)
            self.cb_peak = e
            self.pauses += 1
        return liquidated

    # -- views / persistence --------------------------------------------------------------------------------

    def positions(self) -> list[dict[str, Any]]:
        out = []
        for k, p in self.P.items():
            wallet, coin = split_key(k)
            m = self.mark_of(k)
            tr = self.T.get(k) or {}
            out.append({"wallet": wallet, "coin": coin, "side": 1 if p["q"] > 0 else -1, "qty": abs(p["q"]),
                        "entry": p["a"], "mark": m, "notional": abs(p["q"]) * m, "upnl": p["q"] * (m - p["a"]),
                        "cap": tr.get("cap"), "r": tr.get("R"), "adds": tr.get("adds", 0),
                        "capped": tr.get("capped", 0), "opened_ms": tr.get("t0"), "realized": tr.get("pnl", 0.0),
                        "maxlev": self.maxlev(coin)})
        return sorted(out, key=lambda x: -x["notional"])

    def summary(self, now_ms: int) -> dict[str, Any]:
        e = self.equity()
        mm = self.maintenance()
        return {"name": self.name, "equity": e, "cash": self.cash, "unrealized": e - self.cash,
                "start_equity": self.start_equity, "return_pct": (e / self.start_equity - 1) * 100,
                "notional": self.notional(), "exposure_x": self.notional() / e if e > 0 else None,
                "maintenance": mm, "liq_distance": e / mm if mm > 0 else None, "peak": self.peak,
                "drawdown_pct": (1 - e / self.peak) * 100 if self.peak > 0 else 0.0, "max_drawdown_pct": self.mdd * 100,
                "paused_until": self.paused_until if self.paused(now_ms) else None, "pauses": self.pauses,
                "liquidations": self.liquidations, "trades": self.trades, "wins": self.wins,
                "win_rate": self.wins / self.trades if self.trades else None, "fees_paid": self.fees_paid,
                "open_trades": sum(1 for t in self.T.values() if t["opened"]), "leverage": self.leverage,
                "fee": self.fee, "per_wallet": self.per_wallet}

    def to_dict(self) -> dict[str, Any]:
        return {"cash": self.cash, "P": self.P, "T": self.T, "WAIT": self.WAIT, "M": self.M, "peak": self.peak,
                "mdd": self.mdd, "cb_peak": self.cb_peak, "paused_until": self.paused_until, "pauses": self.pauses,
                "liquidations": self.liquidations, "trades": self.trades, "wins": self.wins,
                "fees_paid": self.fees_paid, "per_wallet": self.per_wallet, "started_ms": self.started_ms,
                "start_equity": self.start_equity}

    def load(self, d: dict[str, Any]) -> None:
        self.cash = float(d["cash"])
        self.P = {k: {"q": float(v["q"]), "a": float(v["a"])} for k, v in d.get("P", {}).items()}
        self.T = dict(d.get("T", {}))
        self.WAIT = {k: bool(v) for k, v in d.get("WAIT", {}).items()}
        self.M = {k: float(v) for k, v in d.get("M", {}).items()}
        for name in ("peak", "mdd", "cb_peak", "fees_paid", "start_equity"):
            setattr(self, name, float(d.get(name, getattr(self, name))))
        for name in ("paused_until", "pauses", "liquidations", "trades", "wins", "started_ms"):
            setattr(self, name, int(d.get(name, getattr(self, name))))
        self.per_wallet = dict(d.get("per_wallet", {}))
