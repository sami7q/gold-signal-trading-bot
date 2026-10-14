"""`loco-copy` -- the whole user interface (botup.md §4)."""

from __future__ import annotations

import argparse
import asyncio
import csv
import getpass
import json
import re
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loco_copy.config import DEFAULT_LABEL, PROJECT_ROOT, Settings, get_settings, with_mode_override
from loco_copy.constants import CopyStatus
from loco_copy.metrics import compute
from loco_copy.store import Store, lane_key, now_ms

DURATION = re.compile(r"^(\d+)([smhd])$")
UNIT_MS = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}


def since_ms(text: str) -> int:
    m = DURATION.match(text.strip())
    if not m:
        raise argparse.ArgumentTypeError("use a duration like 30m, 24h or 7d")
    return now_ms() - int(m.group(1)) * UNIT_MS[m.group(2)]


def parse_when(text: str) -> int:
    if DURATION.match(text):
        return since_ms(text)
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)


def fmt_ts(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M:%S") if ms else "-"


def age(ms: int | None) -> str:
    if not ms:
        return "never"
    s = (now_ms() - ms) // 1000
    return f"{s}s ago" if s < 120 else f"{s // 60}m ago" if s < 7200 else f"{s // 3600}h ago"


def table(rows: list[list[Any]], header: list[str]) -> None:
    cells = [[str(c) for c in r] for r in [header, *rows]]
    widths = [max(len(r[i]) for r in cells) for i in range(len(header))]
    for i, r in enumerate(cells):
        print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)))
        if i == 0:
            print("  ".join("-" * w for w in widths))


def side(s: int) -> str:
    return "LONG" if s > 0 else "SHORT"


def lane_label(s: Settings, label: str | None) -> str:
    label = (label or s.labels[0]).lower()
    if label not in s.labels:
        raise SystemExit(f"Unknown leader {label!r}. Leaders: {', '.join(s.labels)}")
    return label


def mode_of(s: Settings) -> str:
    return "WATCH" if not s.copy_enabled else "OBSERVE" if s.copy_observe_only else "EXECUTE"


# -- commands -------------------------------------------------------------------------------------------------


def cmd_run(s: Settings, _a: argparse.Namespace) -> None:
    from loco_copy.app import AlreadyRunning, run  # noqa: PLC0415

    try:
        run(s)
    except AlreadyRunning as exc:
        raise SystemExit(str(exc)) from exc


def cmd_status(s: Settings, _a: argparse.Namespace) -> None:
    from loco_copy.app import RunLock  # noqa: PLC0415
    from loco_copy.notify import Notifier  # noqa: PLC0415
    from loco_copy.risk import RiskGuard  # noqa: PLC0415

    store = Store(s.db_path)
    rt = store.get_state("runtime") or {}
    running = RunLock(s.data_dir / "run.lock").is_held_elsewhere()
    m = compute(store, now_ms() - 86_400_000)
    risk = RiskGuard(store, s, Notifier(store, echo=False))
    print(f"bot process        : {'RUNNING' if running else 'not running'} (pid {rt.get('pid', '-')}, "
          f"heartbeat {age(rt.get('updated_ms'))})")
    print(f"mode               : broker={s.broker} env={s.broker_env} copy_enabled={s.copy_enabled} "
          f"observe_only={s.copy_observe_only}")
    print(f"websocket          : {'connected' if rt.get('ws_connected') else 'DISCONNECTED'}, last frame "
          f"{age(rt.get('last_frame_ms'))}, reconnects {rt.get('ws_reconnects', 0)}")
    print(f"leader snapshot    : {age(rt.get('last_snapshot_ms'))}, failures {rt.get('snapshot_failures', 0)}")
    print(f"detect lag (24h)   : p50 {m.detect_p50_ms} ms, p95 {m.detect_p95_ms} ms")
    print(f"equity / available : {rt.get('equity')} / {rt.get('available')}")
    print(f"open copies        : {len(store.copies(open_only=True))}")
    print(f"kill switch        : {'ON' if risk.kill_switch_on() else 'off'}"
          f"{' (data/STOP present)' if s.stop_file.exists() else ''}; "
          f"leader breaker {'TRIPPED' if risk.leader_breaker_tripped() else 'ok'}")
    warn = store.logs_since(now_ms() - 86_400_000, min_level="WARNING")
    print(f"warnings (24h)     : {len(warn)}" + (f"  last: {warn[-1]['type']}" if warn else ""))
    print()
    _leaders_table(s, store)


