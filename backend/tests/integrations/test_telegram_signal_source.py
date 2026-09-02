"""Phase 11: real Telegram read-only integration.

No real Telegram connection is ever made in this file — `_handle_event` is
tested directly with plain fake objects (no Telethon network needed at
all), and `start()`/`stop()`/reconnect-monitoring are tested against a fake
`TelegramClient` substituted for the real one. This matches the task's own
instruction: "Add tests without contacting Telegram wherever possible."
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
import telethon
from sqlmodel import select

from app.core.config import Settings
from app.core.constants import AuditEventType, SignalStatus
from app.database.models.audit_event import AuditEvent
from app.database.models.signal import Signal
from app.integrations.broker.paper import PaperBroker
from app.integrations.signal_source.telegram import TelegramSignalSource
from app.signals.models import IncomingMessageData
from app.trading.service import TradingService

ALLOWED_CHANNEL = "1001112223334"


def _settings(**overrides) -> Settings:
    defaults = dict(
        telegram_enabled=True,
        telegram_api_id="12345",
        telegram_api_hash="fake-hash-not-real",
        telegram_session_path="unused-in-tests",
        telegram_allowed_channel_id=ALLOWED_CHANNEL,
        telegram_observe_only=True,
        telegram_health_poll_seconds=0,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class FakeMessage:
    def __init__(self, id, date, reply_to_msg_id=None):  # noqa: A002
        self.id = id
        self.date = date
        self.reply_to_msg_id = reply_to_msg_id


class FakeEvent:
    def __init__(self, chat_id, message, raw_text):
        self.chat_id = chat_id
        self.message = message
        self.raw_text = raw_text


def _broker() -> PaperBroker:
    from decimal import Decimal

    return PaperBroker(
        starting_balance=Decimal("10000"),
        symbol="XAUUSD",
        point=Decimal("0.01"),
        digits=2,
        contract_size=Decimal("100"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("50"),
        volume_step=Decimal("0.01"),
        spread_points=20,
        starting_price=Decimal("4638.30"),
    )


# -- _handle_event: pure normalization, no Telethon connection needed --------


def test_normalizes_new_message_correctly(session):
    source = TelegramSignalSource(_settings())
    received: list[IncomingMessageData] = []
    source.on_message(received.append)

    sent_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    event = FakeEvent(
        chat_id=int(ALLOWED_CHANNEL),
        message=FakeMessage(id=555, date=sent_at, reply_to_msg_id=None),
        raw_text="BUY GOLD",
    )
    source._handle_event(event, is_edit=False)

    assert len(received) == 1
    data = received[0]
    assert data.source == "telegram"
    assert data.channel_id == ALLOWED_CHANNEL
    assert data.message_id == "555"
    assert data.text == "BUY GOLD"
    assert data.sent_at == sent_at
    assert data.reply_to_message_id is None


def test_allowed_channel_message_accepted(session):
    source = TelegramSignalSource(_settings())
    received = []
    source.on_message(received.append)

    event = FakeEvent(int(ALLOWED_CHANNEL), FakeMessage(1, datetime.now(UTC)), "BUY GOLD")
    source._handle_event(event, is_edit=False)

    assert len(received) == 1


def test_photo_with_no_caption_is_normalized_to_empty_text_and_never_crashes(session):
    """A photo/chart image with no caption has `raw_text == ""` in Telethon
    -- it must reach the pipeline as an empty-text message (safely
    classified IGNORED downstream, never a signal), not crash the handler
    or the dispatch loop."""
    source = TelegramSignalSource(_settings())
    received = []
    source.on_message(received.append)

    photo_event = FakeEvent(int(ALLOWED_CHANNEL), FakeMessage(1, datetime.now(UTC)), "")
    source._handle_event(photo_event, is_edit=False)

    assert len(received) == 1
    assert received[0].text == ""
    health = asyncio.run(source.health())
    assert health.last_error is None


def test_photo_with_no_caption_never_creates_a_signal_end_to_end(session, seed_account):
    broker = _broker()
    service = TradingService(
        session, broker, account_id=seed_account.id, signal_completion_timeout_seconds=120
    )
    data = IncomingMessageData(
        source="telegram",
        channel_id=ALLOWED_CHANNEL,
        message_id="photo-1",
        text="",
        sent_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
    )

    result = service.process_incoming_message(data)

    assert result.signal is None
    assert result.parsed is not None
    assert result.parsed.classification.value == "IGNORED"
    assert session.exec(select(Signal)).all() == []


def test_photo_with_a_valid_caption_still_works_normally(session):
    """A photo *with* a caption carries the caption text in `raw_text` --
    this must still be read as a normal command, proving photos aren't
    blanket-blocked, only ones with no actionable text."""
    source = TelegramSignalSource(_settings())
    received = []
    source.on_message(received.append)

    captioned_event = FakeEvent(int(ALLOWED_CHANNEL), FakeMessage(1, datetime.now(UTC)), "BUY GOLD")
    source._handle_event(captioned_event, is_edit=False)

    assert len(received) == 1
    assert received[0].text == "BUY GOLD"


def test_wrong_channel_ignored(session):
    source = TelegramSignalSource(_settings())
    received = []
    source.on_message(received.append)

    other_channel_event = FakeEvent(
        int("99999999999"), FakeMessage(1, datetime.now(UTC)), "BUY GOLD"
    )
    source._handle_event(other_channel_event, is_edit=False)

    assert received == []


def test_reply_to_message_id_preserved(session):
    source = TelegramSignalSource(_settings())
    received = []
    source.on_message(received.append)

    event = FakeEvent(
        int(ALLOWED_CHANNEL),
        FakeMessage(id=2, date=datetime.now(UTC), reply_to_msg_id=1),
        "SL 4623",
    )
    source._handle_event(event, is_edit=False)

    assert received[0].reply_to_message_id == "1"


def test_telegram_error_does_not_crash_trading_engine(session):
    source = TelegramSignalSource(_settings())

    def _raising_handler(data):
        raise RuntimeError("simulated downstream failure")

    source.on_message(_raising_handler)
    event = FakeEvent(int(ALLOWED_CHANNEL), FakeMessage(1, datetime.now(UTC)), "BUY GOLD")

    # Must not raise.
    source._handle_event(event, is_edit=False)
    assert source._last_error is not None
    assert "RuntimeError" in source._last_error


def test_handle_event_writes_telegram_message_received_audit_event(session):
    source = TelegramSignalSource(_settings())
    source.on_message(lambda data: None)
    event = FakeEvent(
        int(ALLOWED_CHANNEL),
        FakeMessage(id=7, date=datetime.now(UTC), reply_to_msg_id=3),
        "BUY GOLD",
    )
    source._handle_event(event, is_edit=False)

    events = list(session.exec(select(AuditEvent)))
    received = [e for e in events if e.event_type == AuditEventType.TELEGRAM_MESSAGE_RECEIVED.value]
    assert len(received) == 1
    assert '"message_id": "7"' in received[0].metadata_json
    assert '"reply_to_message_id": "3"' in received[0].metadata_json
    # The raw signal text must never be duplicated into the audit trail.
    assert "BUY GOLD" not in received[0].message
    assert "BUY GOLD" not in received[0].metadata_json


# -- edited messages: same identity, dedup handles the rest -----------------


def test_edited_message_cannot_create_a_second_execution(session, seed_account):
    broker = _broker()
    source = TelegramSignalSource(_settings(telegram_observe_only=False))
    service = TradingService(session, broker, account_id=seed_account.id)
    source.on_message(service.process_incoming_message)

    original = FakeEvent(
        int(ALLOWED_CHANNEL), FakeMessage(id=42, date=datetime.now(UTC)), "BUY GOLD"
    )
    source._handle_event(original, is_edit=False)

    # Same message id, edited text -- must be treated as the existing
    # message (duplicate), never processed as new content.
    edited = FakeEvent(
        int(ALLOWED_CHANNEL), FakeMessage(id=42, date=datetime.now(UTC)), "BUY GOLD (edited)"
    )
    source._handle_event(edited, is_edit=True)

    from app.database.models.signal import Signal

    signals = list(session.exec(select(Signal)))
    assert len(signals) == 1  # the edit never created a second signal


# -- start()/stop(): fake TelegramClient, zero real network ------------------


class FakeTelegramClient:
    """Stands in for telethon.TelegramClient. Records every `.on()`
    registration so tests can prove handlers are registered exactly once."""

    def __init__(self, session_path, api_id, api_hash) -> None:
        self.session_path = session_path
        self.api_id = api_id
        self.api_hash = api_hash
        self.on_registrations: list[object] = []
        self.started = False
        self.disconnected = False
        self._connected = True
        self.authorized = True

    def on(self, event_builder):
        self.on_registrations.append(event_builder)

        def decorator(callback):
            return callback

        return decorator

    async def start(self):
        self.started = True

    async def is_user_authorized(self):
        return self.authorized

    def is_connected(self):
        return self._connected

    async def disconnect(self):
        self.disconnected = True
        self._connected = False


def test_start_registers_handlers_exactly_once_and_reports_connected(monkeypatch, session):
    fake_client = FakeTelegramClient("path", 1, "hash")
    monkeypatch.setattr(telethon, "TelegramClient", lambda *a, **k: fake_client)

    source = TelegramSignalSource(_settings())
    asyncio.run(source.start())

    assert fake_client.started is True
    assert len(fake_client.on_registrations) == 2  # NewMessage + MessageEdited, no more

    health = asyncio.run(source.health())
    assert health.enabled is True
    assert health.connected is True
    assert health.authenticated is True
    assert health.allowed_channel_configured is True

    asyncio.run(source.stop())
    assert fake_client.disconnected is True


def test_start_failure_is_captured_in_health_and_reraised(monkeypatch, session):
    class _ExplodingClient(FakeTelegramClient):
        async def start(self):
            raise ConnectionError("simulated network failure")

    monkeypatch.setattr(telethon, "TelegramClient", lambda *a, **k: _ExplodingClient("p", 1, "h"))

    source = TelegramSignalSource(_settings())
    with pytest.raises(ConnectionError):
        asyncio.run(source.start())

    health = asyncio.run(source.health())
    assert health.connected is False
    assert health.last_error is not None
    assert "ConnectionError" in health.last_error

    events = {e.event_type for e in session.exec(select(AuditEvent))}
    assert AuditEventType.TELEGRAM_ERROR.value in events
    assert AuditEventType.TELEGRAM_CONNECTING.value in events


def test_reconnect_monitor_does_not_reregister_handlers(monkeypatch, session):
    fake_client = FakeTelegramClient("path", 1, "hash")
    monkeypatch.setattr(telethon, "TelegramClient", lambda *a, **k: fake_client)

    source = TelegramSignalSource(_settings(telegram_health_poll_seconds=0))

    async def _run():
        await source.start()
        assert len(fake_client.on_registrations) == 2

        # Simulate a disconnect/reconnect cycle observed by the monitor loop.
        fake_client._connected = False
        await asyncio.sleep(0.01)
        fake_client._connected = True
        await asyncio.sleep(0.01)

        # The monitor must only ever have called .on() during start() —
        # never again while observing connection state.
        assert len(fake_client.on_registrations) == 2
        await source.stop()

    asyncio.run(_run())

    event_types = [e.event_type for e in session.exec(select(AuditEvent))]
    assert AuditEventType.TELEGRAM_DISCONNECTED.value in event_types
    assert AuditEventType.TELEGRAM_RECONNECTING.value in event_types


def test_telegram_disabled_never_touches_telethon(monkeypatch, session):
    def _fail(*a, **k):
        raise AssertionError("TelegramClient must never be constructed when TELEGRAM_ENABLED=false")

    monkeypatch.setattr(telethon, "TelegramClient", _fail)

    source = TelegramSignalSource(_settings(telegram_enabled=False, telegram_allowed_channel_id=""))
    asyncio.run(source.start())  # must return immediately, must not raise

    health = asyncio.run(source.health())
    assert health.enabled is False
    assert health.healthy is False


# -- full signal-flow behavior through TradingService, source="telegram" ----


def _msg(text: str, mid: str, *, channel: str = ALLOWED_CHANNEL, reply_to: str | None = None):
    now = datetime.now(UTC)
    return IncomingMessageData(
        source="telegram",
        channel_id=channel,
        message_id=mid,
        text=text,
        sent_at=now,
        received_at=now,
        reply_to_message_id=reply_to,
    )


def test_buy_signal_enters_waiting_for_details(session, seed_account):
    service = TradingService(session, _broker(), account_id=seed_account.id)
    result = service.process_incoming_message(_msg("BUY GOLD", "t1"))
    assert result.signal.status == SignalStatus.WAITING_FOR_DETAILS


def test_target_only_message_creates_no_trade(session, seed_account):
    from app.database.models.signal import Signal
    from app.database.models.trade import Trade

    service = TradingService(session, _broker(), account_id=seed_account.id)
    service.process_incoming_message(_msg("4644\n4650\n4663\n4677", "t1"))

    assert list(session.exec(select(Signal))) == []
    assert list(session.exec(select(Trade))) == []


def test_duplicate_telegram_event_blocked(session, seed_account):
    service = TradingService(session, _broker(), account_id=seed_account.id)
    first = service.process_incoming_message(_msg("BUY GOLD", "dup-1"))
    second = service.process_incoming_message(_msg("BUY GOLD", "dup-1"))

    assert first.duplicate is False
    assert second.duplicate is True

    from app.database.models.signal import Signal

    assert len(list(session.exec(select(Signal)))) == 1


def test_two_waiting_signals_generic_sl_is_ambiguous_no_trade(session, seed_account):
    service = TradingService(session, _broker(), account_id=seed_account.id)
    service.process_incoming_message(_msg("BUY GOLD", "amb-a"))
    service.process_incoming_message(_msg("BUY GOLD", "amb-b"))
    result = service.process_incoming_message(_msg("SL 4623", "amb-sl"))

    assert result.ambiguous is True
    from app.database.models.trade import Trade

    assert list(session.exec(select(Trade))) == []


def test_two_waiting_signals_telegram_reply_resolves_correct_signal_only(session, seed_account):
    service = TradingService(
        session, _broker(), account_id=seed_account.id, signal_completion_timeout_seconds=120
    )
    a = service.process_incoming_message(_msg("BUY GOLD", "rep-a"))
    service.process_incoming_message(_msg("BUY GOLD", "rep-b"))

    result = service.process_incoming_message(_msg("SL 4623", "rep-a-sl", reply_to="rep-a"))

    assert result.ambiguous is False
    assert result.signal.id == a.signal.id
    session.refresh(a.signal)
    assert a.signal.status in (SignalStatus.READY, SignalStatus.EXECUTED)


def test_buy_plus_sl_reaches_ready_but_observe_only_blocks_execution(session, seed_account):
    """TELEGRAM_OBSERVE_ONLY defaults true: the signal must still reach
    READY (parser/state-machine validated), but the trading engine must
    never be invoked -- no trade, no execution."""
    from app.core.config import get_settings
    from app.database.models.trade import Trade

    get_settings.cache_clear()
    import os

    os.environ["TELEGRAM_OBSERVE_ONLY"] = "true"
    try:
        service = TradingService(session, _broker(), account_id=seed_account.id)
        service.process_incoming_message(_msg("BUY GOLD", "obs-a"))
        result = service.process_incoming_message(_msg("4640 - 4637\nSL 4623", "obs-b"))

        assert result.became_ready is True
        session.refresh(result.signal)
        assert result.signal.status == SignalStatus.READY  # not EXECUTED
        assert list(session.exec(select(Trade))) == []

        events = {e.event_type for e in session.exec(select(AuditEvent))}
        assert AuditEventType.SIGNAL_OBSERVED_ONLY.value in events
    finally:
        os.environ.pop("TELEGRAM_OBSERVE_ONLY", None)
        get_settings.cache_clear()


def test_buy_plus_sl_executes_when_observe_only_disabled(session, seed_account):
    from app.core.config import get_settings
    from app.database.models.trade import Trade
    from app.settings.schemas import TradingSettingsUpdate
    from app.settings.service import SettingsService

    get_settings.cache_clear()
    import os

    os.environ["TELEGRAM_OBSERVE_ONLY"] = "false"
    try:
        SettingsService(session).update(
            seed_account.id, TradingSettingsUpdate(auto_trading=True, buy_enabled=True)
        )
        service = TradingService(session, _broker(), account_id=seed_account.id)
        service.process_incoming_message(_msg("BUY GOLD", "live-a"))
        result = service.process_incoming_message(_msg("4640 - 4637\nSL 4623", "live-b"))

        session.refresh(result.signal)
        assert result.signal.status == SignalStatus.EXECUTED
        assert len(list(session.exec(select(Trade)))) == 1
    finally:
        os.environ.pop("TELEGRAM_OBSERVE_ONLY", None)
        get_settings.cache_clear()


def test_mock_source_messages_are_never_gated_by_observe_only(session, seed_account):
    """TELEGRAM_OBSERVE_ONLY must only ever affect source="telegram" -- the
    Simulation page (source="mock") must always execute normally regardless
    of this flag, or dashboard-driven testing would silently stop working."""
    from app.core.config import get_settings
    from app.database.models.trade import Trade

    get_settings.cache_clear()
    import os

    os.environ["TELEGRAM_OBSERVE_ONLY"] = "true"
    try:
        from app.settings.schemas import TradingSettingsUpdate
        from app.settings.service import SettingsService

        SettingsService(session).update(
            seed_account.id, TradingSettingsUpdate(auto_trading=True, buy_enabled=True)
        )
        service = TradingService(session, _broker(), account_id=seed_account.id)
        now = datetime.now(UTC)
        service.process_incoming_message(
            IncomingMessageData(
                source="mock",
                channel_id="c",
                message_id="m1",
                text="BUY GOLD",
                sent_at=now,
                received_at=now,
            )
        )
        result = service.process_incoming_message(
            IncomingMessageData(
                source="mock",
                channel_id="c",
                message_id="m2",
                text="4640 - 4637\nSL 4623",
                sent_at=now,
                received_at=now,
            )
        )
        session.refresh(result.signal)
        assert result.signal.status == SignalStatus.EXECUTED
        assert len(list(session.exec(select(Trade)))) == 1
    finally:
        os.environ.pop("TELEGRAM_OBSERVE_ONLY", None)
        get_settings.cache_clear()
