"""AI trade advisor for the simulator's open trades (botup.md §V8). For every open trade it keeps:

  numbers  -- computed here, deterministic, from public data:
      * the trader's own record (Hyperliquid fills of the last year, rebuilt into round trips): win rate overall /
        on this side / on this coin, how long his trades last, how big his usual win and loss are, and the win
        rate of his trades that lasted at least as long as this one already has ("still open after N hours");
      * the market (Hyperliquid 1h candles + asset context): trend (EMA20 / EMA50), RSI14, ATR14 %, 24 h change,
        distance from the 7-day high / low, funding, open interest -- now and at the moment he entered;
      * our position: entry, mark, return %, cap used, the account's liquidation distance.
    -> an estimated success probability (his record, shrunk towards his overall rate), a risk score 0-100 with its
       reasons, and exit alerts (dashboard only) when a rule fires: usual-profit zone reached, longer than he
       usually holds, momentum turned against us, overbought / oversold in our favour, loss beyond his usual,
       liquidation near, funding against us, the trader started to reduce.
  words    -- Claude (Anthropic API, key in the OS keyring `loco-copy-anthropic`) reads those numbers and writes,
      in Arabic: the likely reason for the entry (inferred -- traders do not publish reasons), the thesis, the
      risks, an exit plan and a manual-exit hint. Asked when a trade opens (after it settles), every
      ADVISOR_REFRESH_HOURS, when a high alert fires, and on demand from the dashboard. Capped per day.

Nothing here trades. The tested strategy exits only with the trader: a manual exit leaves that strategy.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from loco_copy.config import Settings
from loco_copy.feed import HyperliquidClient
from loco_copy.notify import Notifier
from loco_copy.portfolio import PortfolioSim, dex_of, ledger_name
from loco_copy.store import Store

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
HISTORY_TTL_MS = 6 * HOUR_MS
CANDLE_TTL_MS = 5 * 60_000
CTX_TTL_MS = 60_000
LOOP_SECONDS = 60
SETTLE_MS = 2 * 60_000  # a new trade is analysed once its first burst of adds is over
ALERT_REARM_MS = 2 * HOUR_MS
ANTHROPIC_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
KEYRING_SERVICE = "loco-copy-anthropic"
SHRINK = 10.0  # Bayesian shrink weight (trades) towards the broader rate

SEVERITY_ORDER = {"info": 0, "medium": 1, "high": 2}


def _now() -> int:
    return int(time.time() * 1000)


# -- the trader's record --------------------------------------------------------------------------------------

def round_trips(fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Closed trades of one wallet from its fills (same reconstruction as the research `wallet_scanner`):
    a trade starts when the position leaves 0 and ends at 0 (a flip ends one and starts the next).
    ret = net PnL / largest notional of the trade."""
    out: list[dict[str, Any]] = []
    st: dict[str, dict[str, Any] | None] = {}
    for f in sorted(fills, key=lambda x: (x["time"], x.get("tid", 0))):
        c = str(f["coin"])
        if not c or c[0] in "@#" or "/" in c:
            continue
        sz, px = float(f["sz"]), float(f["px"])
        d = sz if f["side"] == "B" else -sz
        s0 = float(f["startPosition"])
        s1 = s0 + d
        t = st.get(c)
        if abs(s0) < 1e-12:
            t = st[c] = {"coin": c, "side": 1 if d > 0 else -1, "open": f["time"], "pnl": 0.0, "fee": 0.0,
                         "maxntl": 0.0, "pre": False}
        elif t is None:
            t = st[c] = {"pre": True}
        if not t["pre"]:
            t["pnl"] += float(f.get("closedPnl") or 0)
            t["fee"] += float(f.get("fee") or 0)
            t["maxntl"] = max(t["maxntl"], abs(s1) * px, abs(s0) * px)
        flip = abs(s0) > 1e-12 and abs(s1) > 1e-9 and (s0 > 0) != (s1 > 0)
        if abs(s1) < 1e-9 or flip:
            if not t["pre"] and t["maxntl"] > 0:
                net = t["pnl"] - t["fee"]
                out.append({"coin": c, "side": t["side"], "open": t["open"], "close": f["time"], "net": net,
                            "ret": net / t["maxntl"], "hold_ms": f["time"] - t["open"]})
            st[c] = None
            if flip:
                st[c] = {"coin": c, "side": 1 if s1 > 0 else -1, "open": f["time"], "pnl": 0.0, "fee": 0.0,
                         "maxntl": abs(s1) * px, "pre": False}
    return out


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _rate(trips: list[dict[str, Any]]) -> tuple[int, int]:
    return sum(1 for t in trips if t["net"] > 0), len(trips)


