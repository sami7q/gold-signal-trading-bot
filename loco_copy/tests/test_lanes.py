"""botup.md v3: several leaders, one lane each (config, store migration, feed routing, isolation, gate)."""

import json
import sqlite3
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from loco_copy.brokers.paper import PaperBroker
from loco_copy.config import LOCO_ADDRESS, read_lane_overrides
from loco_copy.constants import CopyStatus, Decision, Rejection
from loco_copy.engine import CopyEngine, run_inline
from loco_copy.feed import HyperliquidFeed
from loco_copy.notify import Notifier
from loco_copy.reconcile import Reconciler
from loco_copy.risk import RiskGuard
from loco_copy.store import SCHEMA_VERSION, Store
from loco_copy.symbols import SymbolMapper
from loco_copy.tracker import LeaderTracker
from tests.conftest import INSTRUMENTS, T0, F, make_settings

EA = "0x3ea004bbb1004936e526bf97cbd796f0c78273d7"
TWO = f"loco:{LOCO_ADDRESS},3ea:{EA}"


# -- config ---------------------------------------------------------------------------------------------------

def test_v2_settings_mean_one_loco_lane(tmp_path):
    s = make_settings(tmp_path)
    assert s.labels == ["loco"] and s.leader_list[0].address == LOCO_ADDRESS
    assert s.for_lane("loco").keyring_service == "loco-copy-bybit-demo"


def test_a_new_leader_starts_in_watch_with_its_own_keys(tmp_path):
    s = make_settings(tmp_path, leaders=TWO)
    loco, ea = s.for_lane("loco"), s.for_lane("3ea")
    assert s.labels == ["loco", "3ea"] and ea.leader_address == EA and ea.lane == "3ea"
    assert loco.copy_enabled and not loco.copy_observe_only  # the global mode is Loco's
    assert not ea.copy_enabled  # WATCH until the owner switches it
    assert ea.keyring_service == "loco-copy-bybit-demo-3ea" and ea.shared_keyring_service == "loco-copy-bybit-demo"
    with pytest.raises(KeyError):
        s.for_lane("nobody")


@pytest.mark.parametrize("bad", ["l:" + LOCO_ADDRESS, "lo co:" + LOCO_ADDRESS, f"loco:{LOCO_ADDRESS},loco:{EA}",
                                 f"loco:{LOCO_ADDRESS},x2:{LOCO_ADDRESS}", "loco:0x1234", f"loco{LOCO_ADDRESS}"])
def test_bad_leaders_refuse_to_start(tmp_path, bad):
    with pytest.raises(ValueError):
        make_settings(tmp_path, leaders=bad)


def test_lane_overrides_are_typed_and_typos_stop_start_up(tmp_path):
    env = tmp_path / ".env"
    env.write_text("RISK_PER_TRADE__3EA=0.05\nALLOW_EXECUTE__LOCO=true        # the gate, per lane\n"
                   "LEVERAGE__3EA=10\nSOME_TOOL__X=whatever\n", encoding="utf-8")
    overrides = read_lane_overrides(["loco", "3ea"], env)
    assert overrides == {"3ea": {"risk_per_trade": "0.05", "leverage": "10"}, "loco": {"allow_execute": "true"}}
    s = make_settings(tmp_path, leaders=TWO, allow_execute=False).model_copy(update={"lane_overrides": overrides})
    ea = s.for_lane("3ea")
    assert ea.risk_per_trade == Decimal("0.05") and ea.leverage == 10 and not ea.allow_execute
    assert s.for_lane("loco").allow_execute and s.for_lane("loco").leverage == 20
    for typo in ("RISK_PER_TRAD__3EA=0.05", "RISK_PER_TRADE__3EB=0.05", "BROKER__3EA=bybit"):
        env.write_text(typo + "\n", encoding="utf-8")
        with pytest.raises(ValueError):
            read_lane_overrides(["loco", "3ea"], env)
    out_of_range = s.model_copy(update={"lane_overrides": {"3ea": {"risk_per_trade": "2"}}})
    with pytest.raises(ValueError):
        out_of_range.for_lane("3ea")


