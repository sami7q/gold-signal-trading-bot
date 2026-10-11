"""Dashboard API (`loco-copy api`): a separate process from the bot, so nothing here can ever block or crash
trading. It reads the bot's SQLite (WAL) and writes only three things, all through the `state` table that the
bot already polls: the kill switch, a close-all request, and the watch/observe/execute mode.

Listens on 127.0.0.1 only; the Cloudflare tunnel is the only way in. Logins are cookie sessions
(HttpOnly + Secure + SameSite=None, because the dashboard on Vercel is a different site).
"""

import json
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
from loco_copy.config import Settings, get_settings, with_mode_override
from loco_copy.constants import CopyStatus
from loco_copy.metrics import compute
from loco_copy.models import Copy
from loco_copy.notify import Notifier
from loco_copy.risk import RiskGuard
from loco_copy.store import Store, now_ms

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
        "latency_order_ms": c.latency_order_ms,
    }


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

    def guard(store: Store) -> RiskGuard:
        return RiskGuard(store, s, Notifier(store, echo=False))

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

    # -- read --------------------------------------------------------------------------------------------------

    @app.get("/api/overview")
    def overview(_user: UserDep, store: StoreDep) -> dict[str, Any]:
        now = now_ms()
        rt = store.get_state("runtime") or {}
        snap = store.get_state("leader_snapshot") or {}
        eff = with_mode_override(s, store.get_state("mode_override"))
        risk = guard(store)
        copies = store.copies()
        real = [c for c in copies if c.status not in (CopyStatus.OBSERVED_ONLY, CopyStatus.FAILED)]
        closed = [c for c in real if c.status in CLOSED_REAL]
        day_start = now - now % DAY_MS
        today = [c for c in closed if (c.closed_at or 0) >= day_start]
        wins = sum(c.realized_pnl > 0 for c in closed)
        observed = [c for c in copies if c.status == CopyStatus.OBSERVED_ONLY]
        equity = Decimal(rt["equity"]) if rt.get("equity") else None
        m = compute(store, now - DAY_MS)
        return {
            "bot": {"running": bool(rt.get("updated_ms") and now - rt["updated_ms"] < 90_000),
                    "heartbeat_at": iso(rt.get("updated_ms")), "ws_connected": bool(rt.get("ws_connected")),
                    "last_frame_at": iso(rt.get("last_frame_ms")), "last_snapshot_at": iso(rt.get("last_snapshot_ms")),
                    "snapshot_failures": rt.get("snapshot_failures", 0), "ws_reconnects": rt.get("ws_reconnects", 0)},
            "account": {"broker": eff.broker, "env": eff.broker_env, "equity": rt.get("equity"),
                        "available": rt.get("available")},
            "mode": {"mode": mode_label(eff), "live": eff.broker == "bybit" and eff.broker_env == "live"},
            "risk": {"kill_switch": risk.kill_switch_on(), "stop_file": s.stop_file.exists(),
                     "leader_breaker": risk.leader_breaker_tripped(),
                     "daily_loss_hit": bool(equity is not None and risk.daily_loss_hit(equity))},
            "leader": {"address": s.leader_address, "account_value": snap.get("account_value"),
                       "snapshot_at": iso(snap.get("taken_at_ms")), "open_positions": len(snap.get("positions", {}))},
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
        snap = store.get_state("leader_snapshot") or {}
        tracker = store.get_state("tracker") or {}
        ours = {c.coin: c for c in store.copies(open_only=True)}
        ours |= {c.coin: c for c in store.copies_with_status(CopyStatus.OBSERVED_ONLY) if c.closed_at is None}
        positions: dict[str, str] = snap.get("positions", {})
        rows = []
        for coin in sorted(set(positions) | set(ours), key=lambda c: -abs(Decimal(positions.get(c, "0")))):
            size = Decimal(positions.get(coin, "0"))
            copy = ours.get(coin)
            rows.append({"coin": coin, "side": side_label(1 if size > 0 else -1) if size else None,
                         "size": dec(abs(size)), "entry_px": snap.get("entry_px", {}).get(coin),
                         "copyable": bool(tracker.get(coin, {}).get("synced", True)),
                         "ours": copy_dict(copy) if copy else None})
        return {"address": s.leader_address, "snapshot_at": iso(snap.get("taken_at_ms")),
                "account_value": snap.get("account_value"), "rows": rows}

    @app.get("/api/copies")
    def copies(_user: UserDep, store: StoreDep, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        rows = store.copies()
        if status == "OPEN":
            rows = [c for c in rows if c.status in (CopyStatus.OPEN, CopyStatus.SUBMITTING)]
        elif status == "CLOSED":
            rows = [c for c in rows if c.status in CLOSED_REAL]
        elif status:
            rows = [c for c in rows if c.status == status]
        rows.sort(key=lambda c: (c.opened_at or 0, c.id or 0), reverse=True)
        return [copy_dict(c) for c in rows][: max(1, min(limit, 1000))]

    @app.get("/api/events")
    def events(_user: UserDep, store: StoreDep, decision: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        out = []
        for e in store.recent_events(limit=max(1, min(limit, 1000)), decision=decision):
            out.append({"id": e["id"], "coin": e["coin"], "episode_id": e["episode_id"], "type": e["type"],
                        "side": side_label(e["side"]) if e["side"] else None, "fraction": e["fraction"],
                        "leader_px": e["leader_px"], "leader_time": iso(e["leader_time_ms"]),
                        "detected_at": iso(e["detected_at_ms"]), "lag_ms": e["detected_at_ms"] - e["leader_time_ms"],
                        "decision": e["decision"], "rejection": e["rejection"], "detail": e["detail"]})
        return out

    @app.get("/api/logs")
    def logs(_user: UserDep, store: StoreDep, level: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return [{"id": r["id"], "ts": iso(r["ts"]), "level": r["level"], "type": r["type"],
                 "payload": json.loads(r["payload"])}
                for r in store.recent_logs(limit=max(1, min(limit, 1000)), min_level=level)]

    @app.get("/api/analytics")
    def analytics(_user: UserDep, store: StoreDep, days: int = 30) -> dict[str, Any]:
        since = now_ms() - max(1, min(days, 3650)) * DAY_MS
        closed = sorted((c for c in store.copies() if c.status in CLOSED_REAL and (c.closed_at or 0) >= since),
                        key=lambda c: c.closed_at or 0)
        curve, running = [], Decimal(0)
        daily: dict[str, dict[str, Any]] = {}
        for c in closed:
            running += c.realized_pnl
            curve.append({"closed_at": iso(c.closed_at), "coin": c.coin, "pnl": dec(c.realized_pnl),
                          "cumulative": dec(running)})
            day = datetime.fromtimestamp((c.closed_at or 0) / 1000, UTC).strftime("%Y-%m-%d")
            d = daily.setdefault(day, {"day": day, "trades": 0, "wins": 0, "losses": 0, "pnl": Decimal(0)})
            d["trades"] += 1
            d["wins" if c.realized_pnl > 0 else "losses"] += 1
            d["pnl"] += c.realized_pnl
        m = compute(store, since)
        return {"days": days, "curve": curve,
                "daily": [{**d, "pnl": dec(d["pnl"])} for d in daily.values()],
                "by_close": {st: sum(c.status == st for c in closed) for st in CLOSED_REAL},
                "metrics": {k: (dec(v) if isinstance(v, Decimal) else v) for k, v in asdict(m).items()}}

    @app.get("/api/settings")
    def settings_view(_user: UserDep, store: StoreDep) -> dict[str, Any]:
        eff = with_mode_override(s, store.get_state("mode_override"))
        return {
            "leader_address": s.leader_address, "broker": s.broker, "broker_env": s.broker_env,
            "live": s.broker == "bybit" and s.broker_env == "live", "mode": mode_label(eff),
            "strategy": {"risk_per_trade": dec(s.risk_per_trade), "leverage": s.leverage,
                         "take_profit_roi": dec(s.take_profit_roi),
                         "take_profit_move_pct": dec(s.take_profit_roi / s.leverage * 100),
                         "liquidation_move_pct": dec((Decimal(1) / s.leverage - s.maintenance_margin_rate) * 100),
                         "follow_adds": s.follow_adds, "follow_reduces": s.follow_reduces,
                         "max_open_positions": s.max_open_positions,
                         "max_notional_per_coin": dec(s.max_notional_per_coin),
                         "allow_long": s.allow_long, "allow_short": s.allow_short,
                         "coin_allowlist": sorted(s.allowlist), "coin_blocklist": sorted(s.blocklist)},
            "safety": {"max_entry_drift_pct": dec(s.max_entry_drift * 100), "max_signal_age_ms": s.max_signal_age_ms,
                       "max_order_slippage_pct": dec(s.max_order_slippage * 100),
                       "daily_loss_limit_pct": dec(s.daily_loss_limit_pct * 100),
                       "leader_dd_breaker_pct": dec(s.leader_dd_breaker_pct * 100),
                       "missed_exit_seconds": s.missed_exit_seconds},
        }

    # -- control -----------------------------------------------------------------------------------------------

    @app.post("/api/control/mode")
    def set_mode(body: ModeBody, user: UserDep, store: StoreDep) -> dict[str, Any]:
        if body.mode == "EXECUTE" and (body.confirm or "").strip() != EXECUTE_CONFIRM:
            raise HTTPException(status_code=400, detail=f'التنفيذ الفعلي يتطلب كتابة "{EXECUTE_CONFIRM}" للتأكيد.')
        store.set_state("mode_override", {"copy_enabled": body.mode != "WATCH", "observe_only": body.mode != "EXECUTE"})
        Notifier(store, echo=False).warning("MODE_REQUESTED", mode=body.mode, user=user["username"])
        return {"mode": body.mode}

    @app.post("/api/control/kill")
    def kill(body: KillBody, user: UserDep, store: StoreDep) -> dict[str, Any]:
        guard(store).engage_kill(f"dashboard: {user['username']}")
        s.stop_file.parent.mkdir(parents=True, exist_ok=True)
        s.stop_file.touch()
        if body.close_all:
            store.set_state("close_all_requested", True)
        rt = store.get_state("runtime") or {}
        return {"kill_switch": True, "close_all_requested": body.close_all,
                "bot_running": bool(rt.get("updated_ms") and now_ms() - rt["updated_ms"] < 90_000)}

    @app.post("/api/control/resume")
    def resume(user: UserDep, store: StoreDep) -> dict[str, Any]:
        guard(store).release()
        Notifier(store, echo=False).warning("RESUMED_FROM_DASHBOARD", user=user["username"])
        return {"kill_switch": False}

    return app


def serve(settings: Settings) -> None:
    import uvicorn  # noqa: PLC0415

    from loco_copy.notify import configure_logging  # noqa: PLC0415

    configure_logging(settings.log_dir, filename="api.log")
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port, proxy_headers=True,
                forwarded_allow_ips="127.0.0.1", log_config=None)