def trader_stats(trips: list[dict[str, Any]], *, coin: str, side: int, age_ms: int) -> dict[str, Any]:
    """His record and the estimated success probability of the trade in progress.
    p_all -> p_side (shrunk to p_all) -> p_coin_side (shrunk to p_side); then, if enough of his trades lasted at
    least as long as this one already has, half of that conditional rate (shrunk to p_coin_side) is mixed in."""
    w_all, n_all = _rate(trips)
    p_all = (w_all + 1) / (n_all + 2)  # Laplace: never exactly 0 or 1
    same_side = [t for t in trips if t["side"] == side]
    w_s, n_s = _rate(same_side)
    p_side = (w_s + SHRINK * p_all) / (n_s + SHRINK)
    cs = [t for t in same_side if t["coin"] == coin]
    w_cs, n_cs = _rate(cs)
    p_cs = (w_cs + SHRINK * p_side) / (n_cs + SHRINK)
    survived = [t for t in trips if t["hold_ms"] >= age_ms]
    w_sv, n_sv = _rate(survived)
    p = p_cs
    if age_ms > HOUR_MS and n_sv >= 10:
        p_sv = (w_sv + SHRINK * p_cs) / (n_sv + SHRINK)
        p = 0.5 * p_cs + 0.5 * p_sv
    wins = [t["ret"] for t in trips if t["net"] > 0]
    losses = [-t["ret"] for t in trips if t["net"] <= 0]
    holds = [t["hold_ms"] for t in trips]
    return {
        "trades": n_all, "win_rate": w_all / n_all if n_all else None,
        "side_trades": n_s, "side_win_rate": w_s / n_s if n_s else None,
        "coin_trades": n_cs, "coin_win_rate": w_cs / n_cs if n_cs else None,
        "survived_trades": n_sv, "survived_win_rate": w_sv / n_sv if n_sv else None,
        "hold_p50_h": (_pct(holds, 0.5) or 0) / HOUR_MS if holds else None,
        "hold_p75_h": (_pct(holds, 0.75) or 0) / HOUR_MS if holds else None,
        "hold_p90_h": (_pct(holds, 0.9) or 0) / HOUR_MS if holds else None,
        "win_ret_p50": _pct(wins, 0.5), "win_ret_p75": _pct(wins, 0.75),
        "loss_ret_p50": _pct(losses, 0.5), "loss_ret_p75": _pct(losses, 0.75),
        "probability": p,
        "method": "سجل المتداول: نسبة ربحه الكلية ← على نفس الاتجاه ← على نفس العملة، مع صفقاته التي استمرت مثل هذه",
    }


# -- the market -----------------------------------------------------------------------------------------------