# -- store v2 -> v3 -------------------------------------------------------------------------------------------

V2 = """
CREATE TABLE schema_version (version INTEGER NOT NULL);
INSERT INTO schema_version VALUES (2);
CREATE TABLE fills (tid INTEGER PRIMARY KEY, coin TEXT NOT NULL, side TEXT NOT NULL, px TEXT NOT NULL,
    sz TEXT NOT NULL, start_position TEXT NOT NULL, time_ms INTEGER NOT NULL, oid INTEGER,
    received_at_ms INTEGER NOT NULL);
CREATE INDEX ix_fills_time ON fills(time_ms);
CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT NOT NULL, episode_id INTEGER NOT NULL,
    type TEXT NOT NULL, side INTEGER NOT NULL, fraction TEXT, leader_px TEXT NOT NULL,
    leader_time_ms INTEGER NOT NULL, detected_at_ms INTEGER NOT NULL, tid INTEGER NOT NULL,
    decision TEXT NOT NULL, rejection TEXT, detail TEXT);
CREATE TABLE copies (id INTEGER PRIMARY KEY AUTOINCREMENT, coin TEXT NOT NULL, symbol TEXT NOT NULL,
    episode_id INTEGER NOT NULL, side INTEGER NOT NULL, status TEXT NOT NULL, qty_initial TEXT NOT NULL,
    qty_open TEXT NOT NULL, entry_price TEXT, tp_price TEXT, exit_price_avg TEXT,
    exit_qty_total TEXT NOT NULL DEFAULT '0', realized_pnl TEXT NOT NULL DEFAULT '0',
    fees TEXT NOT NULL DEFAULT '0', funding TEXT NOT NULL DEFAULT '0', margin TEXT, opened_at INTEGER,
    closed_at INTEGER, close_reason TEXT, leader_entry_px TEXT NOT NULL, slippage_bps TEXT,
    latency_detect_ms INTEGER, latency_order_ms INTEGER, UNIQUE(coin, episode_id));
CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, copy_id INTEGER REFERENCES copies(id),
    client_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, requested_qty TEXT NOT NULL, filled_qty TEXT,
    avg_price TEXT, fee TEXT, status TEXT NOT NULL, error TEXT, sent_at_ms INTEGER NOT NULL, filled_at_ms INTEGER);
CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT INTO fills VALUES (42, 'BTC', 'A', '83933', '0.5', '0', 1, 9, 2);
INSERT INTO events (coin, episode_id, type, side, leader_px, leader_time_ms, detected_at_ms, tid, decision)
    VALUES ('BTC', 1, 'OPEN', -1, '83933', 1, 2, 42, 'COPIED');
INSERT INTO copies (id, coin, symbol, episode_id, side, status, qty_initial, qty_open, leader_entry_px,
    realized_pnl) VALUES (7, 'BTC', 'BTCUSDT', 1, -1, 'CLOSED_RECONCILED', '1.191', '0', '83933', '83.08');
INSERT INTO orders (copy_id, client_id, kind, requested_qty, status, sent_at_ms)
    VALUES (7, 'loco:BTC:1:open', 'OPEN', '1.191', 'FILLED', 1);
INSERT INTO state VALUES ('tracker', '{"BTC": {"position": "0"}}'), ('mode_override', '{"copy_enabled": true}'),
    ('kill_switch', 'false'), ('last_fill_ms', '1');
"""


