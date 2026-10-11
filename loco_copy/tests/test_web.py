from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from loco_copy.auth import hash_password, verify_password
from loco_copy.config import with_mode_override
from loco_copy.constants import CopyStatus, Decision
from loco_copy.models import Copy
from loco_copy.store import Store, now_ms
from loco_copy.web import EXECUTE_CONFIRM, create_app
from tests.conftest import F, make_settings

ORIGIN = "https://boot-tau-indol.vercel.app"


@pytest.fixture
def ctx(tmp_path):
    s = make_settings(tmp_path, api_cookie_secure=False)  # TestClient talks plain http
    store = Store(s.db_path)
    store.upsert_user("sami", "Sami", hash_password("correct-horse"))
    store.close()
    return s, TestClient(create_app(s), headers={"Origin": ORIGIN})  # as the dashboard's browser sends it


def login(client, password="correct-horse"):
    return client.post("/api/auth/login", json={"username": "sami", "password": password})


def test_everything_but_health_and_login_needs_a_session(ctx):
    _s, c = ctx
    assert c.get("/api/health").status_code == 200
    for path in ("/api/auth/me", "/api/overview", "/api/loco", "/api/copies", "/api/events", "/api/logs",
                 "/api/analytics", "/api/settings"):
        assert c.get(path).status_code == 401, path
    assert c.post("/api/control/kill", json={}).status_code == 401
    assert c.post("/api/control/mode", json={"mode": "WATCH"}).status_code == 401


def test_login_me_logout(ctx):
    _s, c = ctx
    assert login(c, "wrong").status_code == 401
    r = login(c)
    assert r.status_code == 200 and r.json()["user"]["username"] == "sami"
    assert "loco_session" in r.cookies
    assert c.get("/api/auth/me").json()["user"]["display_name"] == "Sami"
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").status_code == 401


def test_deleted_user_is_signed_out(ctx):
    s, c = ctx
    login(c)
    assert c.get("/api/auth/me").status_code == 200
    store = Store(s.db_path)
    assert store.delete_user("sami") and not store.delete_user("sami")
    assert c.get("/api/auth/me").status_code == 401  # the live session died with the user
    assert login(c).status_code == 401


def test_brute_force_is_throttled(ctx):
    _s, c = ctx
    for _ in range(5):
        assert login(c, "nope").status_code == 401
    assert login(c).status_code == 429  # even the right password, for 15 minutes


def test_gold_bot_password_hashes_are_compatible():
    # format written by the gold bot's app/core/security.py::hash_password
    import hashlib

    salt = bytes.fromhex("00112233445566778899aabbccddeeff")
    digest = hashlib.pbkdf2_hmac("sha256", b"secret-pass", salt, 390_000)
    encoded = f"pbkdf2_sha256$390000${salt.hex()}${digest.hex()}"
    assert verify_password("secret-pass", encoded) and not verify_password("x", encoded)
    assert not verify_password("x", "garbage")


def test_execute_mode_needs_the_confirmation_phrase(ctx):
    s, c = ctx
    login(c)
    assert c.post("/api/control/mode", json={"mode": "EXECUTE"}).status_code == 400
    assert c.post("/api/control/mode", json={"mode": "OBSERVE"}).status_code == 200
    store = Store(s.db_path)
    eff = with_mode_override(s, store.get_state("mode_override:loco"))
    assert eff.copy_enabled and eff.copy_observe_only
    assert c.post("/api/control/mode", json={"mode": "EXECUTE", "confirm": EXECUTE_CONFIRM}).status_code == 200
    eff = with_mode_override(s, store.get_state("mode_override:loco"))
    assert eff.copy_enabled and not eff.copy_observe_only
    assert eff.broker == s.broker and eff.broker_env == s.broker_env  # the dashboard can never change these
    assert c.get("/api/settings").json()["mode"] == "EXECUTE"


def test_kill_and_resume(ctx):
    s, c = ctx
    login(c)
    r = c.post("/api/control/kill", json={"close_all": True}).json()
    assert r["kill_switch"] and r["close_all_requested"] and s.stop_file.exists()
    assert c.get("/api/overview").json()["risk"]["kill_switch"] is True
    c.post("/api/control/resume")
    assert not s.stop_file.exists()
    assert c.get("/api/overview").json()["risk"]["kill_switch"] is False