def _ema(values: list[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    out: list[float] = []
    for v in values:
        out.append(v if not out else out[-1] + k * (v - out[-1]))
    return out


def indicators(candles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """1h candles (Hyperliquid format) -> trend, momentum and volatility at the last bar."""
    if len(candles) < 30:
        return None
    c = [float(k["c"]) for k in candles]
    h = [float(k["h"]) for k in candles]
    lo = [float(k["l"]) for k in candles]
    e20, e50 = _ema(c, 20)[-1], _ema(c, 50)[-1]
    gains, losses = [], []
    for a, b in zip(c[-15:-1], c[-14:], strict=True):
        gains.append(max(b - a, 0.0))
        losses.append(max(a - b, 0.0))
    ag, al = sum(gains) / 14, sum(losses) / 14
    rsi = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    trs = [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(len(c) - 14, len(c))]
    last = c[-1]
    week = candles[-168:]
    hi7, lo7 = max(float(k["h"]) for k in week), min(float(k["l"]) for k in week)
    trend = "up" if last > e20 > e50 else "down" if last < e20 < e50 else "range"
    return {"price": last, "ema20": e20, "ema50": e50, "rsi14": rsi, "atr14_pct": sum(trs) / 14 / last * 100,
            "change_24h_pct": (last / c[-25] - 1) * 100 if len(c) > 25 else None, "trend": trend,
            "high_7d": hi7, "low_7d": lo7, "from_high_7d_pct": (last / hi7 - 1) * 100,
            "from_low_7d_pct": (last / lo7 - 1) * 100, "bar_time": candles[-1]["t"]}


# -- risk and exit alerts -------------------------------------------------------------------------------------

def assess(pos: dict[str, Any], stats: dict[str, Any] | None, mkt: dict[str, Any] | None, ctx: dict[str, Any] | None,
           *, age_ms: int, liq_distance: float | None, leader_reducing: bool) -> dict[str, Any]:
    """Risk score 0-100 with its reasons, and the exit alerts that fire now (code, severity, Arabic text)."""
    side = pos["side"]
    ret = side * (pos["mark"] / pos["entry"] - 1) if pos["entry"] else 0.0  # price move in our favour
    risk = 10.0
    reasons: list[str] = []
    alerts: list[dict[str, str]] = []

    def alert(code: str, severity: str, text: str) -> None:
        alerts.append({"code": code, "severity": severity, "text": text})

    if liq_distance is not None:
        if liq_distance < 2:
            risk += 40
            reasons.append(f"الحساب قريب من التصفية (الرصيد = {liq_distance:.1f}× هامش الصيانة)")
            alert("LIQ_NEAR", "high", f"الحساب قريب من التصفية: الرصيد {liq_distance:.1f}× هامش الصيانة فقط")
        elif liq_distance < 4:
            risk += 15
            reasons.append(f"هامش الأمان من التصفية متوسط ({liq_distance:.1f}×)")
    cap = pos.get("cap") or 0
    if cap and pos["qty"] * pos["entry"] >= cap * 0.99:
        risk += 5
        reasons.append("الصفقة بلغت سقف 20% من الرصيد")
    if mkt:
        against = (side > 0 and mkt["trend"] == "down") or (side < 0 and mkt["trend"] == "up")
        if against:
            risk += 15
            reasons.append("الاتجاه على فريم الساعة عكس صفقتنا (السعر وEMA20 وEMA50)")
            crossed = (side > 0 and mkt["price"] < mkt["ema20"]) or (side < 0 and mkt["price"] > mkt["ema20"])
            if crossed:
                alert("MOMENTUM_AGAINST", "medium",
                      "الزخم انقلب ضد الصفقة: السعر عكس EMA20 والاتجاه عكسنا على فريم الساعة")
        if mkt["atr14_pct"] > 2.5:
            risk += 10
            reasons.append(f"تذبذب عالٍ: متوسط حركة الساعة {mkt['atr14_pct']:.2f}%")
        if ret > 0 and ((side > 0 and mkt["rsi14"] > 75) or (side < 0 and mkt["rsi14"] < 25)):
            alert("RSI_EXTREME_FAVOR", "info", f"تشبّع لصالحنا (RSI {mkt['rsi14']:.0f}) — مكان معتاد لجني ربح جزئي")
    if ctx and ctx.get("funding") is not None:
        yearly = float(ctx["funding"]) * 24 * 365 * 100
        if side * yearly > 30:  # longs pay positive funding, shorts pay negative
            risk += 5
            reasons.append(f"التمويل ضدنا ({yearly:+.0f}% سنويًا)")
            alert("FUNDING_AGAINST", "info", f"التمويل ضد الصفقة ({yearly:+.0f}% سنويًا) — كلفة الاحتفاظ ترتفع")
    if stats:
        p90 = stats.get("hold_p90_h")
        if p90 and age_ms / HOUR_MS > p90:
            risk += 10
            reasons.append(f"أطول من 90% من صفقاته ({age_ms / HOUR_MS:.0f} س مقابل {p90:.0f} س)")
            alert("OVERDUE", "medium" if ret > 0 else "info",
                  f"الصفقة أطول من 90% من صفقات المتداول ({age_ms / HOUR_MS:.0f} ساعة)"
                  + (" وهي رابحة — وقت مناسب للتفكير بجني الربح" if ret > 0 else ""))
        w50, w75 = stats.get("win_ret_p50"), stats.get("win_ret_p75")
        if w75 and ret >= w75:
            alert("TAKE_PROFIT_ZONE", "medium",
                  f"الربح {ret * 100:.2f}% تجاوز 75% من صفقاته الرابحة ({w75 * 100:.2f}%) — منطقة جني ربح")
        elif w50 and ret >= w50:
            alert("TAKE_PROFIT_ZONE", "info",
                  f"الربح {ret * 100:.2f}% وصل ربح صفقاته الرابحة المعتاد ({w50 * 100:.2f}%)")
        l75 = stats.get("loss_ret_p75")
        if l75 and ret <= -l75:
            risk += 20
            reasons.append(f"الخسارة {ret * 100:.2f}% أكبر من 75% من خساراته المعتادة")
            alert("LOSS_BEYOND_USUAL", "high", f"الخسارة {ret * 100:.2f}% أكبر من خسارته المعتادة ({-l75 * 100:.2f}%)")
        p = stats.get("probability")
        if p is not None and p < 0.45:
            risk += 10
            reasons.append(f"سجله ضعيف في هذا النوع من الصفقات ({p * 100:.0f}%)")
    if leader_reducing:
        alert("LEADER_REDUCING", "info", "المتداول بدأ يخفّف مركزه خلال الساعة الأخيرة")
    score = max(0.0, min(100.0, risk))
    level = "low" if score < 30 else "medium" if score < 60 else "high"
    return {"ret": ret, "risk_score": score, "risk_level": level, "risk_reasons": reasons, "alerts": alerts}


# -- Claude ---------------------------------------------------------------------------------------------------

SYSTEM_PROMPT = "\n".join([
    'أنت محلل صفقات عملات رقمية خبير يكتب بالعربية الفصحى المبسطة لمالك بوت ينسخ صفقات متداولين على Hyperliquid.',
    'تصلك أرقام محسوبة مسبقًا عن صفقة مفتوحة (سجل المتداول، حالة السوق الآن ووقت الدخول، مركزنا، المخاطر، التنبيهات).',
    'القواعد:',
    '- لا تخترع أي رقم: استخدم الأرقام المعطاة فقط، ويمكنك تقريبها.',
    '- المتداول لا يعلن سبب دخوله: «سبب الدخول» استنتاج من حالة السوق وقت الدخول '
    'وطريقة دخوله (دفعة واحدة أو تعزيزات)، قل ذلك بوضوح.',
    '- نسبة النجاح المعطاة تقدير إحصائي من سجله، لا وعد. علّق عليها ولا تغيّرها.',
    '- الاستراتيجية المختبرة تخرج مع المتداول فقط؛ أي خروج يدوي يخرج عنها — اذكر ذلك عند اقتراح الخروج.',
    '- كن محددًا ومختصرًا: جمل قصيرة، بدون مقدمات.',
    'أجب بـ JSON فقط بهذا الشكل:',
    '{"entry_reason": "...", "thesis": "...", "success_comment": "...", "risks": ["...", "..."],',
    ' "exit_plan": "...", "manual_exit_hint": "...", "verdict": "hold|watch|take_partial|exit",'
    ' "confidence": "low|medium|high"}',
])


class ClaudeClient:
    def __init__(self, api_key: str, model: str = "", http: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self.model = model
        self._http = http or httpx.AsyncClient(timeout=60)

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}

    async def pick_model(self) -> str:
        """ADVISOR_MODEL, else the newest Sonnet this key can use (from /v1/models), else the newest model."""
        if self.model:
            return self.model
        r = await self._http.get(f"{ANTHROPIC_URL}/models", params={"limit": 100}, headers=self._headers())
        r.raise_for_status()
        models = sorted(r.json().get("data", []), key=lambda m: m.get("created_at", ""), reverse=True)
        sonnet = [m["id"] for m in models if "sonnet" in m["id"]]
        if not sonnet and not models:
            raise RuntimeError("no models available for this API key")
        self.model = sonnet[0] if sonnet else models[0]["id"]
        return self.model

    async def analyse(self, facts: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        model = await self.pick_model()
        body = {"model": model, "max_tokens": 1500, "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": "بيانات الصفقة:\n" + json.dumps(facts, ensure_ascii=False)}]}
        r = await self._http.post(f"{ANTHROPIC_URL}/messages", json=body, headers=self._headers())
        r.raise_for_status()
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        start, end = text.find("{"), text.rfind("}")
        parsed = json.loads(text[start:end + 1]) if start >= 0 and end > start else {"thesis": text}
        return parsed, {"model": data.get("model", model), "usage": data.get("usage", {})}


def _key_from_keyring() -> str | None:
    try:
        import keyring  # noqa: PLC0415

        return keyring.get_password(KEYRING_SERVICE, "api_key")
    except Exception:  # noqa: BLE001 - no keyring backend: no AI text, numbers still work
        return None


# -- the live advisor -----------------------------------------------------------------------------------------

def trade_key(wallet: str, coin: str, t0: int) -> str:
    return f"{wallet}|{coin}|{t0}"


class Advisor:
    def __init__(self, settings: Settings, store: Store, notifier: Notifier, client: HyperliquidClient,
                 sim: PortfolioSim, *, claude: ClaudeClient | None = None, clock: Callable[[], int] = _now) -> None:
        self.s = settings
        self.store = store
        self.n = notifier
        self.client = client
        self.sim = sim
        self.clock = clock
        if claude is None:
            key = _key_from_keyring()
            claude = ClaudeClient(key, settings.advisor_model) if key else None
        self.claude = claude
        self._history: dict[str, tuple[int, list[dict[str, Any]]]] = {}
        self._candles: dict[str, tuple[int, list[dict[str, Any]]]] = {}
        self._ctx: dict[str, tuple[int, dict[str, dict[str, Any]]]] = {}

    # -- data ----------------------------------------------------------------------------------------------

    async def history(self, address: str) -> list[dict[str, Any]]:
        """The wallet's fills of the last year (Hyperliquid keeps the last 10,000), cached HISTORY_TTL_MS."""
        cached = self._history.get(address)
        if cached and self.clock() - cached[0] < HISTORY_TTL_MS:
            return cached[1]
        fills: list[dict[str, Any]] = []
        seen: set[int] = set()
        start = self.clock() - 365 * DAY_MS
        for _ in range(6):
            page = await self.client.info({"type": "userFillsByTime", "user": address, "startTime": start,
                                           "aggregateByTime": True}, 20)
            if not page:
                break
            new = [f for f in page if f["tid"] not in seen]
            seen.update(f["tid"] for f in new)
            fills += new
            if len(page) < 2000 or not new:
                break
            start = max(int(f["time"]) for f in page)
        trips = round_trips(fills)
        self._history[address] = (self.clock(), trips)
        return trips

    async def candles(self, coin: str) -> list[dict[str, Any]]:
        cached = self._candles.get(coin)
        if cached and self.clock() - cached[0] < CANDLE_TTL_MS:
            return cached[1]
        now = self.clock()
        rows = await self.client.info({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h",
                                                                         "startTime": now - 400 * HOUR_MS,
                                                                         "endTime": now}}, 20)
        rows = list(rows or [])
        self._candles[coin] = (now, rows)
        return rows

    async def asset_ctx(self, coin: str) -> dict[str, Any] | None:
        dex = dex_of(coin)
        cached = self._ctx.get(dex)
        if not cached or self.clock() - cached[0] >= CTX_TTL_MS:
            body: dict[str, Any] = {"type": "metaAndAssetCtxs"} | ({"dex": dex} if dex else {})
            meta, ctxs = await self.client.info(body, 20)
            table = {u["name"]: c for u, c in zip(meta.get("universe", []), ctxs, strict=False)}
            cached = self._ctx[dex] = (self.clock(), table)
        return cached[1].get(coin)

    # -- one trade -----------------------------------------------------------------------------------------

    def open_trades(self) -> list[dict[str, Any]]:
        """Our open trades on the smallest real account of every simulator section (the advice does not depend
        on the account size). A trade held by several sections (8bae in the trio and in a mix) is analysed once:
        the first section that holds it (the trio before the mixes) gives its numbers (§V10)."""
        out: dict[str, dict[str, Any]] = {}
        for g in self.sim.groups:
            name = ledger_name("real", self.sim.capitals[0], g.name)
            for p in self.sim.ledgers[name].positions():
                if p.get("opened_ms") and f"{p['wallet']}|{p['coin']}" not in out:
                    out[f"{p['wallet']}|{p['coin']}"] = p | {"ledger": name}
        return list(out.values())

    async def snapshot(self, pos: dict[str, Any]) -> dict[str, Any]:
        wallet = next(w for w in self.sim.wallets if w.label == pos["wallet"])
        now = self.clock()
        age = now - int(pos["opened_ms"])
        trips, rows, ctx = await asyncio.gather(self.history(wallet.address), self.candles(pos["coin"]),
                                                self.asset_ctx(pos["coin"]))
        stats = trader_stats(trips, coin=pos["coin"], side=pos["side"], age_ms=age) if trips else None
        mkt = indicators(rows)
        at_entry = indicators([k for k in rows if int(k["t"]) <= int(pos["opened_ms"])])
        ledger = self.sim.ledgers[pos.get("ledger") or ledger_name("real", self.sim.capitals[0])]
        recent = self.store.pf_events_for(pos["wallet"], pos["coin"], now - HOUR_MS)
        reducing = any(abs(e["start_pos"] + (e["sz"] if e["side"] == "B" else -e["sz"])) < abs(e["start_pos"])
                       for e in recent)
        verdict = assess(pos, stats, mkt, ctx, age_ms=age, liq_distance=ledger.liquidation_distance(),
                         leader_reducing=reducing)
        entry_fills = [dict(e) for e in self.store.pf_events_for(pos["wallet"], pos["coin"], int(pos["opened_ms"]) - 1)]
        return {"wallet": pos["wallet"], "coin": pos["coin"], "side": pos["side"], "t0": int(pos["opened_ms"]),
                "age_h": age / HOUR_MS, "position": pos, "stats": stats, "market": mkt, "market_at_entry": at_entry,
                "ctx": {k: ctx.get(k) for k in ("funding", "openInterest", "dayNtlVlm", "premium", "markPx")}
                if ctx else None,
                "trader_fills": [{"t": e["leader_time_ms"], "side": e["side"], "px": e["px"], "sz": e["sz"],
                                  "start": e["start_pos"]} for e in entry_fills[:40]],
                "liq_distance": ledger.liquidation_distance(), **verdict, "updated_ms": now}

    def record_alerts(self, snap: dict[str, Any]) -> list[dict[str, str]]:
        """New alerts only: an alert stays quiet while it keeps firing, and may fire again ALERT_REARM_MS after it
        stopped. Dashboard + log (INFO: never Telegram -- owner's choice)."""
        key = trade_key(snap["wallet"], snap["coin"], snap["t0"])
        state: dict[str, Any] = self.store.get_state(f"advisor_alerts:{key}") or {}
        now = self.clock()
        fresh = []
        firing = {a["code"] for a in snap["alerts"]}
        for a in snap["alerts"]:
            st: dict[str, Any] = state.get(a["code"]) or {}
            recent = bool(st) and (st.get("active") or now - int(st.get("cleared_ms", 0)) < ALERT_REARM_MS)
            if recent and SEVERITY_ORDER[a["severity"]] <= SEVERITY_ORDER.get(st.get("severity", "info"), 0):
                state[a["code"]] = {**st, "active": True}
                continue
            state[a["code"]] = {"active": True, "severity": a["severity"], "since": now}
            fresh.append(a)
            self.store.pf_insert_alert({"wallet": snap["wallet"], "coin": snap["coin"], "trade_t0": snap["t0"],
                                        "ts": now, "code": a["code"], "severity": a["severity"], "text": a["text"],
                                        "price": snap["position"]["mark"], "ret": snap["ret"]})
            self.n.info("PF_ADVICE_ALERT", wallet=snap["wallet"], coin=snap["coin"], code=a["code"],
                        severity=a["severity"], text=a["text"])
        for code, st in state.items():
            if code not in firing and st.get("active"):
                state[code] = {**st, "active": False, "cleared_ms": now}
        self.store.set_state(f"advisor_alerts:{key}", state)
        return fresh

    def ai_due(self, snap: dict[str, Any], fresh: list[dict[str, str]]) -> str | None:
        key = trade_key(snap["wallet"], snap["coin"], snap["t0"])
        req = self.store.get_state(f"advisor_request:{snap['wallet']}|{snap['coin']}")
        if req and not req.get("done"):
            return "manual"
        if self.clock() - snap["t0"] < SETTLE_MS:
            return None
        last = int(self.store.get_state(f"advisor_ai_at:{key}") or 0)
        if not last:
            return "open"
        if any(a["severity"] == "high" for a in fresh):
            return "alert"
        if self.clock() - last >= self.s.advisor_refresh_hours * HOUR_MS:
            return "refresh"
        return None

    def _budget_ok(self) -> bool:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        used = self.store.get_state(f"advisor_calls:{day}") or 0
        return int(used) < self.s.advisor_max_daily_calls

    def _spend(self) -> None:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        self.store.set_state(f"advisor_calls:{day}", int(self.store.get_state(f"advisor_calls:{day}") or 0) + 1)

    async def run_ai(self, snap: dict[str, Any], why: str) -> None:
        key = trade_key(snap["wallet"], snap["coin"], snap["t0"])
        self.store.set_state(f"advisor_ai_at:{key}", self.clock())
        if why == "manual":
            req_key = f"advisor_request:{snap['wallet']}|{snap['coin']}"
            self.store.set_state(req_key, {**(self.store.get_state(req_key) or {}), "done": True})
        ai: dict[str, Any] | None = None
        meta: dict[str, Any] = {"why": why}
        if self.claude is None:
            meta["error"] = "لا يوجد مفتاح Anthropic على السيرفر — شغّل: loco-copy set-key anthropic"
        elif not self._budget_ok():
            meta["error"] = f"بلغ الحد اليومي ({self.s.advisor_max_daily_calls} تحليل)"
        else:
            facts = {k: snap[k] for k in ("coin", "side", "age_h", "stats", "market", "market_at_entry", "ctx",
                                          "trader_fills", "liq_distance", "ret", "risk_score", "risk_level",
                                          "risk_reasons", "alerts")}
            facts["side"] = "شراء (long)" if snap["side"] > 0 else "بيع (short)"
            facts["position"] = {k: snap["position"].get(k) for k in ("entry", "mark", "notional", "upnl", "fees",
                                                                      "adds", "cap")}
            facts["trader"] = snap["wallet"]
            try:
                self._spend()
                ai, info = await self.claude.analyse(facts)
                meta |= info
            except Exception as exc:  # noqa: BLE001 - the numbers are stored anyway
                meta["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                self.n.warning("PF_ADVICE_AI_FAILED", wallet=snap["wallet"], coin=snap["coin"], error=meta["error"])
        self.store.pf_insert_analysis({
            "wallet": snap["wallet"], "coin": snap["coin"], "trade_t0": snap["t0"], "ts": self.clock(), "kind": why,
            "probability": (snap["stats"] or {}).get("probability"), "risk_score": snap["risk_score"],
            "snapshot": json.dumps({k: v for k, v in snap.items() if k != "trader_fills"}, ensure_ascii=False,
                                   default=str),
            "ai": json.dumps(ai, ensure_ascii=False) if ai else None, "meta": json.dumps(meta, ensure_ascii=False)})

    async def tick(self) -> None:
        live = {}
        for pos in self.open_trades():
            try:
                snap = await self.snapshot(pos)
            except Exception as exc:  # noqa: BLE001 - one market's data failing never stops the others
                self.n.info("PF_ADVICE_FAILED", wallet=pos["wallet"], coin=pos["coin"], error=str(exc)[:200])
                continue
            fresh = self.record_alerts(snap)
            live[f"{snap['wallet']}|{snap['coin']}"] = {k: v for k, v in snap.items() if k != "trader_fills"}
            why = self.ai_due(snap, fresh)
            if why:
                await self.run_ai(snap, why)
        self.store.set_state("advisor_live", {"updated_ms": self.clock(), "trades": live,
                                              "ai_enabled": self.claude is not None})

    async def loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - never stop advising
                self.n.warning("PF_ADVICE_LOOP_FAILED", error=f"{type(exc).__name__}: {exc}")
            for _ in range(LOOP_SECONDS // 5):  # a dashboard request is answered within ~5 s
                await asyncio.sleep(5)
                if self.store.state_keys("advisor_request:") and self._pending_request():
                    break

    def _pending_request(self) -> bool:
        for k in self.store.state_keys("advisor_request:"):
            req = self.store.get_state(k) or {}
            if not req.get("done"):
                return True
        return False
