"""Dashboard API (`loco-copy api`): a separate process from the bot, so nothing here can ever block or crash
trading. It reads the bot's SQLite (WAL) and writes only three things, all through the `state` table that the
bot already polls: the kill switch, a close-all request, and the watch/observe/execute mode.

Listens on 127.0.0.1 only; the Cloudflare tunnel is the only way in. Logins are cookie sessions
(HttpOnly + Secure + SameSite=None, because the dashboard on Vercel is a different site).
"""

import json
import sqlite3
import time
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from loco_copy.auth import new_token, token_hash, verify_password
from loco_copy.config import DEFAULT_LABEL, Settings, get_settings, with_mode_override
from loco_copy.constants import CopyStatus, EventType
from loco_copy.metrics import compute
from loco_copy.models import Copy
from loco_copy.notify import Notifier
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
        "latency_order_ms": c.latency_order_ms, "leader": c.leader,
    }


def event_dict(e: sqlite3.Row, copy: Copy | None) -> dict[str, Any]:
    return {"id": e["id"], "coin": e["coin"], "episode_id": e["episode_id"], "type": e["type"],
            "side": side_label(e["side"]) if e["side"] else None, "fraction": e["fraction"],
            "leader_px": e["leader_px"], "leader_time": iso(e["leader_time_ms"]),
            "detected_at": iso(e["detected_at_ms"]), "lag_ms": e["detected_at_ms"] - e["leader_time_ms"],
            "decision": e["decision"], "rejection": e["rejection"], "detail": e["detail"],
            "copy_id": copy.id if copy else None, "copy_status": copy.status if copy else None,
            "leader": e["leader"]}


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
            "strategy": {"risk_per_trade": dec(base.risk_per_trade), "leverage": base.leverage,
                         "take_profit_move_pct": dec(base.take_profit_roi / base.leverage * 100),
                         "max_open_positions": base.max_open_positions},
        }

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
                        "available": lrt.get("available")},
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
                         "take_profit_move_pct": dec(base.take_profit_roi / base.leverage * 100),
                         "liquidation_move_pct": dec((Decimal(1) / base.leverage - base.maintenance_margin_rate) * 100),
                         "follow_adds": base.follow_adds, "follow_reduces": base.follow_reduces,
                         "max_open_positions": base.max_open_positions,
                         "max_notional_per_coin": dec(base.max_notional_per_coin),
                         "allow_long": base.allow_long, "allow_short": base.allow_short,
                         "coin_allowlist": sorted(base.allowlist), "coin_blocklist": sorted(base.blocklist)},
            "safety": {"max_entry_drift_pct": dec(base.max_entry_drift * 100),
                       "max_signal_age_ms": base.max_signal_age_ms,
                       "max_order_slippage_pct": dec(base.max_order_slippage * 100),
                       "daily_loss_limit_pct": dec(base.daily_loss_limit_pct * 100),
                       "leader_dd_breaker_pct": dec(base.leader_dd_breaker_pct * 100),
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