def cmd_positions(s: Settings, a: argparse.Namespace) -> None:
    label = lane_label(s, getattr(a, "leader", None))
    store = Store(s.db_path)
    snap = store.get_state(lane_key("leader_snapshot", label))
    if not snap:
        raise SystemExit(f"No snapshot of {label} yet -- start the bot (loco-copy run) first.")
    ours = {c.coin: c for c in store.copies(open_only=True, leader=label)}
    ours |= {c.coin: c for c in store.copies_with_status(CopyStatus.OBSERVED_ONLY, leader=label)
             if c.closed_at is None}
    tracker = store.get_state(lane_key("tracker", label)) or {}
    print(f"{label} snapshot {age(snap['taken_at_ms'])} -- account value {snap['account_value']}")
    rows = []
    for coin in sorted(set(snap["positions"]) | set(ours)):
        pos = Decimal(snap["positions"].get(coin, "0"))
        c = ours.get(coin)
        synced = tracker.get(coin, {}).get("synced", True)
        rows.append([coin, side(1 if pos > 0 else -1) if pos else "flat", pos, snap["entry_px"].get(coin, "-"),
                     "yes" if synced else "NO (pre-existing/drift)",
                     f"{side(c.side)} {c.qty_open} @ {c.entry_price} [{c.status}]" if c else "-"])
    table(rows, ["coin", "leader side", "leader size", "leader entry", "copyable", "ours"])


def _leaders_table(s: Settings, store: Store) -> None:
    from loco_copy.notify import Notifier  # noqa: PLC0415
    from loco_copy.risk import RiskGuard  # noqa: PLC0415

    rt = (store.get_state("runtime") or {}).get("lanes") or {}
    rows = []
    for label in s.labels:
        lane = s.for_lane(label)
        eff = with_mode_override(lane, store.get_state(lane_key("mode_override", label)))
        guard = RiskGuard(store, lane, Notifier(store, echo=False))
        snap = store.get_state(lane_key("leader_snapshot", label)) or {}
        lrt = rt.get(label, {})
        rows.append([label, lane.leader_address, mode_of(eff), "open" if lane.allow_execute else "closed",
                     "PAUSED" if guard.paused() else "on", "TRIPPED" if guard.leader_breaker_tripped() else "ok",
                     lrt.get("equity", "-"), lrt.get("account", "-"), len(store.copies(open_only=True, leader=label)),
                     age(snap.get("taken_at_ms"))])
    table(rows, ["leader", "address", "mode", "exec gate", "switch", "breaker", "equity", "account", "open",
                 "snapshot"])


def cmd_leaders(s: Settings, _a: argparse.Namespace) -> None:
    _leaders_table(s, Store(s.db_path))


def cmd_strategy(s: Settings, _a: argparse.Namespace) -> None:
    """botup.md v4 §5 cli.py: the active strategy per lane, K, the cap, R of open trades, the virtual account."""
    from loco_copy.cap20 import describe  # noqa: PLC0415

    store = Store(s.db_path)
    for label in s.labels:
        lane = s.for_lane(label)
        eff = with_mode_override(lane, store.get_state(lane_key("mode_override", label)))
        w0 = (store.get_state(lane_key("leader_equity", label)) or {}).get("value", "-")
        print(f"\n[{label}] {lane.strategy}  mode={mode_of(eff)}  exec gate="
              f"{'open' if lane.allow_execute else 'closed'}  leader equity W0={w0}")
        if not lane.is_cap20:
            print(f"  first fill only, {lane.risk_per_trade * 100}% of equity at {lane.leverage}x, take profit")
            continue
        for k, v in describe(lane).items():
            print(f"  {k:32} {v}")
        rows = [[c.coin, "virtual" if c.status == CopyStatus.OBSERVED_ONLY else "exchange", c.side, c.qty_open,
                 c.ratio_r, c.cap_notional, f"{c.cost_notional / c.cap_notional:.0%}" if c.cap_notional else "-",
                 c.pending_qty, "yes" if c.capped else "no", c.adds_done, c.leader_pos]
                for c in store.copies(leader=label) if c.strategy == "cap20_cross" and c.closed_at is None]
        if rows:
            table(rows, ["coin", "book", "side", "qty", "R", "CAP", "used", "pending", "capped", "adds", "leader"])
    for name, v in ((store.get_state("runtime") or {}).get("virtual") or {}).items():
        print(f"\n[{name}] equity {v['equity']} (start {v['start_equity']}), notional {v['notional']}, "
              f"positions {len(v['positions'])}, liquidations {v['liquidations']}, kill {v.get('kill_tripped')}")


