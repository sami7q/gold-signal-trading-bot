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
    eff = with_mode_override(s, store.get_state("mode_override"))
    assert eff.copy_enabled and eff.copy_observe_only
    assert c.post("/api/control/mode", json={"mode": "EXECUTE", "confirm": EXECUTE_CONFIRM}).status_code == 200
    eff = with_mode_override(s, store.get_state("mode_override"))
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
    store.set_state("leader_snapshot", {"taken_at_ms": now_ms(), "account_value": "467000",
                                        "positions": {"HYPE": "7250", "BTC": "-0.5"}, "entry_px": {"HYPE": "88"}})
    store.set_state("tracker", {"BTC": {"synced": False}})
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