def test_v2_database_is_migrated_in_place(tmp_path):
    path = tmp_path / "loco.db"
    db = sqlite3.connect(path)
    db.executescript(V2)
    db.commit()
    db.close()

    store = Store(path)
    raw = store._db
    assert raw.execute("SELECT version FROM schema_version").fetchone()[0] == SCHEMA_VERSION == 4  # v2 -> v3 -> v4
    copy = store.get_copy("BTC", 1)
    assert copy is not None and copy.id == 7 and copy.leader == "loco" and copy.realized_pnl == Decimal("83.08")
    assert copy.strategy == "first_fill" and not copy.capped and copy.pending_qty == 0  # v4 columns, v2 row
    assert {"pos_before", "pos_after", "our_px"} <= {r["name"] for r in raw.execute("PRAGMA table_info(events)")}
    assert store.orders_for_copy(7)[0]["client_id"] == "loco:BTC:1:open"  # orders still point at the copy
    assert store.has_fill(42) and store.events_since(0)[0]["leader"] == "loco"
    assert store.get_state("tracker:loco") == {"BTC": {"position": "0"}} and store.get_state("tracker") is None
    assert store.get_state("mode_override:loco") == {"copy_enabled": True} and store.get_state("kill_switch") is False
    assert store.get_state("last_fill_ms:loco") == 1
    # v3: the same trade seen from the other side of two copied leaders is two fills
    fill = F(0, "BTC", "B", 83933, "0.5", "0", tid=42)
    assert store.insert_fill(fill, leader="3ea") and not store.insert_fill(fill, leader="loco")
    store.close()
    Store(path).close()  # opening a v3 database again changes nothing


# -- feed ---------------------------------------------------------------------------------------------------

def _frame(user: str | None, tid: int) -> str:
    data: dict[str, object] = {"fills": [{"coin": "HYPE", "px": "40", "sz": "1", "side": "B", "time": T0,
                                          "startPosition": "0", "tid": tid, "oid": 5}]}
    if user is not None:
        data["user"] = user
    return json.dumps({"channel": "userFills", "data": data})


async def test_one_websocket_routes_each_leaders_fills_to_his_lane():
    got: dict[str, list[int]] = {"loco": [], "3ea": []}
    unknown: list[str] = []

    def handler(label):
        async def h(fills, _is_history):
            got[label].extend(f.tid for f in fills)

        return h

    feed = HyperliquidFeed(ws_url="wss://x", users={LOCO_ADDRESS: handler("loco"), EA.upper(): handler("3ea")},
                           client=None, on_frame=lambda: None, last_seen_ms=lambda _a: None,  # type: ignore[arg-type]
                           on_unknown_user=unknown.append)
    await feed._dispatch(_frame(EA, 1))
    await feed._dispatch(_frame(LOCO_ADDRESS.upper(), 2))
    await feed._dispatch(_frame("0x" + "1" * 40, 3))
    await feed._dispatch(_frame(None, 4))  # no `user` and two leaders: cannot be routed, dropped
    assert got == {"loco": [2], "3ea": [1]} and unknown == ["0x" + "1" * 40, "?"]


# -- two lanes on one exchange account ---------------------------------------------------------------------

@pytest.fixture
def lanes(tmp_path):
    s = make_settings(tmp_path, leaders=TWO).model_copy(update={"lane_overrides": {
        "3ea": {"copy_enabled": "true", "copy_observe_only": "false"}}})
    now = [T0]

    def clock():
        return now[0]

    store = Store(":memory:")
    n = Notifier(store, echo=False)
    paper = PaperBroker(instruments=dict(INSTRUMENTS), start_equity=Decimal("10000"), fee_rate=Decimal(0),
                        slippage_bps=Decimal(0), leverage=20, maintenance_margin_rate=Decimal("0.005"), clock=clock)
    for sym, px in (("HYPEUSDT", "40"), ("BTCUSDT", "100000")):
        paper.set_price(sym, Decimal(px))
    out = {}
    for label in s.labels:
        ls = s.for_lane(label)
        out[label] = SimpleNamespace(s=ls, tracker=LeaderTracker(), risk=RiskGuard(store, ls, n, monitor_feed=False,
                                                                                    clock=clock))

    def held_by_other(me):
        def check(symbol):
            for other in out:
                if other != me and any(c.symbol == symbol for c in store.live_copies(leader=other)):
                    return other
            return None

        return check

    for label, lane in out.items():
        lane.engine = CopyEngine(store=store, broker=paper, settings=lane.s, risk=lane.risk, notifier=n,
                                 mapper=SymbolMapper(INSTRUMENTS), tracker=lane.tracker, instruments=dict(INSTRUMENTS),
                                 clock=clock, run_sync=run_inline, held_elsewhere=held_by_other(label))
        lane.recon = Reconciler(store=store, broker=paper, settings=lane.s, notifier=n, tracker=lane.tracker,
                                risk=lane.risk, engine=lane.engine, run_sync=run_inline, clock=clock,
                                account_symbols=lambda: {c.symbol for c in store.live_copies()},
                                check_orphans=label == "loco")

    async def fill(label, f):
        now[0] = f.received_at_ms
        events, _ = out[label].tracker.on_fill(f)
        return [await out[label].engine.handle(e) for e in events]

    return SimpleNamespace(s=s, store=store, paper=paper, lane=out, fill=fill)