def cmd_backup(s: Settings, _a: argparse.Namespace) -> None:
    target = s.data_dir / "backups" / f"loco-{time.strftime('%Y%m%d-%H%M%S')}.db"
    print(f"Backed up to {Store(s.db_path).backup(target)}")


def cmd_trades(s: Settings, a: argparse.Namespace) -> None:
    store = Store(s.db_path)
    rows = []
    for c in store.copies(since_ms=a.since, open_only=a.open, leader=a.leader):
        rows.append([c.id, c.leader, fmt_ts(c.opened_at), c.coin, c.symbol, side(c.side), c.status, c.qty_initial,
                     c.leader_entry_px, c.entry_price or "-",
                     f"{c.slippage_bps:.1f}" if c.slippage_bps is not None else "-",
                     c.latency_order_ms if c.latency_order_ms is not None else "-", c.tp_price or "-",
                     c.exit_price_avg or "-", f"{c.realized_pnl:.2f}", c.close_reason or ""])
    table(rows, ["id", "leader", "opened (UTC)", "coin", "symbol", "side", "status", "qty", "leader px", "our px",
                 "slip bps", "order ms", "tp", "exit", "pnl", "reason"])


def cmd_events(s: Settings, a: argparse.Namespace) -> None:
    store = Store(s.db_path)
    rows = [[fmt_ts(e["detected_at_ms"]), e["leader"], e["coin"], e["episode_id"], e["type"],
             side(e["side"]) if e["side"] else "-",
             e["fraction"] or "", e["leader_px"], e["detected_at_ms"] - e["leader_time_ms"], e["decision"],
             e["rejection"] or "", e["detail"] or ""] for e in store.events_since(a.since, leader=a.leader)]
    table(rows, ["detected (UTC)", "leader", "coin", "ep", "type", "side", "frac", "loco px", "lag ms", "decision",
                 "rejection", "detail"])


def cmd_kill(s: Settings, a: argparse.Namespace) -> None:
    from loco_copy.notify import Notifier  # noqa: PLC0415
    from loco_copy.risk import RiskGuard  # noqa: PLC0415

    store = Store(s.db_path)
    if a.leader:  # one leader only: pause his lane (his exits are still followed)
        label = lane_label(s, a.leader)
        RiskGuard(store, s.for_lane(label), Notifier(store, echo=False)).set_paused(True, by="loco-copy kill")
        if a.close_all:
            store.set_state(lane_key("close_all_requested", label), True)
            print(f"Asked the running bot to close every {label} copy (reduce-only).")
        print(f"Leader {label} paused: no new copies of him. Others are untouched.")
        return
    RiskGuard(store, s, Notifier(store, echo=False)).engage_kill("loco-copy kill")
    s.stop_file.parent.mkdir(parents=True, exist_ok=True)
    s.stop_file.touch()
    print("Kill switch ON: no new opens (data/STOP created).")
    if a.close_all:
        from loco_copy.app import RunLock  # noqa: PLC0415

        if RunLock(s.data_dir / "run.lock").is_held_elsewhere():
            store.set_state("close_all_requested", True)
            print("Asked the running bot to close every copy (reduce-only); check `loco-copy trades --open`.")
        else:
            asyncio.run(_close_all_offline(s, store))


