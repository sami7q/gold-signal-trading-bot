"""Dashboard API (`loco-copy api`): a separate process from the bot, so nothing here can ever block or crash
trading. It reads the bot's SQLite (WAL) and writes only three things, all through the `state` table that the
bot already polls: the kill switch, a close-all request, and the watch/observe/execute mode.

Listens on 127.0.0.1 only; the Cloudflare tunnel is the only way in. Logins are cookie sessions
(HttpOnly + Secure + SameSite=None, because the dashboard on Vercel is a different site).
"""

import asyncio
import json
import sqlite3
import time
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from loco_copy.auth import new_token, token_hash, verify_password
from loco_copy.cap20 import describe
from loco_copy.config import DEFAULT_LABEL, Settings, get_settings, with_mode_override
from loco_copy.constants import CopyStatus, EventType
from loco_copy.metrics import compute, percentile
from loco_copy.models import Copy
from loco_copy.notify import Notifier
from loco_copy.observe import event_slippage_bps, lane_ledger, observe_stats
from loco_copy.pf_ledger import Ledger
from loco_copy.risk import RiskGuard, release_everything
from loco_copy.store import Store, lane_key, now_ms

COOKIE = "loco_session"
EXECUTE_CONFIRM = "ENABLE EXECUTION"
DAY_MS = 86_400_000
CLOSED_REAL = (CopyStatus.CLOSED_LEADER, CopyStatus.CLOSED_TP, CopyStatus.CLOSED_LIQ, CopyStatus.CLOSED_MANUAL,
               CopyStatus.CLOSED_RECONCILED, CopyStatus.UNKNOWN)