def test_writes_from_another_site_are_refused(ctx):
    # The cookie is SameSite=None, so another page's form POST would carry it; only Origin tells them apart.
    s, c = ctx
    login(c)
    c.post("/api/control/kill", json={})
    evil = c.post("/api/control/resume", headers={"Origin": "https://evil.example", "Content-Type": "text/plain"})
    assert evil.status_code == 403
    bare = TestClient(c.app)  # no Origin at all, e.g. a script replaying the cookie
    bare.cookies = c.cookies
    assert bare.post("/api/control/resume").status_code == 403
    assert bare.post("/api/auth/login", json={"username": "sami", "password": "correct-horse"}).status_code == 403
    assert c.get("/api/overview").json()["risk"]["kill_switch"] is True  # still engaged
    assert s.stop_file.exists()


def test_cors_only_for_the_dashboard(ctx):
    _s, c = ctx
    ok = c.options("/api/overview", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == ORIGIN
    assert ok.headers.get("access-control-allow-credentials") == "true"
    bad = c.options("/api/overview", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


def test_read_endpoints_shape(ctx):
    s, c = ctx
    store = Store(s.db_path)
    copy = store.insert_copy(Copy(coin="HYPE", symbol="HYPEUSDT", episode_id=1, side=1, status=CopyStatus.OPEN,
                                  qty_initial=Decimal("500"), qty_open=Decimal("500"), leader_entry_px=Decimal("40"),
                                  entry_price=Decimal("40.01"), tp_price=Decimal("41"), opened_at=now_ms()))
    closed = Copy(coin="BTC", symbol="BTCUSDT", episode_id=1, side=1, status=CopyStatus.CLOSED_TP,
                  qty_initial=Decimal("0.1"), qty_open=Decimal(0), leader_entry_px=Decimal("100000"),
                  entry_price=Decimal("100000"), opened_at=now_ms() - 5000)
    closed = store.insert_copy(closed)
    closed.realized_pnl, closed.closed_at = Decimal("250"), now_ms()
    store.update_copy(closed)
    virtual = store.insert_copy(Copy(coin="ETH", symbol="ETHUSDT", episode_id=1, side=-1,
                                     status=CopyStatus.OBSERVED_ONLY, qty_initial=Decimal("1"), qty_open=Decimal(0),
                                     leader_entry_px=Decimal("2500"), entry_price=Decimal("2500"),
                                     opened_at=now_ms() - 9000))
    virtual.realized_pnl, virtual.closed_at = Decimal("100"), now_ms()
    store.update_copy(virtual)
    store.insert_event(*_event(), decision=Decision.COPIED)
    store.set_state("leader_snapshot:loco", {"taken_at_ms": now_ms(), "account_value": "467000",
                                        "positions": {"HYPE": "7250", "BTC": "-0.5"}, "entry_px": {"HYPE": "88"}})
    store.set_state("tracker:loco", {"BTC": {"synced": False}})
    login(c)
    ov = c.get("/api/overview").json()
    assert ov["stats"]["open_copies"] == 1 and ov["stats"]["wins"] == 1
    assert ov["stats"]["realized_pnl_total"] == "250" and ov["mode"]["mode"] == "EXECUTE"
    rows = {r["coin"]: r for r in c.get("/api/loco").json()["rows"]}
    assert rows["HYPE"]["ours"]["id"] == copy.id and rows["HYPE"]["side"] == "LONG"
    assert rows["BTC"]["side"] == "SHORT" and rows["BTC"]["copyable"] is False
    assert c.get("/api/copies?status=OPEN").json()[0]["coin"] == "HYPE"
    # newest opened first, whatever order the rows were inserted in
    assert [x["coin"] for x in c.get("/api/copies").json()] == ["HYPE", "BTC", "ETH"]
    assert c.get("/api/events").json()[0]["decision"] == "COPIED"
    an = c.get("/api/analytics?days=7").json()
    assert an["curve"][-1]["cumulative"] == "250" and an["by_close"]["CLOSED_TP"] == 1
    assert an["metrics"]["realized_pnl"] == "250"  # the observe-only copy's virtual 100 stays out
    assert ov["stats"]["observed_pnl"] == "100"
    assert c.get("/api/settings").json()["strategy"]["leverage"] == 20


def test_trade_and_event_details_rebuild_the_btc_trade(ctx):
    # The real 2026-10-11 trade: Loco shorts 0.5 BTC in 15 pieces, we copy, his close is missed, the reconciler
    # closes us 70 s later. The detail views must show all of that from the stored data.
    from dataclasses import replace

    from loco_copy.constants import OrderKind, OrderStatus
    from loco_copy.models import LeaderEvent
    from tests.test_tracker import BTC_OPEN_672, BTC_OPEN_819, _burst

    s, c = ctx
    store = Store(s.db_path)
    for f in _burst(672, BTC_OPEN_672) + _burst(819, BTC_OPEN_819):
        store.insert_fill(replace(f, oid=570582805792))  # one order, 15 pieces
    store.insert_fill(replace(F(1_282_694, "BTC", "B", 83772, "0.5", "-0.5", tid=793162333766451), oid=571262093874))
    ev = LeaderEvent("OPEN", "BTC", 1, -1, Decimal("83933"), F(0, "BTC", "A", 1, 1, 0).time_ms + 672,
                     F(0, "BTC", "A", 1, 1, 0).time_ms + 1069, 233538180697405)
    event_id = store.insert_event(ev, Decision.COPIED, detail="qty=1.191 px=83936.7")
    t0 = ev.leader_time_ms - 672
    copy = store.insert_copy(Copy(coin="BTC", symbol="BTCUSDT", episode_id=1, side=-1, status=CopyStatus.OPEN,
                                  qty_initial=Decimal("1.191"), qty_open=Decimal("1.191"),
                                  leader_entry_px=Decimal("83933"), entry_price=Decimal("83936.7"),
                                  tp_price=Decimal("81838.3"), opened_at=t0 + 2615, margin=Decimal("4998.430485")))
    for cid, kind, sent, filled, px, fee in (("loco:BTC:1:open", OrderKind.OPEN, t0 + 1979, t0 + 2615, "83936.7",
                                              "54.98273534"),
                                             ("loco:BTC:1:reconciled", OrderKind.CLOSE, t0 + 1_353_865,
                                              t0 + 1_353_983, "83774.7", "54.87661724")):
        store.insert_order(copy_id=copy.id, client_id=cid, kind=kind, requested_qty=Decimal("1.191"))
        store._db.execute("UPDATE orders SET sent_at_ms=? WHERE client_id=?", (sent, cid))
        store.finish_order(cid, OrderStatus.FILLED, filled_qty=Decimal("1.191"), avg_price=Decimal(px),
                           fee=Decimal(fee), filled_at_ms=filled)
    copy.status, copy.closed_at, copy.close_reason = CopyStatus.CLOSED_RECONCILED, t0 + 1_354_260, "RECONCILED"
    copy.qty_open, copy.exit_qty_total, copy.exit_price_avg = Decimal(0), Decimal("1.191"), Decimal("83774.7")
    copy.fees, copy.realized_pnl = Decimal("109.85935258"), Decimal("83.08")
    store.update_copy(copy)
    login(c)

    d = c.get(f"/api/copies/{copy.id}").json()
    opened = d["leader"]["open"]
    assert opened["fills"] == 15 and Decimal(opened["size"]) == Decimal("0.5")
    assert Decimal(opened["position_before"]) == 0 and Decimal(opened["position_after"]) == Decimal("-0.5")
    assert d["leader"]["exit_captured"] is False and Decimal(d["leader"]["exit"]["avg_px"]) == 83772
    assert d["comparison"]["exit_delay_ms"] == 1_354_260 - 1_282_694
    assert Decimal(d["figures"]["gross_pnl"]) == Decimal("192.942") and d["figures"]["leverage"] == 20
    kinds = [x["kind"] for x in d["timeline"]]
    assert kinds[:4] == ["LEADER_OPEN", "DETECTED", "ORDER_SENT", "ORDER_FILLED"]
    assert "LEADER_EXIT_MISSED" in kinds and kinds[-1] == "CLOSED"
    assert kinds.index("LEADER_EXIT_MISSED") < kinds.index("CLOSED")

    e = c.get(f"/api/events/{event_id}").json()
    assert e["leader_order"]["fills"] == 15 and e["copy"]["id"] == copy.id and e["fill"]["new_position"] == "-0.00566"
    assert c.get("/api/events").json()[0]["copy_id"] == copy.id
    assert c.get("/api/copies/999").status_code == 404 and c.get("/api/events/999").status_code == 404


EA = "0x3ea004bbb1004936e526bf97cbd796f0c78273d7"


@pytest.fixture
def two(tmp_path):
    from loco_copy.config import LOCO_ADDRESS

    s = make_settings(tmp_path, api_cookie_secure=False, leaders=f"loco:{LOCO_ADDRESS},3ea:{EA}").model_copy(
        update={"lane_overrides": {"3ea": {"allow_execute": "false"}}})
    store = Store(s.db_path)
    store.upsert_user("sami", "Sami", hash_password("correct-horse"))
    store.close()
    return s, TestClient(create_app(s), headers={"Origin": ORIGIN})


def test_leaders_page_switch_and_mode_per_leader(two):
    s, c = two
    login(c)
    leaders = {x["label"]: x for x in c.get("/api/leaders").json()}
    assert list(leaders) == ["loco", "3ea"] and leaders["3ea"]["address"] == EA
    assert leaders["loco"]["mode"] == "EXECUTE" and leaders["3ea"]["mode"] == "WATCH"  # a new leader starts watching
    assert leaders["3ea"]["internal_label"] and not leaders["3ea"]["allow_execute"] and not leaders["3ea"]["paused"]

    assert c.post("/api/leaders/3ea/active", json={"active": False}).json() == {"leader": "3ea", "active": False}
    leaders = {x["label"]: x for x in c.get("/api/leaders").json()}
    assert leaders["3ea"]["paused"] and not leaders["loco"]["paused"]
    assert c.post("/api/leaders/3ea/active", json={"active": True}).status_code == 200

    # 3ea's execution gate is closed on the server: the dashboard cannot open it, whatever the phrase
    r = c.post("/api/control/mode", json={"leader": "3ea", "mode": "EXECUTE", "confirm": EXECUTE_CONFIRM})
    assert r.status_code == 400 and "ALLOW_EXECUTE__3EA" in r.json()["detail"]
    assert c.post("/api/control/mode", json={"leader": "3ea", "mode": "OBSERVE"}).json()["mode"] == "OBSERVE"
    store = Store(s.db_path)
    assert store.get_state("mode_override:3ea") == {"copy_enabled": True, "observe_only": True}
    assert store.get_state("mode_override:loco") is None  # Loco untouched
    assert {x["label"]: x["mode"] for x in c.get("/api/leaders").json()} == {"loco": "EXECUTE", "3ea": "OBSERVE"}

    assert c.get("/api/leaders/3ea/positions").json()["label"] == "3ea"
    for path in ("/api/leaders/nobody/positions", "/api/copies?leader=nobody", "/api/events?leader=nobody"):
        assert c.get(path).status_code == 404, path
    assert c.post("/api/control/mode", json={"leader": "nobody", "mode": "WATCH"}).status_code == 404


def test_rows_say_which_leader_they_belong_to(two):
    s, c = two
    store = Store(s.db_path)
    for leader, coin in (("loco", "HYPE"), ("3ea", "BTC")):
        store.insert_copy(Copy(coin=coin, symbol=f"{coin}USDT", episode_id=1, side=1, status=CopyStatus.OPEN,
                               qty_initial=Decimal(1), qty_open=Decimal(1), leader_entry_px=Decimal(1),
                               opened_at=now_ms(), leader=leader))
    login(c)
    assert {x["leader"] for x in c.get("/api/copies").json()} == {"loco", "3ea"}
    only = c.get("/api/copies?leader=3ea").json()
    assert [x["coin"] for x in only] == ["BTC"] and only[0]["leader"] == "3ea"
    assert c.get("/api/overview?leader=3ea").json()["stats"]["open_copies"] == 1
    assert c.get("/api/overview").json()["stats"]["open_copies"] == 2


def test_api_store_survives_crossing_threads(tmp_path):
    # FastAPI may open the store in one pool thread and run the handler in another.
    import threading

    store = Store(tmp_path / "x.db", cross_thread=True)
    errors: list[Exception] = []

    def use() -> None:
        try:
            store.users()
        except Exception as e:  # noqa: BLE001 -- surfaced through the assert below
            errors.append(e)

    t = threading.Thread(target=use)
    t.start()
    t.join()
    assert not errors


def _event():
    from loco_copy.tracker import LeaderTracker

    events, _ = LeaderTracker().on_fill(F(0, "HYPE", "B", 40, 10, 0))
    return (events[0],)