async def _close_all_offline(s: Settings, store: Store) -> None:
    from loco_copy.app import App, close_all  # noqa: PLC0415

    app = App(s)
    await app.build_brokers()
    app.build_lanes()
    for lane in app.lanes.values():
        await close_all(app.store, lane.engine, app.n)
    app.save_runtime()
    await app.client.close()
    print("Closed every open copy (reduce-only).")


def cmd_resume(s: Settings, a: argparse.Namespace) -> None:
    from loco_copy.notify import Notifier  # noqa: PLC0415
    from loco_copy.risk import RiskGuard, release_everything  # noqa: PLC0415

    store = Store(s.db_path)
    if a.leader:
        label = lane_label(s, a.leader)
        guard = RiskGuard(store, s.for_lane(label), Notifier(store, echo=False))
        guard.set_paused(False, by="loco-copy resume")
        guard.reset_breaker()
        print(f"Leader {label} resumed (switch on, his leader breaker reset). The global kill switch is unchanged.")
        return
    release_everything(store, s, Notifier(store, echo=False))
    print("Kill switch released (data/STOP removed, every leader breaker reset). Paused leaders stay paused.")


def cmd_report(s: Settings, a: argparse.Namespace) -> None:
    store = Store(s.db_path)
    m = compute(store, a.since, leader=a.leader)
    out = s.data_dir / "exports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    copies = store.copies(since_ms=a.since, leader=a.leader)
    with open(out / f"trades_{stamp}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "opened_utc", "closed_utc", "coin", "symbol", "side", "status", "qty", "leader_px",
                    "entry", "tp", "exit", "pnl", "fees", "slippage_bps", "latency_detect_ms", "latency_order_ms"])
        for c in copies:
            w.writerow([c.id, fmt_ts(c.opened_at), fmt_ts(c.closed_at), c.coin, c.symbol, side(c.side), c.status,
                        c.qty_initial, c.leader_entry_px, c.entry_price, c.tp_price, c.exit_price_avg, c.realized_pnl,
                        c.fees, c.slippage_bps, c.latency_detect_ms, c.latency_order_ms])

    def mark(ok: bool | None) -> str:
        return "n/a" if ok is None else "PASS" if ok else "FAIL"

    lines = [
        f"# loco-copy report — {stamp}", "",
        f"Window: since {fmt_ts(a.since)} UTC. Leader: {a.leader or 'all'}. Broker `{s.broker}` / `{s.broker_env}`, "
        f"observe_only={s.copy_observe_only}.", "",
        "| Metric | Value | Target | Status |", "|---|---|---|---|",
        f"| Detection latency p50 / p95 | {m.detect_p50_ms} / {m.detect_p95_ms} ms | < 1000 / < 3000 ms | "
        f"{mark(None if m.detect_p50_ms is None else m.detect_p50_ms < 1000 and (m.detect_p95_ms or 0) < 3000)} |",
        f"| Order latency p50 | {m.order_p50_ms} ms | < 1000 ms | "
        f"{mark(None if m.order_p50_ms is None else m.order_p50_ms < 1000)} |",
        f"| Entry slippage median | {m.slippage_median_bps} bps | < 10 bps | "
        f"{mark(None if m.slippage_median_bps is None else m.slippage_median_bps < 10)} |",
        f"| Capture rate | {m.capture_rate} ({m.copied_opens}/{m.leader_opens}, {m.not_listed} not listed) | ≥ 0.90 | "
        f"{mark(None if m.capture_rate is None else m.capture_rate >= 0.9)} |",
        f"| Missed exits (auto-closed) | {m.missed_exits} | 0 | {mark(m.missed_exits == 0)} |",
        f"| Liquidations | {m.liquidations} | compare with `loco-copy replay` on the same fills | — |",
        f"| Take profits / leader exits | {m.take_profits} / {m.leader_exits} | — | — |",
        f"| Realized PnL | {m.realized_pnl:.2f} | — | — |", "",
        f"Rejections: {json.dumps(m.rejections)}", "",
        "Not financial advice."]
    (out / f"report_{stamp}.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n-> {out / f'report_{stamp}.md'}\n-> {out / f'trades_{stamp}.csv'}")