def iso(ms: int | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat() if ms else None


def dec(v: Decimal | None) -> str | None:
    """Decimals travel as strings so no precision is lost to JSON floats."""
    return None if v is None else str(v)


def pct(v: Decimal | None, per: int = 1) -> str | None:
    """A fraction as percent text (divided by `per`, e.g. ROI per leverage); None stays None (= off)."""
    return None if v is None else str(v / per * 100)


def side_label(side: int) -> str:
    return "LONG" if side > 0 else "SHORT"


def copy_dict(c: Copy) -> dict[str, Any]:
    return {
        "id": c.id, "coin": c.coin, "symbol": c.symbol, "episode_id": c.episode_id, "side": side_label(c.side),
        "status": c.status, "observed": c.status == CopyStatus.OBSERVED_ONLY,
        "qty_initial": dec(c.qty_initial), "qty_open": dec(c.qty_open), "leader_entry_px": dec(c.leader_entry_px),
        "entry_price": dec(c.entry_price), "tp_price": dec(c.tp_price), "exit_price_avg": dec(c.exit_price_avg),
        "realized_pnl": dec(c.realized_pnl), "fees": dec(c.fees), "margin": dec(c.margin),
        "opened_at": iso(c.opened_at), "closed_at": iso(c.closed_at), "close_reason": c.close_reason,
        "slippage_bps": dec(c.slippage_bps), "latency_detect_ms": c.latency_detect_ms,
        "latency_order_ms": c.latency_order_ms, "leader": c.leader, "strategy": c.strategy,
        # cap20_cross (botup.md v4 §5 web): R, CAP, used cap %, pending, capped, adds, leader position
        "virtual": c.status == CopyStatus.OBSERVED_ONLY and c.strategy == "cap20_cross",
        "ratio_r": dec(c.ratio_r), "cap_notional": dec(c.cap_notional), "cost_notional": dec(c.cost_notional),
        "cap_used_pct": _pct(c.cost_notional / c.cap_notional, 1) if c.cap_notional else None,
        "pending_qty": dec(c.pending_qty), "capped": c.capped, "qty_at_cap": dec(c.qty_at_cap),
        "leader_pos_at_cap": dec(c.leader_pos_at_cap), "adds_done": c.adds_done, "leader_pos": dec(c.leader_pos),
    }


def event_dict(e: sqlite3.Row, copy: Copy | None) -> dict[str, Any]:
    return {"id": e["id"], "coin": e["coin"], "episode_id": e["episode_id"], "type": e["type"],
            "side": side_label(e["side"]) if e["side"] else None, "fraction": e["fraction"],
            "leader_px": e["leader_px"], "leader_time": iso(e["leader_time_ms"]),
            "detected_at": iso(e["detected_at_ms"]), "lag_ms": e["detected_at_ms"] - e["leader_time_ms"],
            "decision": e["decision"], "rejection": e["rejection"], "detail": e["detail"],
            "copy_id": copy.id if copy else None, "copy_status": copy.status if copy else None,
            "leader": e["leader"], "pos_before": e["pos_before"], "pos_after": e["pos_after"],
            "our_px": e["our_px"], "slippage_bps": event_slippage_bps(e)}


def fill_new_position(f: sqlite3.Row) -> Decimal:
    start, sz = Decimal(f["start_position"]), Decimal(f["sz"])
    return start + sz if f["side"] == "B" else start - sz


def fill_dict(f: sqlite3.Row | None) -> dict[str, Any] | None:
    if f is None:
        return None
    return {"tid": f["tid"], "oid": f["oid"], "side": "BUY" if f["side"] == "B" else "SELL", "px": f["px"],
            "sz": f["sz"], "start_position": f["start_position"], "new_position": dec(fill_new_position(f)),
            "time": iso(f["time_ms"]), "received_at": iso(f["received_at_ms"])}


def leader_order(store: Store, fill: sqlite3.Row | None) -> dict[str, Any] | None:
    """The whole leader order a fill belongs to: Hyperliquid can split one order into dozens of fills."""
    if fill is None:
        return None
    rows = [fill]
    if fill["oid"] is not None:
        rows = [f for f in store.coin_fills_between(fill["coin"], fill["time_ms"] - 60_000, fill["time_ms"] + 60_000,
                                                    leader=fill["leader"])
                if f["oid"] == fill["oid"]] or [fill]
    starts = [Decimal(f["start_position"]) for f in rows]
    ends = [fill_new_position(f) for f in rows]
    start_set, end_set = set(starts), set(ends)
    size = sum((Decimal(f["sz"]) for f in rows), Decimal(0))
    notional = sum((Decimal(f["sz"]) * Decimal(f["px"]) for f in rows), Decimal(0))
    return {"fills": len(rows), "side": "BUY" if fill["side"] == "B" else "SELL", "size": dec(size),
            "avg_px": dec(round(notional / size, 8)) if size else None, "notional": dec(round(notional, 2)),
            # the pieces chain start -> end; the order began where no piece ends and finished where none starts
            "position_before": dec(next((p for p in starts if p not in end_set), starts[0])),
            "position_after": dec(next((p for p in ends if p not in start_set), ends[-1])),
            "first_at": iso(min(f["time_ms"] for f in rows)), "last_at": iso(max(f["time_ms"] for f in rows))}


def order_dict(o: sqlite3.Row) -> dict[str, Any]:
    reason = o["client_id"].split(":", 3)[3] if o["client_id"].count(":") >= 3 else o["kind"]
    return {"id": o["id"], "kind": o["kind"], "reason": reason.split(":")[0], "client_id": o["client_id"],
            "requested_qty": o["requested_qty"], "filled_qty": o["filled_qty"], "avg_price": o["avg_price"],
            "fee": o["fee"], "status": o["status"], "error": o["error"], "sent_at": iso(o["sent_at_ms"]),
            "filled_at": iso(o["filled_at_ms"]),
            "latency_ms": o["filled_at_ms"] - o["sent_at_ms"] if o["filled_at_ms"] else None}


def _pct(v: Decimal, places: int) -> float:
    return float(round(v * 100, places))


def copy_detail_dict(store: Store, s: Settings, c: Copy) -> dict[str, Any]:
    """Everything about one copy, for the trade's detail panel: Loco's side of it, ours, and a timeline."""
    now = now_ms()
    events = store.episode_events(c.coin, c.episode_id, leader=c.leader)
    orders = store.orders_for_copy(c.id) if c.id is not None else []
    open_ev = next((e for e in events if e["type"] == EventType.OPEN), None)
    open_order = leader_order(store, store.fill_by_tid(open_ev["tid"], leader=c.leader)) if open_ev else None

    # The leader's own exit: the first fill after the open that left him flat or on the other side. Found from his
    # fills, so it is known even when the bot missed the event.
    exit_fill = None
    if open_ev is not None:
        horizon = now if c.closed_at is None else c.closed_at + DAY_MS
        for f in store.coin_fills_between(c.coin, open_ev["leader_time_ms"] + 1, horizon, leader=c.leader):
            start, new = Decimal(f["start_position"]), fill_new_position(f)
            if start * c.side > 0 and new * c.side <= 0:
                exit_fill = f
                break
    exit_order = leader_order(store, exit_fill)
    exit_captured = any(e["type"] in (EventType.CLOSE, EventType.FLIP) for e in events)

    entry, out = c.entry_price, c.exit_price_avg
    notional = c.qty_initial * entry if entry else None
    lev = round(notional / c.margin) if notional and c.margin else s.leverage
    gross = c.side * (out - entry) * c.exit_qty_total if out is not None and entry else None
    liq_move = Decimal(1) / lev - s.maintenance_margin_rate
    leader_exit_px = Decimal(exit_order["avg_px"]) if exit_order and exit_order["avg_px"] else None
    figures = {
        "notional": dec(round(notional, 2)) if notional else None, "leverage": lev, "margin": dec(c.margin),
        "gross_pnl": dec(round(gross, 8)) if gross is not None else None, "fees": dec(c.fees),
        "funding": dec(c.funding), "net_pnl": dec(c.realized_pnl),
        "roi_pct": _pct(c.realized_pnl / c.margin, 2) if c.margin and c.closed_at else None,
        "price_move_pct": _pct(c.side * (out / entry - 1), 3) if out is not None and entry else None,
        "tp_move_pct": _pct(c.side * (c.tp_price / entry - 1), 3) if c.tp_price and entry else None,
        "liq_price_est": dec(round(entry * (1 - c.side * liq_move), 8)) if entry else None,
        "duration_ms": (c.closed_at or now) - c.opened_at if c.opened_at else None,
    }
    comparison = {  # positive bps = worse for us than Loco
        "entry_diff_bps": float(round(c.slippage_bps, 2)) if c.slippage_bps is not None else None,
        "exit_diff_bps": float(round(c.side * (leader_exit_px - out) / leader_exit_px * 10_000, 2))
        if leader_exit_px and out is not None else None,
        "exit_delay_ms": c.closed_at - exit_fill["time_ms"] if exit_fill is not None and c.closed_at else None,
    }

    timeline: list[dict[str, Any]] = []

    def add(at: int | None, kind: str, **fields: Any) -> None:
        if at:
            timeline.append({"at": iso(at), "at_ms": at, "kind": kind, **fields})

    if open_ev is not None:
        add(open_ev["leader_time_ms"], "LEADER_OPEN", px=open_ev["leader_px"], order=open_order)
        add(open_ev["detected_at_ms"], "DETECTED", lag_ms=open_ev["detected_at_ms"] - open_ev["leader_time_ms"])
    for o in orders:
        add(o["sent_at_ms"], "ORDER_SENT", order=order_dict(o))
        if o["filled_at_ms"]:
            add(o["filled_at_ms"], "ORDER_FILLED", order=order_dict(o))
            if o["kind"] == "OPEN" and c.tp_price is not None:
                add(o["filled_at_ms"], "TP_SET", px=dec(c.tp_price), move_pct=figures["tp_move_pct"])
        elif o["status"] == "FAILED":
            add(o["sent_at_ms"], "ORDER_FAILED", order=order_dict(o))
    for e in events:
        if e["type"] != EventType.OPEN:
            add(e["leader_time_ms"], f"LEADER_{e['type']}", event=event_dict(e, c))
    if exit_fill is not None and not exit_captured:
        add(exit_fill["time_ms"], "LEADER_EXIT_MISSED", order=exit_order)
    if c.opened_at:
        for r in store.logs_between(c.opened_at - 60_000, (c.closed_at or now) + 60_000):
            payload = json.loads(r["payload"])
            same_coin = payload.get("coin") == c.coin or payload.get("symbol") == c.symbol
            if r["level"] != "INFO" and same_coin and payload.get("leader", c.leader) == c.leader:
                add(r["ts"], "LOG", level=r["level"], type=r["type"], payload=payload)
    add(c.closed_at, "CLOSED", status=c.status, reason=c.close_reason)
    timeline.sort(key=lambda x: x["at_ms"])  # stable: same-millisecond steps keep their logical order

    return {"copy": copy_dict(c) | {"exit_qty_total": dec(c.exit_qty_total), "funding": dec(c.funding)},
            "figures": figures, "comparison": comparison,
            "leader": {"open": open_order, "exit": exit_order, "exit_captured": exit_captured,
                       "exit_time": iso(exit_fill["time_ms"]) if exit_fill is not None else None},
            "orders": [order_dict(o) for o in orders], "timeline": timeline}


#: the simulator records equity every 5 min; a longer silence means the bot was not running
OUTAGE_MS = 30 * 60_000


def outages(ts: list[int], start: int, end: int) -> list[tuple[int, int]]:
    """Periods inside [start, end] with no equity snapshot for longer than OUTAGE_MS (bot or server stopped)."""
    points = [start, *(t for t in ts if start <= t <= end), end]
    return [(a, b) for a, b in pairwise(points) if b - a > OUTAGE_MS]


def in_outage(t: int, down: list[tuple[int, int]]) -> bool:
    return any(a < t <= b for a, b in down)


def pf_groups(rt: dict[str, Any]) -> list[dict[str, Any]]:
    """The simulator sections of the runtime (botup.md §V10); a runtime written before §V10 is the trio alone."""
    groups = rt.get("groups")
    if groups:
        return list(groups)
    return [{"name": "trio", "title": "الثلاثي", "labels": [w["label"] for w in rt.get("wallets") or []],
             "started_ms": rt.get("started_ms"), "research_year": None, "research_half": None}]


def pf_ledger_name(scenario: str, capital: int, group: str, lev: int = 0) -> str:
    """Same names as portfolio.ledger_name: lev=0 (or the base leverage, see pf_lev) keeps the pre-§V13 name."""
    name = f"{scenario}:{capital}" if group == "trio" else f"{group}:{scenario}:{capital}"
    return f"{name}:x{lev}" if lev else name


def pf_lev(rt: dict[str, Any], leverage: int | None) -> tuple[int, int, list[int]]:
    """(the chosen leverage, its ledger suffix -- 0 for the base leverage --, every leverage simulated)."""
    base = int(rt.get("base_leverage") or (rt.get("settings") or {}).get("leverage") or 3)
    levs = [int(x) for x in rt.get("leverages") or [base]]
    lev = leverage if leverage in levs else base
    return lev, (0 if lev == base else lev), levs


def pf_actions(e: sqlite3.Row, group: str) -> tuple[str, float, str, float] | None:
    """What one section did with a wallet fill: (base action, base qty, real action, real qty), or None when that
    section did not see the fill (wallet not in it, or before it started). Rows older than §V10 are the trio's."""
    raw = e["actions"]
    if raw:
        a = json.loads(raw).get(group)  # "mix2ac4" (base leverage) or "mix2ac4:x5"
        return (str(a[0]), float(a[1]), str(a[2]), float(a[3])) if a else None
    if group == "trio":
        return e["base_action"], e["base_qty"], e["real_action"], e["real_qty"]
    return None


LIVE_POLL_SECONDS = 0.5
LIVE_HEARTBEAT_SECONDS = 15.0
LIVE_AUTH_SECONDS = 60.0


def live_snapshot(store: Store) -> dict[str, Any]:
    """What changed since the last tick (botup.md §V11): the simulator's view time and the newest row of every
    table the dashboard shows. A few primary-key lookups -- cheap enough to run every half second."""
    one = store._db.execute  # noqa: SLF001 - read-only lookups on the store's own connection
    rt = one("SELECT json_extract(value, '$.updated_ms') AS v FROM state WHERE key = 'portfolio_runtime'").fetchone()
    out: dict[str, Any] = {"rt": rt["v"] if rt else None}
    for key, table in (("ev", "pf_events"), ("ord", "pf_orders"), ("tr", "pf_trades"), ("al", "pf_alerts"),
                       ("an", "pf_analysis"), ("log", "log"), ("cp", "events")):
        out[key] = one(f"SELECT MAX(id) AS v FROM {table}").fetchone()["v"]
    return out


def leader_name(label: str) -> str:
    """How the dashboard names a lane. Only Loco has a public name; other labels are internal."""
    return "Loco" if label == DEFAULT_LABEL else label


def mode_label(s: Settings) -> Literal["WATCH", "OBSERVE", "EXECUTE"]:
    if not s.copy_enabled:
        return "WATCH"
    return "OBSERVE" if s.copy_observe_only else "EXECUTE"


class LoginThrottle:
    """5 failed logins for one username or one address within 15 minutes -> refused for 15 minutes."""

    WINDOW, LIMIT = 900.0, 5

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = defaultdict(list)

    def blocked(self, *keys: str) -> bool:
        now = time.monotonic()
        for k in keys:
            self._fails[k] = [t for t in self._fails[k] if now - t < self.WINDOW]
        return any(len(self._fails[k]) >= self.LIMIT for k in keys)

    def fail(self, *keys: str) -> None:
        for k in keys:
            self._fails[k].append(time.monotonic())

    def clear(self, *keys: str) -> None:
        for k in keys:
            self._fails.pop(k, None)


class LoginBody(BaseModel):
    username: str
    password: str
    remember_me: bool = True


class ModeBody(BaseModel):
    mode: Literal["WATCH", "OBSERVE", "EXECUTE"]
    confirm: str | None = None
    #: Which leader's lane. Absent = the first one (v2 dashboards send no leader).
    leader: str | None = None


class ActiveBody(BaseModel):
    active: bool


class AdviceBody(BaseModel):
    wallet: str
    coin: str


class KillBody(BaseModel):
    close_all: bool = False


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    app = FastAPI(title="loco-copy", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=s.origins, allow_credentials=True,
                       allow_methods=["GET", "POST"], allow_headers=["*"])
    throttle = LoginThrottle()
    allowed_origins = frozenset(s.origins)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next: Any) -> Any:
        """CSRF guard. The session cookie is SameSite=None (Vercel and the tunnel are different sites), so any
        page could make the browser send it along with a plain form POST -- e.g. to /api/control/resume.
        Browsers always attach Origin to cross-site POSTs and pages cannot forge it, so every write must come
        from the dashboard's own origin. Reads stay as they are: CORS already keeps their responses private."""
        if request.method == "POST" and request.headers.get("origin") not in allowed_origins:
            return JSONResponse(status_code=403, content={"detail": "طلب من مصدر غير مسموح."})
        return await call_next(request)

    def get_store() -> Iterator[Store]:
        store = Store(s.db_path, cross_thread=True)
        try:
            yield store
        finally:
            store.close()

    StoreDep = Annotated[Store, Depends(get_store)]

    def current_user(request: Request, store: StoreDep) -> dict[str, Any]:
        token = request.cookies.get(COOKIE)
        user = store.session_user(token_hash(token)) if token else None
        if user is None:
            raise HTTPException(status_code=401, detail="يلزم تسجيل الدخول.")
        return dict(user)

    UserDep = Annotated[dict[str, Any], Depends(current_user)]

    # -- auth ------------------------------------------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "time": iso(now_ms())}

    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request, response: Response, store: StoreDep) -> dict[str, Any]:
        ip = request.client.host if request.client else "?"
        keys = (f"user:{body.username.lower()}", f"ip:{ip}")
        if throttle.blocked(*keys):
            raise HTTPException(status_code=429, detail="محاولات كثيرة خاطئة. حاول بعد 15 دقيقة.")
        row = store.user_by_username(body.username.strip())
        if row is None or not verify_password(body.password, row["password_hash"]):
            throttle.fail(*keys)
            raise HTTPException(status_code=401, detail="اسم المستخدم أو كلمة المرور غير صحيحة.")
        throttle.clear(*keys)
        token = new_token()
        lifetime = s.session_days * DAY_MS if body.remember_me else DAY_MS // 2
        store.create_session(token_hash(token), row["id"], now_ms() + lifetime)
        response.set_cookie(COOKIE, token, max_age=lifetime // 1000, httponly=True, secure=s.api_cookie_secure,
                            samesite="none" if s.api_cookie_secure else "lax", path="/")
        Notifier(store, echo=False).info("DASHBOARD_LOGIN", user=row["username"], ip=ip)
        return {"user": {"id": row["id"], "username": row["username"], "display_name": row["display_name"]}}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, store: StoreDep) -> dict[str, Any]:
        token = request.cookies.get(COOKIE)
        if token:
            store.delete_session(token_hash(token))
        response.delete_cookie(COOKIE, path="/", secure=s.api_cookie_secure,
                               samesite="none" if s.api_cookie_secure else "lax")
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(user: UserDep) -> dict[str, Any]:
        return {"user": user}

    # -- leaders (lanes) --------------------------------------------------------------------------------------

    lanes = {label: s.for_lane(label) for label in s.labels}
    default_label = s.labels[0]

    def lane_settings(label: str | None) -> Settings:
        if label is None:
            return lanes[default_label]
        if label not in lanes:
            raise HTTPException(status_code=404, detail="لا يوجد متداول بهذا الاسم.")
        return lanes[label]

    def effective(store: Store, label: str) -> Settings:
        return with_mode_override(lanes[label], store.get_state(lane_key("mode_override", label)))

    def lane_guard(store: Store, label: str) -> RiskGuard:
        return RiskGuard(store, lanes[label], Notifier(store, echo=False))

    def check_leader(leader: str | None) -> str | None:
        if leader is not None and leader not in lanes:
            raise HTTPException(status_code=404, detail="لا يوجد متداول بهذا الاسم.")
        return leader

    def lane_runtime(rt: dict[str, Any], label: str) -> dict[str, Any]:
        """The bot's heartbeat for one lane (a v2 heartbeat, from before the restart, only knows `loco`)."""
        lane_rt = (rt.get("lanes") or {}).get(label)
        if lane_rt is not None:
            return dict(lane_rt)
        return {k: rt.get(k) for k in ("equity", "available", "last_snapshot_ms")} if label == default_label else {}

    def lane_summary(store: Store, label: str, rt: dict[str, Any]) -> dict[str, Any]:
        base, eff, guard_ = lanes[label], effective(store, label), lane_guard(store, label)
        snap = store.get_state(lane_key("leader_snapshot", label)) or {}
        lrt = lane_runtime(rt, label)
        copies = store.copies(leader=label)
        real = [c for c in copies if c.status not in (CopyStatus.OBSERVED_ONLY, CopyStatus.FAILED)]
        closed = [c for c in real if c.status in CLOSED_REAL]
        observed = [c for c in copies if c.status == CopyStatus.OBSERVED_ONLY]
        wins = sum(c.realized_pnl > 0 for c in closed)
        last_event = store.recent_events(limit=1, leader=label)
        return {
            "label": label, "name": leader_name(label), "address": base.leader_address,
            "internal_label": label != DEFAULT_LABEL, "mode": mode_label(eff), "allow_execute": base.allow_execute,
            "paused": guard_.paused(), "breaker": guard_.leader_breaker_tripped(),
            "account": lrt.get("account"), "shared_account": bool(lrt.get("shared_account")),
            "equity": lrt.get("equity"), "available": lrt.get("available"),
            "snapshot_at": iso(snap.get("taken_at_ms")), "account_value": snap.get("account_value"),
            "leader_positions": len(snap.get("positions", {})),
            "open_copies": sum(c.status in (CopyStatus.OPEN, CopyStatus.SUBMITTING) for c in real),
            "observed_open": sum(c.closed_at is None for c in observed),
            "trades_total": len(real), "closed": len(closed), "wins": wins, "losses": len(closed) - wins,
            "realized_pnl": dec(sum((c.realized_pnl for c in closed), Decimal(0))),
            "observed_pnl": dec(sum((c.realized_pnl for c in observed if c.closed_at), Decimal(0))),
            "last_event_at": iso(last_event[0]["detected_at_ms"]) if last_event else None,
            "strategy": strategy_card(base),
            "leader_equity": (store.get_state(lane_key("leader_equity", label)) or {}).get("value"),
            "virtual_account": lrt.get("virtual_account"),
            "virtual": lane_ledger(store, label) if base.is_cap20 else None,
        }

    def strategy_card(base: Settings) -> dict[str, Any]:
        if base.is_cap20:
            return describe(base) | {"name": "cap20_cross"}
        return {"name": "first_fill", "risk_per_trade": dec(base.risk_per_trade), "leverage": base.leverage,
                "take_profit_move_pct": pct(base.take_profit_roi, base.leverage),
                "max_open_positions": base.max_open_positions}

    def positions_view(store: Store, label: str) -> dict[str, Any]:
        snap = store.get_state(lane_key("leader_snapshot", label)) or {}
        tracker = store.get_state(lane_key("tracker", label)) or {}
        ours = {c.coin: c for c in store.copies(open_only=True, leader=label)}
        ours |= {c.coin: c for c in store.copies_with_status(CopyStatus.OBSERVED_ONLY, leader=label)
                 if c.closed_at is None}
        positions: dict[str, str] = snap.get("positions", {})
        rows = []
        for coin in sorted(set(positions) | set(ours), key=lambda c: -abs(Decimal(positions.get(c, "0")))):
            size = Decimal(positions.get(coin, "0"))
            copy = ours.get(coin)
            rows.append({"coin": coin, "side": side_label(1 if size > 0 else -1) if size else None,
                         "size": dec(abs(size)), "entry_px": snap.get("entry_px", {}).get(coin),
                         "copyable": bool(tracker.get(coin, {}).get("synced", True)),
                         "ours": copy_dict(copy) if copy else None})
        return {"label": label, "name": leader_name(label), "address": lanes[label].leader_address,
                "snapshot_at": iso(snap.get("taken_at_ms")), "account_value": snap.get("account_value"),
                "rows": rows}

    # -- read --------------------------------------------------------------------------------------------------

    @app.get("/api/leaders")
    def leaders(_user: UserDep, store: StoreDep) -> list[dict[str, Any]]:
        rt = store.get_state("runtime") or {}
        return [lane_summary(store, label, rt) for label in lanes]

    @app.get("/api/leaders/{label}/positions")
    def leader_positions(label: str, _user: UserDep, store: StoreDep) -> dict[str, Any]:
        lane_settings(label)
        return positions_view(store, label)

    @app.get("/api/overview")
    def overview(_user: UserDep, store: StoreDep, leader: str | None = None) -> dict[str, Any]:
        """Totals for every leader together, or for one (`?leader=`)."""
        leader = check_leader(leader)
        now = now_ms()
        rt = store.get_state("runtime") or {}
        focus = leader or default_label
        snap = store.get_state(lane_key("leader_snapshot", focus)) or {}
        eff = effective(store, focus)
        copies = store.copies(leader=leader)
        real = [c for c in copies if c.status not in (CopyStatus.OBSERVED_ONLY, CopyStatus.FAILED)]
        closed = [c for c in real if c.status in CLOSED_REAL]
        day_start = now - now % DAY_MS
        today = [c for c in closed if (c.closed_at or 0) >= day_start]
        wins = sum(c.realized_pnl > 0 for c in closed)
        observed = [c for c in copies if c.status == CopyStatus.OBSERVED_ONLY]
        lrt = lane_runtime(rt, focus)
        equity = Decimal(lrt["equity"]) if lrt.get("equity") else None
        guards = {label: lane_guard(store, label) for label in ([leader] if leader else lanes)}
        m = compute(store, now - DAY_MS, leader=leader)
        return {
            "bot": {"running": bool(rt.get("updated_ms") and now - rt["updated_ms"] < 90_000),
                    "heartbeat_at": iso(rt.get("updated_ms")), "ws_connected": bool(rt.get("ws_connected")),
                    "last_frame_at": iso(rt.get("last_frame_ms")), "last_snapshot_at": iso(rt.get("last_snapshot_ms")),
                    "snapshot_failures": rt.get("snapshot_failures", 0), "ws_reconnects": rt.get("ws_reconnects", 0)},
            "account": {"broker": eff.broker, "env": eff.broker_env, "equity": lrt.get("equity"),
                        "available": lrt.get("available"), "name": lrt.get("account"),
                        "notional": lrt.get("notional"), "exposure_x": lrt.get("exposure_x"),
                        "liq_distance": lrt.get("liq_distance"), "account_kill": bool(lrt.get("account_kill"))},
            "strategy": strategy_card(lanes[focus]),
            "mode": {"mode": mode_label(eff), "live": eff.broker == "bybit" and eff.broker_env == "live"},
            "lanes": [{"label": label, "name": leader_name(label), "mode": mode_label(effective(store, label)),
                       "paused": lane_guard(store, label).paused()} for label in lanes],
            "risk": {"kill_switch": guards[focus].kill_switch_on(), "stop_file": s.stop_file.exists(),
                     "leader_breaker": any(g.leader_breaker_tripped() for g in guards.values()),
                     "daily_loss_hit": bool(equity is not None and guards[focus].daily_loss_hit(equity))},
            "leader": {"label": focus, "name": leader_name(focus), "address": lanes[focus].leader_address,
                       "account_value": snap.get("account_value"), "snapshot_at": iso(snap.get("taken_at_ms")),
                       "open_positions": len(snap.get("positions", {}))},
            "stats": {"open_copies": sum(c.status in (CopyStatus.OPEN, CopyStatus.SUBMITTING) for c in real),
                      "trades_total": len(real), "closed": len(closed), "wins": wins, "losses": len(closed) - wins,
                      "win_rate": round(100 * wins / len(closed), 2) if closed else None,
                      "realized_pnl_total": dec(sum((c.realized_pnl for c in closed), Decimal(0))),
                      "realized_pnl_today": dec(sum((c.realized_pnl for c in today), Decimal(0))),
                      "closed_today": len(today),
                      "take_profits": sum(c.status == CopyStatus.CLOSED_TP for c in closed),
                      "liquidations": sum(c.status == CopyStatus.CLOSED_LIQ for c in closed),
                      "observed_total": len(observed),
                      "observed_pnl": dec(sum((c.realized_pnl for c in observed if c.closed_at), Decimal(0)))},
            "metrics_24h": {k: (dec(v) if isinstance(v, Decimal) else v) for k, v in asdict(m).items()},
        }

    @app.get("/api/loco")
    def loco(_user: UserDep, store: StoreDep) -> dict[str, Any]:
        """v2 route: the default leader's positions (same as /api/leaders/loco/positions)."""
        return positions_view(store, default_label)

    @app.get("/api/copies")
    def copies(_user: UserDep, store: StoreDep, status: str | None = None, leader: str | None = None,
               limit: int = 200) -> list[dict[str, Any]]:
        rows = store.copies(leader=check_leader(leader))
        if status == "OPEN":
            rows = [c for c in rows if c.status in (CopyStatus.OPEN, CopyStatus.SUBMITTING)]
        elif status == "CLOSED":
            rows = [c for c in rows if c.status in CLOSED_REAL]
        elif status:
            rows = [c for c in rows if c.status == status]
        rows.sort(key=lambda c: (c.opened_at or 0, c.id or 0), reverse=True)
        return [copy_dict(c) for c in rows][: max(1, min(limit, 1000))]

    @app.get("/api/copies/{copy_id}")
    def copy_detail(copy_id: int, _user: UserDep, store: StoreDep) -> dict[str, Any]:
        c = store.copy_by_id(copy_id)
        if c is None:
            raise HTTPException(status_code=404, detail="الصفقة غير موجودة.")
        return copy_detail_dict(store, lanes.get(c.leader, s), c)

    @app.get("/api/events")
    def events(_user: UserDep, store: StoreDep, decision: str | None = None, leader: str | None = None,
               limit: int = 200) -> list[dict[str, Any]]:
        leader = check_leader(leader)
        by_episode = {(c.leader, c.coin, c.episode_id): c for c in store.copies(leader=leader)}
        return [event_dict(e, by_episode.get((e["leader"], e["coin"], e["episode_id"])))
                for e in store.recent_events(limit=max(1, min(limit, 1000)), decision=decision, leader=leader)]

    @app.get("/api/events/{event_id}")
    def event_detail(event_id: int, _user: UserDep, store: StoreDep) -> dict[str, Any]:
        e = store.event_by_id(event_id)
        if e is None:
            raise HTTPException(status_code=404, detail="الحركة غير موجودة.")
        copy = store.get_copy(e["coin"], e["episode_id"], leader=e["leader"])
        fill = store.fill_by_tid(e["tid"], leader=e["leader"])
        return {"event": event_dict(e, copy), "fill": fill_dict(fill), "leader_order": leader_order(store, fill),
                "copy": copy_dict(copy) if copy else None}

    @app.get("/api/logs")
    def logs(_user: UserDep, store: StoreDep, level: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return [{"id": r["id"], "ts": iso(r["ts"]), "level": r["level"], "type": r["type"],
                 "payload": json.loads(r["payload"])}
                for r in store.recent_logs(limit=max(1, min(limit, 1000)), min_level=level)]

    @app.get("/api/analytics")
    def analytics(_user: UserDep, store: StoreDep, days: int = 30, leader: str | None = None) -> dict[str, Any]:
        leader = check_leader(leader)
        since = now_ms() - max(1, min(days, 3650)) * DAY_MS
        closed = sorted((c for c in store.copies(leader=leader)
                         if c.status in CLOSED_REAL and (c.closed_at or 0) >= since), key=lambda c: c.closed_at or 0)
        curve, running = [], Decimal(0)
        daily: dict[str, dict[str, Any]] = {}
        for c in closed:
            running += c.realized_pnl
            curve.append({"closed_at": iso(c.closed_at), "coin": c.coin, "leader": c.leader,
                          "pnl": dec(c.realized_pnl), "cumulative": dec(running)})
            day = datetime.fromtimestamp((c.closed_at or 0) / 1000, UTC).strftime("%Y-%m-%d")
            d = daily.setdefault(day, {"day": day, "trades": 0, "wins": 0, "losses": 0, "pnl": Decimal(0)})
            d["trades"] += 1
            d["wins" if c.realized_pnl > 0 else "losses"] += 1
            d["pnl"] += c.realized_pnl
        m = compute(store, since, leader=leader)
        return {"days": days, "leader": leader, "curve": curve,
                "daily": [{**d, "pnl": dec(d["pnl"])} for d in daily.values()],
                "by_close": {st: sum(c.status == st for c in closed) for st in CLOSED_REAL},
                "metrics": {k: (dec(v) if isinstance(v, Decimal) else v) for k, v in asdict(m).items()}}

    @app.get("/api/virtual")
    def virtual(_user: UserDep, store: StoreDep, days: int = 28) -> dict[str, Any]:
        """The v5 trio panel (botup.md §V5.5 / §V5.6): the virtual cross account, its equity curve, each lane's
        observed trades, latency / slippage of every leader fill, and the go / no-go checks."""
        now = now_ms()
        since = now - max(1, min(days, 365)) * DAY_MS
        rt = store.get_state("runtime") or {}
        accounts: dict[str, Any] = rt.get("virtual") or {}
        name = "virtual" if "virtual" in accounts else next((n for n in accounts if n.startswith("virtual")), None)
        acct = accounts.get(name) if name else None
        labels = (acct or {}).get("lanes") or [x for x in lanes if lanes[x].is_cap20]
        st = observe_stats(store, labels, since)
        curve = [{"t": iso(ts), "equity": dec(eq)} for ts, eq in store.equity_history(name or "virtual", since)]
        opened = [c for x in labels for c in store.copies(leader=x)
                  if c.strategy == "cap20_cross" and c.status == CopyStatus.OBSERVED_ONLY]
        open_now = sorted((c for c in opened if c.closed_at is None), key=lambda c: c.opened_at or 0, reverse=True)
        recent = [e for e in store.recent_events(limit=400) if e["leader"] in labels][:40]
        by_episode = {(c.leader, c.coin, c.episode_id): c for c in opened}
        missed = sum(1 for r in store.logs_since(since)
                     if r["type"] == "MISSED_EXIT_AUTO_CLOSED" and json.loads(r["payload"]).get("leader") in labels)
        errors = sum(1 for r in store.logs_since(since, min_level="ERROR")
                     if json.loads(r["payload"]).get("leader") in (*labels, None))
        trades = sum(1 for c in opened if (c.opened_at or 0) >= since)
        p95, slip = st["latency_p95_ms"], st["slippage_median_bps"]
        checks = [
            {"key": "missed_exits", "value": missed, "pass": missed == 0},
            {"key": "latency_p95", "value": p95, "pass": None if p95 is None else p95 <= 10_000},
            {"key": "median_slippage", "value": slip, "pass": None if slip is None else slip <= 15},
            {"key": "errors", "value": errors, "pass": errors == 0},
            {"key": "trades_observed", "value": trades, "pass": trades >= 10},
            {"key": "replay_match", "value": None, "pass": None},  # weekly offline replay (analysis365.multi)
        ]
        ages = [float(now - c.opened_at) for c in opened if c.opened_at]
        return {
            "account": acct, "name": name, "days": days, "curve": curve,
            "lanes": [{"label": x, "name": leader_name(x), "mode": mode_label(effective(store, x)),
                       "paused": lane_guard(store, x).paused(), "address": lanes[x].leader_address,
                       "leader_equity": (store.get_state(lane_key("leader_equity", x)) or {}).get("value"),
                       **lane_ledger(store, x, since), "counts": st["lanes"].get(x, {})} for x in labels],
            "stats": st | {"oldest_trade_age_ms": max(ages) if ages else None,
                           "median_trade_age_ms": percentile(ages, 0.5)},
            "open": [copy_dict(c) for c in open_now],
            "recent": [event_dict(e, by_episode.get((e["leader"], e["coin"], e["episode_id"]))) for e in recent],
            "checks": checks,
        }

    @app.get("/api/portfolio")
    def portfolio(_user: UserDep, store: StoreDep, days: int = 28, capital: int | None = None,
                  group: str = "trio", leverage: int | None = None) -> dict[str, Any]:
        """One simulator section on Hyperliquid (botup.md §V6; sections §V10): for one starting balance, both
        ledgers (base / real), their equity curves, open positions and closed trades; every fill of the section's
        wallets with our price and latency; a summary of every balance; and the go / no-go checks."""
        rt = store.get_state("portfolio_runtime")
        if not rt or "capitals" not in rt:
            return {"enabled": False}
        groups = pf_groups(rt)
        g = next((x for x in groups if x["name"] == group), groups[0])
        gname = g["name"]
        capitals: list[int] = rt["capitals"]
        cap = capital if capital in capitals else capitals[0]
        lev, suffix, levs = pf_lev(rt, leverage)
        akey = gname if not suffix else f"{gname}:x{lev}"
        names = {sc: pf_ledger_name(sc, cap, gname, suffix) for sc in ("base", "real")}
        now = now_ms()
        since = now - max(1, min(days, 365)) * DAY_MS
        curves = {sc: [{"t": iso(ts), "equity": float(eq)} for ts, eq in
                       store.equity_history(f"portfolio:{name}", since)] for sc, name in names.items()}
        events = []
        for e in store.pf_events_since(since):
            a = pf_actions(e, akey)
            if a is not None:
                events.append((e, a))
        ledgers = rt.get("ledgers") or {}
        real = ledgers.get(names["real"]) or {}
        base = ledgers.get(names["base"]) or {}
        started = int(real.get("started_ms") or g.get("started_ms") or rt.get("started_ms") or now)
        # The bot stopped (server reboot): a fill the trader made then is caught on restart, hours or days late.
        # Latency, stale moves and run time measure the bot while it runs, so those fills are counted apart.
        down = outages([ts for ts, _ in store.equity_history(f"portfolio:{names['real']}", started)], started, now)
        live = [(e, a) for e, a in events if not in_outage(e["leader_time_ms"], down)]
        lat = [float(e["detected_at_ms"] - e["leader_time_ms"]) for e, _ in live]
        slip: list[float] = []
        recent = []
        for i, (e, a) in enumerate(events):
            bps = None
            if e["real_px"] and e["px"] and not in_outage(e["leader_time_ms"], down):
                bps = (1 if e["side"] == "B" else -1) * (e["real_px"] / e["px"] - 1) * 10_000
                if abs(bps) < 5000:
                    slip.append(bps)
                else:
                    bps = None
            if i < 60:
                recent.append({"id": e["id"], "wallet": e["wallet"], "coin": e["coin"], "side": e["side"],
                               "px": e["px"], "sz": e["sz"], "start_pos": e["start_pos"],
                               "leader_time": iso(e["leader_time_ms"]), "detected_at": iso(e["detected_at_ms"]),
                               "lag_ms": e["detected_at_ms"] - e["leader_time_ms"], "real_px": e["real_px"],
                               "slippage_bps": bps, "base_action": a[0], "base_qty": a[1],
                               "real_action": a[2], "real_qty": a[3],
                               "during_outage": in_outage(e["leader_time_ms"], down)})
        trades = {sc: [dict(r) for r in store.pf_trades_since(since, ledger=name)] for sc, name in names.items()}
        orders = {sc: [{**dict(r), "time": iso(r["ts"])} for r in store.pf_orders_since(since, ledger=name)]
                  for sc, name in names.items()}
        fees = {sc: sum(o["fee"] for o in rows) for sc, rows in orders.items()}
        by_wallet: dict[str, dict[str, dict[str, Any]]] = {}
        for name, rows in trades.items():
            for r in rows:
                w = by_wallet.setdefault(r["wallet"], {}).setdefault(
                    name, {"trades": 0, "wins": 0, "pnl": 0.0, "fees": 0.0, "liquidated": 0})
                w["trades"] += 1
                w["wins"] += (r["pnl"] or 0) > 0
                w["pnl"] += r["pnl"] or 0.0
                w["fees"] += r["fees"] or 0.0
                w["liquidated"] += r["reason"] == "LIQUIDATED"
        fills_by_wallet: dict[str, int] = defaultdict(int)
        for e, _ in events:
            fills_by_wallet[e["wallet"]] += 1
        missed = sum(1 for r in store.logs_since(since) if r["type"] == "PF_MISSED_EXIT_CLOSED")
        stale = sum(1 for _, a in live if a[2] == "SKIP_STALE")
        outage_ms = sum(b - a for a, b in down)
        run_days = (now - started - outage_ms) / DAY_MS
        gap = None
        if base.get("equity") and real.get("equity") is not None and base.get("start_equity"):
            gap = (float(real["equity"]) - float(base["equity"])) / float(base["start_equity"]) * 100
        checks = [
            {"key": "days", "value": run_days, "pass": run_days >= 14},
            {"key": "max_dd", "value": real.get("max_drawdown_pct"),
             "pass": None if real.get("max_drawdown_pct") is None else real["max_drawdown_pct"] < 20},
            {"key": "liquidations", "value": real.get("liquidations", 0), "pass": not real.get("liquidations")},
            {"key": "missed_exits", "value": missed, "pass": missed == 0},
            {"key": "stale", "value": stale, "pass": stale == 0},
            {"key": "real_vs_base", "value": gap, "pass": None},
        ]
        trades_out = {name: [{**r, "opened": iso(r["opened_ms"]), "closed": iso(r["closed_ms"])} for r in rows[:80]]
                      for name, rows in trades.items()}
        expected = []
        if suffix:  # the research numbers are for the base leverage only
            pass
        elif gname == "trio":
            for part in s.portfolio_expected.split(";"):
                label, _, growth = part.rpartition(":")
                if label and growth.strip():
                    expected.append({"label": label.strip(), "year": float(growth)})
        else:  # the section's own research numbers (research section, hunt2)
            if g.get("research_year"):
                y = float(g["research_year"])
                expected.append({"label": f"الاختبار الواقعي لسنة ({(y - 1) * 100:+,.0f}%)", "year": y})
            if g.get("research_half"):
                h = float(g["research_half"])
                expected.append({"label": f"متحفظ — نصف أفضلية المتداولين ({(h - 1) * 100:+,.0f}%)", "year": h})
        summary = [{"capital": c, **{sc: {k: (ledgers.get(pf_ledger_name(sc, c, gname, suffix)) or {}).get(k) for k in
                                          ("equity", "return_pct", "max_drawdown_pct", "trades", "win_rate",
                                           "liquidations", "open_trades", "exposure_x")}
                                     for sc in ("base", "real")}} for c in capitals]
        base_lev = pf_lev(rt, None)[0]
        by_lev = [{"leverage": x, "capital": c, **{k: (ledgers.get(pf_ledger_name(
            "real", c, gname, 0 if x == base_lev else x)) or {}).get(k) for k in
            ("equity", "return_pct", "max_drawdown_pct", "trades", "liquidations", "open_trades")}}
            for x in levs for c in capitals]  # every leverage x balance of this section, real scenario (§V13)
        return {
            "enabled": True, "days": days, "capital": cap, "capitals": capitals, "summary": summary,
            "group": g, "groups": groups, "leverage": lev, "leverages": levs,
            # every leverage x balance of this section, real scenario (§V13)
            "by_leverage": by_lev,
            "ledger": {"base": base, "real": real},
            "runtime": {k: v for k, v in rt.items() if k != "ledgers"} | {
                "started_ms": started,
                "wallets": [w | {"real_pnl": (w.get("real_pnl") or {}).get(gname)}
                            for w in rt.get("wallets") or [] if w["label"] in g["labels"]]},
            "run_days": run_days, "curves": curves, "expected": expected, "orders": orders, "fees_in_period": fees,
            "stats": {"events": len(events), "latency_p50_ms": percentile(lat, 0.5),
                      "latency_p95_ms": percentile(lat, 0.95), "slippage_median_bps": percentile(slip, 0.5),
                      "slippage_samples": len(slip), "stale": stale, "missed_exits": missed,
                      # fills made while the bot was stopped: left out of latency / slippage / stale above
                      "outage_events": len(events) - len(live)},
            "outages": [{"start": iso(a), "end": iso(b)} for a, b in down], "outage_days": outage_ms / DAY_MS,
            "by_wallet": by_wallet, "fills_by_wallet": fills_by_wallet, "trades": trades_out, "recent": recent,
            "checks": checks,
        }

    @app.get("/api/portfolio/outage")
    def portfolio_outage(_user: UserDep, store: StoreDep, capital: int | None = None, group: str = "trio",
                         leverage: int | None = None) -> dict[str, Any]:
        """What-if for the longest bot stop: every fill the section's wallets made from the moment the bot stopped
        until now, replayed in time order through a fresh real-scenario ledger as if the bot had never stopped
        (the trader's own price + the real fee, today's wallet balances for sizing). Positions the traders held
        before the stop are left alone until they close, as in the live run. Pure simulation, nothing stored."""
        rt = store.get_state("portfolio_runtime")
        if not rt or "capitals" not in rt:
            return {"enabled": False}
        groups = pf_groups(rt)
        g = next((x for x in groups if x["name"] == group), groups[0])
        capitals: list[int] = rt["capitals"]
        cap = capital if capital in capitals else capitals[0]
        lev, suffix, _levs = pf_lev(rt, leverage)
        now = now_ms()
        real_name = pf_ledger_name("real", cap, g["name"], suffix)
        started = int(((rt.get("ledgers") or {}).get(real_name) or {}).get("started_ms") or g.get("started_ms") or now)
        down = outages([ts for ts, _ in store.equity_history(f"portfolio:{real_name}", started)], started, now)
        if not down:
            return {"enabled": True, "outage": None}
        ended = [x for x in down if x[1] < now] or down  # a stop still going on has nothing to replay yet
        start, end = max(ended, key=lambda x: x[1] - x[0])  # the long stop, not a restart of a few minutes
        st = rt.get("settings") or {}
        base_lev = int(st.get("leverage") or 3)
        ledger = Ledger(f"outage:{real_name}", start_equity=float(cap), leverage=lev,
                        fee=float(st.get("fee_real", 0.00095)),
                        cap_pct=float(st.get("cap_pct", 20.0)), min_order=float(st.get("min_order", 10.0)),
                        max_total_x=float(st.get("max_total_x", 6.0)) * lev / base_lev,
                        dd_pause_pct=float(st.get("dd_pause_pct") or 0) or None,
                        dd_pause_days=float(st.get("dd_pause_days", 7.0)), started_ms=start)
        wallets = {w["label"]: w for w in rt.get("wallets") or [] if w["label"] in g["labels"]}
        fills = sorted((e for e in store.pf_events_since(start) if e["wallet"] in wallets
                        and e["leader_time_ms"] >= start), key=lambda e: (e["leader_time_ms"], e["id"]))
        seen: set[tuple[str, int]] = set()
        n = 0
        for e in fills:
            if (e["wallet"], e["tid"]) in seen:
                continue
            seen.add((e["wallet"], e["tid"]))
            w = wallets[e["wallet"]]
            d = e["sz"] if e["side"] == "B" else -e["sz"]
            ledger.on_fill(e["wallet"], float(w["weight"]), e["coin"], e["start_pos"], d, e["px"], e["leader_time_ms"],
                           float(w.get("equity") or 0))
            n += 1
        return {
            "enabled": True, "capital": cap, "leverage": lev, "group": g["name"],
            "outage": {"start": iso(start), "end": iso(end), "days": (end - start) / DAY_MS},
            "until": iso(now), "fills": n, "summary": ledger.summary(now), "positions": ledger.positions(),
            "trades": [{**t, "opened": iso(t.get("opened_ms")), "closed": iso(t.get("closed_ms"))}
                       for t in reversed(ledger.closed)],
            "orders": [{**o, "time": iso(o["ts"])} for o in reversed(ledger.orders)][:300],
        }

    @app.get("/api/portfolio/chart")
    def portfolio_chart(_user: UserDep, store: StoreDep, wallet: str, coin: str, capital: int | None = None,
                        scenario: Literal["real", "base"] = "real", days: int = 7,
                        group: str = "trio", leverage: int | None = None) -> dict[str, Any]:
        """The live trade chart (botup.md §V7): for one trader + coin, every fill of the trader (where he opened,
        added, reduced, closed) and every order of ours on that ledger (price, size, fee), plus our open position,
        its average entry and the approximate liquidation price of the account if only this coin moved.
        Candles are not here: the dashboard reads them from Hyperliquid directly (REST + WebSocket)."""
        rt = store.get_state("portfolio_runtime") or {}
        capitals: list[int] = rt.get("capitals") or []
        if not capitals:
            return {"enabled": False}
        cap = capital if capital in capitals else capitals[0]
        gname = next((x["name"] for x in pf_groups(rt) if x["name"] == group), "trio")
        lev, suffix, _ = pf_lev(rt, leverage)
        name = pf_ledger_name(scenario, cap, gname, suffix)
        since = now_ms() - max(1, min(days, 90)) * DAY_MS
        ledger = (rt.get("ledgers") or {}).get(name) or {}
        pos = next((p for p in ledger.get("positions") or [] if p["wallet"] == wallet and p["coin"] == coin), None)
        liq = None
        if pos and ledger.get("equity") is not None:
            q, m, mlev = float(pos["qty"]), float(pos["mark"]), float(pos.get("maxlev") or 10)
            other_mm = float(ledger.get("maintenance") or 0) - q * m / (2 * mlev)
            eq = float(ledger["equity"])
            if pos["side"] < 0:  # equity - q (p - m) = other_mm + q p / 2L
                liq = (eq + q * m - other_mm) / (q * (1 + 1 / (2 * mlev)))
            else:  # equity + q (p - m) = other_mm + q p / 2L
                liq = (q * m - eq + other_mm) / (q * (1 - 1 / (2 * mlev)))
            liq = liq if liq > 0 else None
        fills = []
        for e in store.pf_events_for(wallet, coin, since):
            s0 = e["start_pos"]
            s1 = s0 + (e["sz"] if e["side"] == "B" else -e["sz"])
            kind = ("OPEN" if abs(s0) < 1e-12 else "CLOSE" if abs(s1) < 1e-9 else
                    "FLIP" if (s0 > 0) != (s1 > 0) else "ADD" if abs(s1) > abs(s0) else "REDUCE")
            a = pf_actions(e, gname if not suffix else f"{gname}:x{lev}")
            fills.append({"t": e["leader_time_ms"], "side": e["side"], "px": e["px"], "sz": e["sz"], "kind": kind,
                          "pos_after": s1, "our_action": (a[2] if scenario == "real" else a[0]) if a else None})
        orders = [{"t": o["ts"], "kind": o["kind"], "qty": o["qty"], "px": o["px"], "notional": o["notional"],
                   "fee": o["fee"]} for o in store.pf_orders_for(name, wallet, coin, since)]
        return {"enabled": True, "wallet": wallet, "coin": coin, "capital": cap, "scenario": scenario,
                "group": gname, "ledger": name, "position": pos, "liq_price": liq, "fills": fills, "orders": orders,
                "updated_ms": rt.get("updated_ms")}

    @app.get("/api/live")
    async def live(request: Request, _user: UserDep) -> StreamingResponse:
        """Server-Sent Events (botup.md §V11): one small message whenever the bot writes something new -- the
        dashboard refetches exactly what changed instead of polling every 15 s. Heartbeat comment every 15 s
        (keeps the Cloudflare tunnel and any proxy from closing an idle stream); the session is re-checked
        every minute, so a logout or an expired session ends the stream."""
        token = request.cookies.get(COOKIE) or ""

        async def events() -> Any:
            store = Store(s.db_path, cross_thread=True)
            last: dict[str, Any] | None = None
            loop = asyncio.get_running_loop()
            beat = auth = loop.time()
            try:
                yield "retry: 2000\n\n"
                while not await request.is_disconnected():
                    now = loop.time()
                    if now - auth >= LIVE_AUTH_SECONDS:
                        auth = now
                        if store.session_user(token_hash(token)) is None:
                            yield "event: auth\ndata: {}\n\n"
                            return
                    snap = live_snapshot(store)
                    if snap != last:
                        last, beat = snap, now
                        yield f"data: {json.dumps(snap)}\n\n"
                    elif now - beat >= LIVE_HEARTBEAT_SECONDS:
                        beat = now
                        yield ": ping\n\n"
                    await asyncio.sleep(LIVE_POLL_SECONDS)
            finally:
                store.close()

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})

    @app.get("/api/portfolio/compare")
    def portfolio_compare(_user: UserDep, store: StoreDep, capital: int | None = None,
                          leverage: int | None = None) -> dict[str, Any]:
        """botup.md §V10: every simulator section side by side -- each since its own start, and all of them from
        the common start (the latest section start) so the comparison is on the same trades."""
        rt = store.get_state("portfolio_runtime")
        if not rt or "capitals" not in rt:
            return {"enabled": False}
        groups = pf_groups(rt)
        capitals: list[int] = rt["capitals"]
        cap = capital if capital in capitals else capitals[0]
        lev, suffix, levs = pf_lev(rt, leverage)
        ledgers = rt.get("ledgers") or {}
        common = max(int(g.get("started_ms") or 0) for g in groups)
        if suffix:  # a leverage added later starts later than its section
            common = max([common] + [int((ledgers.get(pf_ledger_name("real", cap, g["name"], suffix)) or {})
                                         .get("started_ms") or 0) for g in groups])
        now = now_ms()
        keys = ("equity", "return_pct", "max_drawdown_pct", "drawdown_pct", "trades", "wins", "win_rate",
                "liquidations", "open_trades", "exposure_x", "fees_paid", "notional", "pauses", "per_wallet")
        out = []
        for g in groups:
            row: dict[str, Any] = {k: g.get(k) for k in ("name", "title", "labels", "started_ms", "research_year",
                                                         "research_half")}
            row["days"] = (now - int(g.get("started_ms") or now)) / DAY_MS
            row["capitals"] = {str(c): {sc: {k: (ledgers.get(pf_ledger_name(sc, c, g["name"], suffix)) or {}).get(k)
                                            for k in keys} for sc in ("base", "real")} for c in capitals}
            row["curves"] = {}
            row["since_common"] = {}
            for sc in ("base", "real"):
                name = pf_ledger_name(sc, cap, g["name"], suffix)
                pts = store.equity_history(f"portfolio:{name}", int(g.get("started_ms") or 0))
                row["curves"][sc] = [{"t": iso(ts), "ms": ts, "equity": float(eq)} for ts, eq in pts]
                at = next((float(eq) for ts, eq in pts if ts >= common), None)
                cur = (ledgers.get(name) or {}).get("equity")
                row["since_common"][sc] = (float(cur) / at - 1) * 100 if at and cur is not None else None
            out.append(row)
        return {"enabled": True, "capital": cap, "capitals": capitals, "leverage": lev, "leverages": levs,
                "common_start_ms": common,
                "updated_ms": rt.get("updated_ms"), "groups": out}

    @app.get("/api/portfolio/advice")
    def portfolio_advice(_user: UserDep, store: StoreDep, wallet: str, coin: str) -> dict[str, Any]:
        """The AI advisor for one open trade (botup.md §V8): the live numbers (probability, risk, market, the
        trader's record), Claude's latest written analysis of this trade, and its exit alerts."""
        live_all = store.get_state("advisor_live") or {}
        live = (live_all.get("trades") or {}).get(f"{wallet}|{coin}")
        t0 = int(live["t0"]) if live else None
        row = store.pf_latest_analysis(wallet, coin, with_ai=True)
        last = store.pf_latest_analysis(wallet, coin)
        if t0 is not None:
            row = row if row is not None and row["trade_t0"] == t0 else None
            last = last if last is not None and last["trade_t0"] == t0 else None
        alerts = [dict(a) | {"time": iso(a["ts"])} for a in store.pf_alerts_for(wallet, coin, t0 or 0)]
        req = store.get_state(f"advisor_request:{wallet}|{coin}") or {}
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        return {
            "enabled": bool(live_all), "ai_enabled": bool(live_all.get("ai_enabled")),
            "updated_ms": live_all.get("updated_ms"), "live": live,
            "analysis": None if row is None else {
                "time": iso(row["ts"]), "kind": row["kind"], "ai": json.loads(row["ai"]),
                "meta": json.loads(row["meta"] or "{}"), "probability": row["probability"],
                "risk_score": row["risk_score"]},
            "last_attempt": None if last is None else {"time": iso(last["ts"]), "kind": last["kind"],
                                                       "meta": json.loads(last["meta"] or "{}")},
            "alerts": alerts, "pending": bool(req) and not req.get("done"),
            "calls_today": store.get_state(f"advisor_calls:{day}") or 0, "max_calls": s.advisor_max_daily_calls,
        }

    @app.get("/api/portfolio/alerts")
    def portfolio_alerts(_user: UserDep, store: StoreDep, hours: int = 24) -> list[dict[str, Any]]:
        since = now_ms() - max(1, min(hours, 24 * 30)) * 3_600_000
        return [dict(a) | {"time": iso(a["ts"])} for a in store.pf_alerts_since(since)]

    @app.post("/api/portfolio/advice/refresh")
    def portfolio_advice_refresh(body: AdviceBody, user: UserDep, store: StoreDep) -> dict[str, Any]:
        """Ask the bot for a new Claude analysis of this trade now (picked up within ~5 s)."""
        store.set_state(f"advisor_request:{body.wallet}|{body.coin}",
                        {"at": now_ms(), "done": False, "by": user.get("username")})
        return {"ok": True}

    @app.get("/api/research")
    def research(_user: UserDep) -> dict[str, Any]:
        """botup.md §V9: the research section -- the latest export of the research project (top 10 wallets after the
        tests, mixes, reports), imported with `loco-copy research-import`."""
        p = s.data_dir / "research" / "latest.json"
        if not p.exists():
            return {"available": False}
        data = json.loads(p.read_text(encoding="utf-8"))
        hist = sorted((x.name for x in (s.data_dir / "research").glob("research_*.json")), reverse=True)
        return {"available": True, "imported_ms": int(p.stat().st_mtime * 1000), "history": hist, **data}

    @app.get("/api/settings")
    def settings_view(_user: UserDep, store: StoreDep) -> dict[str, Any]:
        base = lanes[default_label]
        return {
            "leader_address": base.leader_address, "broker": s.broker, "broker_env": s.broker_env,
            "live": s.broker == "bybit" and s.broker_env == "live", "mode": mode_label(effective(store, default_label)),
            "leaders": [{"label": label, "name": leader_name(label), "address": lanes[label].leader_address,
                         "mode": mode_label(effective(store, label)), "allow_execute": lanes[label].allow_execute}
                        for label in lanes],
            "strategy": {"risk_per_trade": dec(base.risk_per_trade), "leverage": base.leverage,
                         "take_profit_roi": dec(base.take_profit_roi),
                         "take_profit_move_pct": pct(base.take_profit_roi, base.leverage),
                         "liquidation_move_pct": dec((Decimal(1) / base.leverage - base.maintenance_margin_rate) * 100),
                         "follow_adds": base.follow_adds, "follow_reduces": base.follow_reduces,
                         "max_open_positions": base.max_open_positions,
                         "max_notional_per_coin": dec(base.max_notional_per_coin),
                         "allow_long": base.allow_long, "allow_short": base.allow_short,
                         "coin_allowlist": sorted(base.allowlist), "coin_blocklist": sorted(base.blocklist)},
            "cap20": strategy_card(base) if base.is_cap20 else None,
            "lane_strategies": {label: strategy_card(lanes[label]) for label in lanes},
            "safety": {"max_entry_drift_pct": pct(base.max_entry_drift),
                       "max_signal_age_ms": base.max_signal_age_ms,
                       "max_order_slippage_pct": dec(base.max_order_slippage * 100),
                       "daily_loss_limit_pct": pct(base.daily_loss_limit_pct),
                       "leader_dd_breaker_pct": pct(base.leader_dd_breaker_pct),
                       "missed_exit_seconds": base.missed_exit_seconds},
        }

    # -- control -----------------------------------------------------------------------------------------------

    @app.post("/api/control/mode")
    def set_mode(body: ModeBody, user: UserDep, store: StoreDep) -> dict[str, Any]:
        label = body.leader or default_label
        lane = lane_settings(label)
        if body.mode == "EXECUTE" and not lane.allow_execute:
            raise HTTPException(status_code=400, detail=(
                f"التنفيذ الفعلي مقفل لهذا المتداول من السيرفر (ALLOW_EXECUTE__{label.upper()}). "
                "يُفتح فقط بعد قرار الاستراتيجية — راجع botup.md (D-1)."))
        if body.mode == "EXECUTE" and (body.confirm or "").strip() != EXECUTE_CONFIRM:
            raise HTTPException(status_code=400, detail=f'التنفيذ الفعلي يتطلب كتابة "{EXECUTE_CONFIRM}" للتأكيد.')
        store.set_state(lane_key("mode_override", label),
                        {"copy_enabled": body.mode != "WATCH", "observe_only": body.mode != "EXECUTE"})
        Notifier(store, echo=False).warning("MODE_REQUESTED", leader=label, mode=body.mode, user=user["username"])
        return {"leader": label, "mode": body.mode}

    @app.post("/api/leaders/{label}/active")
    def set_active(label: str, body: ActiveBody, user: UserDep, store: StoreDep) -> dict[str, Any]:
        """The leader's on/off switch: off = no new copies of this leader; his exits are still followed."""
        lane_settings(label)
        lane_guard(store, label).set_paused(not body.active, by=user["username"])
        return {"leader": label, "active": body.active}

    @app.post("/api/control/kill")
    def kill(body: KillBody, user: UserDep, store: StoreDep) -> dict[str, Any]:
        lane_guard(store, default_label).engage_kill(f"dashboard: {user['username']}")
        s.stop_file.parent.mkdir(parents=True, exist_ok=True)
        s.stop_file.touch()
        if body.close_all:
            store.set_state("close_all_requested", True)
        rt = store.get_state("runtime") or {}
        return {"kill_switch": True, "close_all_requested": body.close_all,
                "bot_running": bool(rt.get("updated_ms") and now_ms() - rt["updated_ms"] < 90_000)}

    @app.post("/api/control/resume")
    def resume(user: UserDep, store: StoreDep) -> dict[str, Any]:
        release_everything(store, s, Notifier(store, echo=False))
        Notifier(store, echo=False).warning("RESUMED_FROM_DASHBOARD", user=user["username"])
        return {"kill_switch": False}

    return app


def serve(settings: Settings) -> None:
    import uvicorn  # noqa: PLC0415

    from loco_copy.notify import configure_logging  # noqa: PLC0415

    configure_logging(settings.log_dir, filename="api.log")
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", log_config=None)