async def test_each_leader_only_ever_touches_his_own_copies(lanes):
    assert await lanes.fill("loco", F(0, "HYPE", "B", 40, 8, 0)) == [Decision.COPIED]
    assert await lanes.fill("3ea", F(10, "BTC", "B", 100000, 1, 0)) == [Decision.COPIED]
    loco, ea = lanes.store.get_copy("HYPE", 1, leader="loco"), lanes.store.get_copy("BTC", 1, leader="3ea")
    assert loco.leader == "loco" and ea.leader == "3ea" and ea.symbol == "BTCUSDT"
    assert lanes.store.get_copy("BTC", 1, leader="loco") is None  # same episode number, other lane: not his

    # Loco closes HYPE: only Loco's copy closes, 3ea's BTC is untouched (and so is his episode 1)
    assert await lanes.fill("loco", F(1000, "HYPE", "A", 40, 8, 8)) == [Decision.FOLLOWED]
    assert lanes.store.get_copy("HYPE", 1, leader="loco").status == CopyStatus.CLOSED_LEADER
    assert lanes.store.get_copy("BTC", 1, leader="3ea").status == CopyStatus.OPEN
    assert {e["leader"] for e in lanes.store.events_since(0, leader="3ea")} == {"3ea"}


async def test_a_coin_one_leader_holds_is_refused_to_the_other_on_a_shared_account(lanes):
    await lanes.fill("loco", F(0, "HYPE", "B", 40, 8, 0))
    assert await lanes.fill("3ea", F(10, "HYPE", "B", 40, 3, 0)) == [Decision.REJECTED]
    ev = lanes.store.events_since(0, leader="3ea")[-1]
    assert ev["rejection"] == Rejection.COIN_HELD_BY_OTHER_LEADER and ev["detail"] == "held by loco"
    assert len(lanes.paper.positions()) == 1  # nothing merged into Loco's HYPE position
    await lanes.fill("loco", F(1000, "HYPE", "A", 40, 8, 8))  # Loco's copy gone ...
    await lanes.fill("3ea", F(1500, "HYPE", "A", 40, 3, 3))  # ... 3ea's refused episode ends ...
    assert await lanes.fill("3ea", F(2000, "HYPE", "B", 40, 3, 0)) == [Decision.COPIED]  # ... his next HYPE copies


async def test_pause_is_per_leader_kill_is_global_and_exits_always_pass(lanes):
    await lanes.fill("3ea", F(0, "BTC", "B", 100000, 1, 0))
    lanes.lane["3ea"].risk.set_paused(True, by="test")
    assert await lanes.fill("3ea", F(10, "HYPE", "B", 40, 3, 0)) == [Decision.REJECTED]
    assert lanes.store.events_since(0, leader="3ea")[-1]["rejection"] == Rejection.LANE_PAUSED
    assert await lanes.fill("loco", F(20, "HYPE", "B", 40, 8, 0)) == [Decision.COPIED]  # Loco unaffected
    # paused, but his exit is still followed
    assert await lanes.fill("3ea", F(30, "BTC", "A", 100000, 1, 1)) == [Decision.FOLLOWED]
    lanes.lane["3ea"].risk.set_paused(False, by="test")
    lanes.lane["loco"].risk.engage_kill("test")
    assert await lanes.fill("3ea", F(40, "BTC", "B", 100000, 1, 0)) == [Decision.REJECTED]
    assert lanes.store.events_since(0, leader="3ea")[-1]["rejection"] == Rejection.KILL_SWITCH


