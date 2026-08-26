"""API smoke tests covering PROJECT_SPEC.md section 16."""

from __future__ import annotations

from datetime import UTC, datetime


def test_health_endpoints(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/health/broker").json()["healthy"] is True
    body = client.get("/api/health/signal-source").json()
    assert body["healthy"] is True
    assert body["source"] == "mock"


def test_signal_source_health_reflects_telegram_once_active(client):
    """`/api/health/signal-source` must show whichever source is actually
    active, not a hardcoded mock — proven here without a real Telegram
    connection by activating a stub source with the same interface
    (PROJECT_SPEC.md Phase 11 section 9)."""
    from app.core.runtime import set_active_signal_source
    from app.integrations.signal_source.base import SignalSource, SourceHealth

    class _StubTelegramSource(SignalSource):
        source_name = "telegram"

        async def start(self) -> None: ...
        async def stop(self) -> None: ...

        async def health(self) -> SourceHealth:
            return SourceHealth(
                healthy=True,
                detail="connected",
                enabled=True,
                connected=True,
                authenticated=True,
                allowed_channel_configured=True,
                last_message_at=datetime(2026, 1, 1, tzinfo=UTC),
                last_error=None,
            )

    set_active_signal_source(_StubTelegramSource())

    body = client.get("/api/health/signal-source").json()
    assert body["source"] == "telegram"
    assert body["healthy"] is True
    assert body["connected"] is True
    assert body["authenticated"] is True
    assert body["allowed_channel_configured"] is True
    assert body["last_message_at"] is not None
    assert body["last_error"] is None


def test_settings_get_and_patch(client):
    settings = client.get("/api/settings").json()
    assert settings["mode"] == "SIMULATION"
    assert settings["auto_trading"] is False

    updated = client.patch("/api/settings", json={"auto_trading": True, "lot_size": "0.20"})
    assert updated.status_code == 200
    assert updated.json()["auto_trading"] is True
    assert updated.json()["lot_size"] == "0.2"


def test_switching_to_live_requires_confirmation_phrase(client):
    response = client.patch("/api/settings", json={"mode": "LIVE"})
    assert response.status_code == 400

    response = client.patch(
        "/api/settings", json={"mode": "LIVE", "confirm_live_phrase": "ENABLE LIVE TRADING"}
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "LIVE"


def test_full_simulation_flow_over_http(client):
    client.patch(
        "/api/settings",
        json={
            "auto_trading": True,
            "buy_enabled": True,
            "lot_size": "0.10",
            "target_pips": "100",
            "max_open_trades": 1,
            "require_stop_loss": True,
        },
    )

    state = client.post("/api/simulation/market-price", json={"bid": "4638.30"}).json()
    assert state["bid"] == "4638.3"

    r1 = client.post("/api/simulation/messages", json={"text": "BUY GOLD", "message_id": "m1"})
    assert r1.json()["signal_status"] == "WAITING_FOR_DETAILS"

    r2 = client.post(
        "/api/simulation/messages", json={"text": "4640 - 4637\nSL 4623", "message_id": "m2"}
    )
    assert r2.json()["became_ready"] is True
    assert r2.json()["signal_status"] == "EXECUTED"

    trades = client.get("/api/trades").json()
    assert len(trades) == 1
    assert trades[0]["status"] == "OPEN"

    closed_state = client.post("/api/simulation/trigger-tp").json()
    assert closed_state["open_positions"] == []

    trades = client.get("/api/trades").json()
    assert trades[0]["status"] == "CLOSED_TP"
    assert trades[0]["result"] == "WIN"

    signals = client.get("/api/signals").json()
    assert signals[0]["status"] == "EXECUTED"
    assert [m["role"] for m in signals[0]["messages"]] == ["ENTRY", "STOP_LOSS"]
    assert signals[0]["messages"][1]["raw_text"] == "4640 - 4637\nSL 4623"

    logs = client.get("/api/logs").json()
    assert any(event["event_type"] == "TRADE_OPENED" for event in logs)

    summary = client.get("/api/dashboard/summary").json()
    assert summary["summary"]["statistics"]["wins"] == 1

    # Duplicate delivery of the same message must not create a second trade.
    dup = client.post("/api/simulation/messages", json={"text": "BUY GOLD", "message_id": "m1"})
    assert dup.json()["duplicate"] is True
    assert len(client.get("/api/trades").json()) == 1


def test_ambiguous_signal_details_over_http_creates_no_trade(client):
    client.patch(
        "/api/settings",
        json={"auto_trading": True, "buy_enabled": True, "lot_size": "0.10", "target_pips": "100"},
    )
    client.post("/api/simulation/market-price", json={"bid": "4638.30"})

    client.post("/api/simulation/messages", json={"text": "BUY GOLD", "message_id": "amb-a1"})
    client.post("/api/simulation/messages", json={"text": "BUY GOLD", "message_id": "amb-b1"})

    ambiguous = client.post(
        "/api/simulation/messages", json={"text": "SL 4623", "message_id": "amb-generic"}
    ).json()
    assert ambiguous["ambiguous"] is True
    assert ambiguous["signal_id"] is None
    assert ambiguous["became_ready"] is False
    assert client.get("/api/trades").json() == []

    statuses = {s["id"]: s["status"] for s in client.get("/api/signals").json()}
    assert set(statuses.values()) == {"WAITING_FOR_DETAILS"}


def test_reply_to_message_id_resolves_ambiguity_over_http(client):
    client.patch(
        "/api/settings",
        json={"auto_trading": True, "buy_enabled": True, "lot_size": "0.10", "target_pips": "100"},
    )
    client.post("/api/simulation/market-price", json={"bid": "4638.30"})

    a = client.post(
        "/api/simulation/messages", json={"text": "BUY GOLD", "message_id": "rep-a1"}
    ).json()
    client.post("/api/simulation/messages", json={"text": "BUY GOLD", "message_id": "rep-b1"})

    resolved = client.post(
        "/api/simulation/messages",
        json={"text": "SL 4623", "message_id": "rep-a2", "reply_to_message_id": "rep-a1"},
    ).json()
    assert resolved["ambiguous"] is False
    assert resolved["signal_id"] == a["signal_id"]
    assert resolved["signal_status"] == "EXECUTED"

    trades = client.get("/api/trades").json()
    assert len(trades) == 1
    assert trades[0]["signal_id"] == a["signal_id"]

    statuses = {s["id"]: s["status"] for s in client.get("/api/signals").json()}
    assert statuses[a["signal_id"]] == "EXECUTED"
    other_signal_id = next(sid for sid in statuses if sid != a["signal_id"])
    assert statuses[other_signal_id] == "WAITING_FOR_DETAILS"


def test_pip_preview_uses_the_same_math_as_the_trading_engine(client):
    client.post("/api/simulation/market-price", json={"bid": "4638.30"})

    preview = client.get("/api/settings/pip-preview?target_pips=100").json()
    assert preview["symbol"] == "XAUUSD"
    assert preview["broker_healthy"] is True
    assert preview["point"] == "0.01"
    # Gold pip size is 10x point (confirmed against a real MT5 Demo XAUUSD
    # symbol -- digits=2, point=0.01, 1 pip=$0.10 -- see app/trading/pips.py).
    assert preview["pip_size"] == "0.1"
    assert preview["target_pips"] == "100"
    assert preview["price_distance"] == "10"
    assert preview["example_entry"] == "4638.5"
    assert preview["calculated_take_profit"] == "4648.5"

    default_preview = client.get("/api/settings/pip-preview").json()
    assert default_preview["target_pips"] == "100"  # falls back to current settings.target_pips

    rejected = client.get("/api/settings/pip-preview?target_pips=0")
    assert rejected.status_code == 422


def test_simulation_endpoints_disabled_in_live_mode(client):
    client.patch(
        "/api/settings", json={"mode": "LIVE", "confirm_live_phrase": "ENABLE LIVE TRADING"}
    )
    response = client.post("/api/simulation/messages", json={"text": "BUY GOLD"})
    assert response.status_code == 403