def cmd_replay(s: Settings, a: argparse.Namespace) -> None:
    from loco_copy.replay import (  # noqa: PLC0415
        fixture_candle_source,
        load_fixture_fills,
        run_replay,
    )

    if (a.strategy or s.strategy) == "cap20_cross":
        _replay_cap20(s, a)
        return
    if a.source == "fixtures":
        fx = PROJECT_ROOT / "tests" / "fixtures"
        fills = load_fixture_fills(fx / "loco_fills.json.gz")
        candles = fixture_candle_source(fx / "loco_candles.json.gz")
    else:
        from loco_copy.replay_sources import db_fills, hyperliquid_candle_source  # noqa: PLC0415

        fills = db_fills(Store(s.db_path))
        candles = hyperliquid_candle_source(s)
    rs = s.model_copy(update={"copy_enabled": True, "copy_observe_only": False})
    r = asyncio.run(run_replay(fills, candles, rs, start_ms=a.from_, end_ms=a.to, fee_rate=Decimal(a.fee or "0.0008")))
    print(f"fills replayed: {len([f for f in fills if f.time_ms >= a.from_ and (a.to is None or f.time_ms <= a.to)])}")
    print(f"counts        : {r.counts}")
    print(f"equity        : {r.start_equity} -> {r.final_equity:.2f}  (max drawdown {float(r.max_dd):.1%})")
    print(f"rejections    : {r.rejections}")


def _replay_cap20(s: Settings, a: argparse.Namespace) -> None:
    """botup.md v4 §5: the cap20 engine over recorded fills on a virtual cross account (leader's own prices, fee +
    slippage per side, candles for marks and liquidation, W(t) from the leader's account value history)."""
    from loco_copy.replay import (  # noqa: PLC0415
        equity_at,
        load_equity_history,
        load_fixture_fills,
        load_trip_candles,
        run_cap20_replay,
    )

    label = a.leader or s.labels[0]
    lane = s.for_lane(label)
    if a.strategy:
        lane = lane.model_copy(update={"strategy": a.strategy, "margin_mode": "CROSS", "follow_adds": True,
                                       "take_profit_roi": None})
    if a.leverage:
        lane = lane.model_copy(update={"leverage": a.leverage})
    if a.source == "fixtures":
        fx = PROJECT_ROOT / "tests" / "fixtures"
        bench = json.loads((fx / "benchmark_cap20.json").read_text(encoding="utf-8"))
        fills = load_fixture_fills(fx / "loco_fills.json.gz")
        candles = load_trip_candles(fx / "loco_candles_cap20.json.gz")
        history = load_equity_history(fx / "loco_equity.json.gz")
        start, end = a.from_ or bench["t0_ms"], a.to or bench["end_ms"]
        lane = lane.model_copy(update={"min_order_usdt": Decimal(bench["min_order_usdt"])})
    else:
        from loco_copy.replay_sources import (  # noqa: PLC0415
            db_fills,
            hyperliquid_candle_source,
            hyperliquid_equity_history,
            trip_candles,
        )

        fills = db_fills(Store(s.db_path), leader=label)
        candles = trip_candles(fills, hyperliquid_candle_source(s))
        history = hyperliquid_equity_history(s, lane.leader_address)
        start, end = a.from_, a.to or now_ms()
    r = asyncio.run(run_cap20_replay(fills, candles, equity_at(history), lane, start_ms=start, end_ms=end,
                                     fee_rate=Decimal(a.fee or "0.00095"), start_equity=Decimal(a.equity)))
    print(f"leader {label} | cap20_cross {lane.leverage}x | K {lane.k} | cap {lane.per_trade_margin_cap_pct}%")
    print(f"counts        : {r.counts}")
    print(f"equity        : {r.start_equity} -> {r.final_equity:.2f}  (max drawdown {float(r.max_dd):.1%})")
    print(f"rejections    : {r.rejections}")