async def test_reconcilers_on_a_shared_account_see_no_orphans(lanes):
    await lanes.fill("loco", F(0, "HYPE", "B", 40, 8, 0))
    await lanes.fill("3ea", F(10, "BTC", "B", 100000, 1, 0))
    for lane in lanes.lane.values():
        await lane.recon.run_once()
    assert not [r for r in lanes.store.logs_since(0) if r["type"] == "ORPHAN_POSITION"]


# -- the App wires one lane per leader -------------------------------------------------------------------------

async def test_app_runs_one_lane_per_leader_on_separate_paper_accounts(tmp_path, monkeypatch):
    from loco_copy import app as app_module

    monkeypatch.setattr(app_module, "fetch_public_instruments", lambda: dict(INSTRUMENTS))
    s = make_settings(tmp_path, leaders=TWO, leader_snapshot_seconds=20)
    app = app_module.App(s)
    await app.build_brokers()
    app.build_lanes()
    loco, ea = app.lanes["loco"], app.lanes["3ea"]
    assert (loco.account, ea.account) == ("paper:loco", "paper:3ea") and loco.broker is not ea.broker
    for lane in (loco, ea):
        lane.risk.on_frame()
        assert lane.paper is not None
        lane.paper.set_price("HYPEUSDT", Decimal("40"))

    now = app_module.now_ms()  # the engine checks signal age against the real clock here
    fresh = replace(F(0, "HYPE", "B", 40, 8, 0, tid=77), time_ms=now, received_at_ms=now)
    await app.on_fill(loco, fresh, False)
    await app.on_fill(ea, fresh, False)  # the same trade id, seen by the other lane: stored again, his own
    await app.on_fill(loco, fresh, False)  # a duplicate within one lane: ignored
    assert app.store.get_copy("HYPE", 1, leader="loco").status == CopyStatus.OPEN  # Loco: EXECUTE
    assert app.store.get_copy("HYPE", 1, leader="3ea") is None  # 3ea: WATCH by default
    assert [e["decision"] for e in app.store.events_since(0, leader="3ea")] == [Decision.REJECTED]
    assert app.store.get_state("last_fill_ms:3ea") == fresh.time_ms and app.store.get_state("tracker:3ea")

    app.write_heartbeat()
    rt = app.store.get_state("runtime")
    assert set(rt["lanes"]) == {"loco", "3ea"} and rt["lanes"]["3ea"]["account"] == "paper:3ea"
    assert not rt["lanes"]["loco"]["shared_account"]
    await app.client.close()


# -- execution gate (botup.md v3 §5.3) ------------------------------------------------------------------------

async def test_execute_without_the_gate_is_recorded_never_sent(harness):
    h = harness(allow_execute=False)
    assert await h.fill(F(0, "HYPE", "B", 40, 8, 0)) == [Decision.OBSERVED_ONLY]
    ev = h.store.events_since(0)[-1]
    assert ev["rejection"] == Rejection.EXECUTION_NOT_ALLOWED and ev["detail"].startswith("execution gate")
    assert h.store.get_copy("HYPE", 1).status == CopyStatus.OBSERVED_ONLY and h.paper.positions() == []


async def test_closing_the_gate_never_blocks_an_exit(harness):
    h = harness()
    await h.fill(F(0, "HYPE", "B", 40, 8, 0))
    h.engine.use_settings(h.settings.model_copy(update={"allow_execute": False}))
    assert await h.fill(F(1000, "HYPE", "A", 40, 8, 8)) == [Decision.FOLLOWED]
    assert h.paper.positions() == []