def cmd_set_key(s: Settings, a: argparse.Namespace) -> None:
    import keyring  # noqa: PLC0415

    if a.target == "anthropic":  # botup.md §V8: the AI advisor's Claude API key
        key = getpass.getpass("Anthropic API key (hidden): ").strip()
        if not key:
            raise SystemExit("Nothing stored (empty input).")
        keyring.set_password("loco-copy-anthropic", "api_key", key)
        print("Stored in the OS keyring under 'loco-copy-anthropic'. Restart the bot to use it.")
        return

    env = a.target.removeprefix("bybit-")
    label = lane_label(s, a.leader) if a.leader else DEFAULT_LABEL
    service = f"loco-copy-bybit-{env}" if label == DEFAULT_LABEL else f"loco-copy-bybit-{env}-{label}"
    key = getpass.getpass(f"Bybit {env} API key (hidden): ").strip()
    secret = getpass.getpass(f"Bybit {env} API secret (hidden): ").strip()
    if not key or not secret:
        raise SystemExit("Nothing stored (empty input).")
    keyring.set_password(service, "api_key", key)
    keyring.set_password(service, "api_secret", secret)
    print(f"Stored in the OS keyring under '{service}'. Never put keys in .env or git.")
    if label != DEFAULT_LABEL:
        print(f"Leader {label} now trades on its own account (restart the bot to use it).")


def cmd_research_import(s: Settings, a: argparse.Namespace) -> None:
    """botup.md §V9: copy the research project's export into data/research (latest + a dated copy) for the dashboard."""
    import json  # noqa: PLC0415
    import shutil  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    src = Path(a.file)
    data = json.loads(src.read_text(encoding="utf-8"))  # refuse a broken file before replacing the current one
    if "top10" not in data or "mixes" not in data:
        raise SystemExit(f"{src} is not a research export (no top10 / mixes)")
    dst = s.data_dir / "research"
    dst.mkdir(parents=True, exist_ok=True)
    day = str(data.get("generated", ""))[:10] or "undated"
    shutil.copyfile(src, dst / f"research_{day}.json")
    shutil.copyfile(src, dst / "latest.json")
    print(f"Imported: {len(data['top10'])} wallets, {len(data['mixes'])} mixes, generated {data.get('generated')}.")


def cmd_api(s: Settings, _a: argparse.Namespace) -> None:
    from loco_copy.web import serve  # noqa: PLC0415

    serve(s)


def cmd_add_user(s: Settings, a: argparse.Namespace) -> None:
    from loco_copy.auth import hash_password  # noqa: PLC0415

    password = getpass.getpass(f"Password for {a.username} (hidden): ")
    if len(password) < 8 or password != getpass.getpass("Repeat password: "):
        raise SystemExit("Not stored: passwords differ or are shorter than 8 characters.")
    Store(s.db_path).upsert_user(a.username, a.display_name or a.username, hash_password(password))
    print(f"Dashboard user '{a.username}' saved.")


def cmd_delete_user(s: Settings, a: argparse.Namespace) -> None:
    store = Store(s.db_path)
    if not store.delete_user(a.username):
        raise SystemExit(f"No dashboard user '{a.username}'.")
    left = ", ".join(r["username"] for r in store.users()) or "none -- create one with add-user"
    print(f"Dashboard user '{a.username}' deleted and signed out. Remaining: {left}")


def cmd_import_users(s: Settings, a: argparse.Namespace) -> None:
    """Copy dashboard logins (with their existing password hashes) from the old gold-bot database."""
    import sqlite3  # noqa: PLC0415

    src = sqlite3.connect(f"file:{a.path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    store = Store(s.db_path)
    for row in src.execute("SELECT username, display_name, password_hash FROM users"):
        store.upsert_user(row["username"], row["display_name"] or row["username"], row["password_hash"])
        print(f"imported {row['username']}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="loco-copy", description="Copy Loco's Hyperliquid perp trades (see botup.md).")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="start the bot (feed + engine + reconciler)").set_defaults(fn=cmd_run)
    sub.add_parser("status", help="health at a glance").set_defaults(fn=cmd_status)
    sub.add_parser("leaders", help="every copied leader: mode, gate, switch, account, open copies").set_defaults(
        fn=cmd_leaders)
    sub.add_parser("loco", help="Loco's positions (last snapshot) next to ours").set_defaults(fn=cmd_positions)
    pos = sub.add_parser("positions", help="a leader's positions (last snapshot) next to ours")
    pos.add_argument("--leader", default=None)
    pos.set_defaults(fn=cmd_positions)
    sub.add_parser("backup", help="consistent copy of the database into data/backups").set_defaults(fn=cmd_backup)
    sub.add_parser("strategy", help="active strategy per leader: K, cap, R of open trades, virtual account") \
        .set_defaults(fn=cmd_strategy)
    t = sub.add_parser("trades", help="copy trades")
    t.add_argument("--open", action="store_true")
    t.add_argument("--leader", default=None)
    t.add_argument("--since", type=since_ms, default=since_ms("7d"))
    t.set_defaults(fn=cmd_trades)
    e = sub.add_parser("events", help="every Loco event and the decision taken")
    e.add_argument("--since", type=since_ms, default=since_ms("1d"))
    e.add_argument("--leader", default=None)
    e.set_defaults(fn=cmd_events)
    k = sub.add_parser("kill", help="engage the kill switch")
    k.add_argument("--close-all", action="store_true", help="also close every copy (reduce-only)")
    k.add_argument("--leader", default=None, help="pause one leader only (his exits are still followed)")
    k.set_defaults(fn=cmd_kill)
    rs = sub.add_parser("resume", help="release the kill switch (or, with --leader, switch one leader back on)")
    rs.add_argument("--leader", default=None)
    rs.set_defaults(fn=cmd_resume)
    r = sub.add_parser("report", help="CSV + markdown: live metrics vs targets")
    r.add_argument("--since", type=since_ms, default=since_ms("7d"))
    r.add_argument("--leader", default=None)
    r.set_defaults(fn=cmd_report)
    rp = sub.add_parser("replay", help="recorded Loco fills through the real pipeline with PaperBroker")
    rp.add_argument("--from", dest="from_", type=parse_when, default=0, help="ISO date/time (UTC) or e.g. 90d")
    rp.add_argument("--to", type=parse_when, default=None)
    rp.add_argument("--source", choices=["db", "fixtures"], default="db")
    rp.add_argument("--fee", default=None, help="fee + slippage per side (first_fill 0.0008, cap20 0.00095)")
    rp.add_argument("--strategy", choices=["first_fill", "cap20_cross"], default=None,
                    help="default: the configured STRATEGY")
    rp.add_argument("--leader", default=None, help="lane label (cap20 --source db)")
    rp.add_argument("--leverage", type=int, default=None, help="cap20: 3 or 5 (default: LEVERAGE)")
    rp.add_argument("--equity", default="10000", help="cap20: start equity")
    rp.set_defaults(fn=cmd_replay)
    sk = sub.add_parser("set-key", help="store an API key in the OS keyring (Bybit, or Anthropic for the AI advisor)")
    sk.add_argument("target", choices=["bybit-demo", "bybit-live", "anthropic"])
    sk.add_argument("--leader", default=None, help="keys of that leader's own Bybit (sub-)account")
    sk.set_defaults(fn=cmd_set_key)
    sub.add_parser("api", help="start the dashboard API (localhost only, behind the tunnel)").set_defaults(fn=cmd_api)
    ri = sub.add_parser("research-import", help="load the research export (research_latest.json) into the dashboard")
    ri.add_argument("file")
    ri.set_defaults(fn=cmd_research_import)
    au = sub.add_parser("add-user", help="create or reset a dashboard login")
    au.add_argument("username")
    au.add_argument("--display-name", default=None)
    au.set_defaults(fn=cmd_add_user)
    du = sub.add_parser("delete-user", help="remove a dashboard login and end its sessions")
    du.add_argument("username")
    du.set_defaults(fn=cmd_delete_user)
    iu = sub.add_parser("import-users", help="copy dashboard logins from the old gold-bot database")
    iu.add_argument("path", help="path to goldbot.db")
    iu.set_defaults(fn=cmd_import_users)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        settings = get_settings()
    except Exception as exc:
        raise SystemExit(f"Configuration refused: {exc}") from exc
    args.fn(settings, args)


if __name__ == "__main__":
    main(sys.argv[1:])
