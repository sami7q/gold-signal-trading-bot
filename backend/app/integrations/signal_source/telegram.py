"""Real Telegram signal source (PROJECT_SPEC.md section 19 / Phase 11).

Disabled by default (`TELEGRAM_ENABLED=false`). `telethon` is an optional
dependency (`pip install -e ".[telegram]"`) and is imported lazily so the
rest of the application works without it installed.

Only messages from `TELEGRAM_ALLOWED_CHANNEL_ID` are ever processed — this
adapter must never subscribe to or read from arbitrary chats. It emits the
exact same `IncomingMessageData` the mock source emits, so the parser and
everything downstream needs zero changes when this is switched on.

Read-only, always: this adapter never sends, replies to, edits, or deletes
a Telegram message, never joins/leaves a chat, and never administers
anything — it only listens. See docs/09-telegram-integration.md.

Trading stays in `TRADING_MODE=SIMULATION` (and, while
`TELEGRAM_OBSERVE_ONLY=true`, real messages don't even reach the trading
engine — see `app.trading.service.TradingService`) while this adapter is
validated against the real channel.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.audit.service import AuditService
from app.core.config import Settings
from app.core.constants import AuditEventType, AuditSeverity
from app.core.logging import get_logger
from app.integrations.signal_source.base import SignalSource, SourceHealth
from app.signals.models import IncomingMessageData

logger = get_logger(__name__)

#: Exception text is never guaranteed free of connection details worth
#: keeping out of a UI at a glance; this mirrors the same sanitization
#: TradingEngine._fail_from_exception applies to broker exceptions.
_MAX_ERROR_LENGTH = 300


def _sanitize_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:_MAX_ERROR_LENGTH]}"


class TelegramSignalSource(SignalSource):
    source_name = "telegram"

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._client = None  # type: ignore[var-annotated]
        self._connected = False
        self._authenticated = False
        self._last_message_at: datetime | None = None
        self._last_error: str | None = None
        self._monitor_task: asyncio.Task | None = None
        self._allowed_channel_id = settings.telegram_allowed_channel_id or None

        if not settings.telegram_enabled:
            return
        if not self._allowed_channel_id:
            raise ValueError("TELEGRAM_ALLOWED_CHANNEL_ID must be set when TELEGRAM_ENABLED=true")

    # -- audit trail (opens its own short-lived session — this class runs
    # outside any HTTP request's request-scoped session, same pattern as
    # app.lifecycle._message_handler) --------------------------------------

    def _audit_log(
        self,
        event_type: AuditEventType,
        message: str,
        *,
        severity: AuditSeverity = AuditSeverity.INFO,
        metadata: dict[str, object] | None = None,
    ) -> None:
        from sqlmodel import Session

        from app.database.session import get_engine

        try:
            with Session(get_engine()) as session:
                AuditService(session).log(event_type, message, severity=severity, metadata=metadata)
        except Exception:  # noqa: BLE001 - an audit-log failure must never crash the source
            logger.exception("Failed to write Telegram audit event %s", event_type)

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        if not self._settings.telegram_enabled:
            logger.info("Telegram source disabled (TELEGRAM_ENABLED=false); not starting.")
            return

        # Imported lazily: only required once Telegram is actually enabled.
        from telethon import TelegramClient, events  # type: ignore[import-not-found]

        allowed_channel_id = self._allowed_channel_id
        assert allowed_channel_id is not None  # guaranteed by __init__

        self._audit_log(
            AuditEventType.TELEGRAM_CONNECTING,
            f"Connecting to Telegram (channel={allowed_channel_id})...",
        )

        client = TelegramClient(
            self._settings.telegram_session_path or "telegram_session",
            int(self._settings.telegram_api_id),
            self._settings.telegram_api_hash,
        )
        allowed_channel = int(allowed_channel_id)

        client.on(events.NewMessage(chats=allowed_channel))(
            self._make_event_callback(is_edit=False)
        )
        client.on(events.MessageEdited(chats=allowed_channel))(
            self._make_event_callback(is_edit=True)
        )

        try:
            await client.start()  # type: ignore[func-returns-value]
            self._authenticated = bool(await client.is_user_authorized())
        except Exception as exc:
            self._last_error = _sanitize_error(exc)
            self._audit_log(
                AuditEventType.TELEGRAM_ERROR,
                f"Telegram connection failed: {type(exc).__name__}",
                severity=AuditSeverity.ERROR,
                metadata={
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc)[:500],
                },
            )
            raise

        self._client = client
        self._connected = True
        self._monitor_task = asyncio.create_task(self._monitor_connection())

        self._audit_log(
            AuditEventType.TELEGRAM_CONNECTED,
            f"Telegram connected (read-only, channel={allowed_channel}).",
            metadata={"channel_id": str(allowed_channel), "authenticated": self._authenticated},
        )
        logger.info("Telegram source connected (read-only, channel=%s)", allowed_channel)

    async def stop(self) -> None:
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            self._monitor_task = None
        if self._client is not None:
            await self._client.disconnect()  # type: ignore[union-attr]
        if self._connected:
            self._audit_log(AuditEventType.TELEGRAM_DISCONNECTED, "Telegram source stopped.")
        self._connected = False

    async def health(self) -> SourceHealth:
        enabled = self._settings.telegram_enabled
        return SourceHealth(
            healthy=enabled and self._connected,
            detail=self._health_detail(enabled),
            enabled=enabled,
            connected=self._connected,
            authenticated=self._authenticated,
            allowed_channel_configured=self._allowed_channel_id is not None,
            last_message_at=self._last_message_at,
            last_error=self._last_error,
        )

    def _health_detail(self, enabled: bool) -> str:
        if not enabled:
            return "disabled"
        if not self._connected:
            return "disconnected"
        if not self._authenticated:
            return "connected, not authenticated"
        return "connected"

    # -- connection monitoring -------------------------------------------------

    async def _monitor_connection(self) -> None:
        """Polls `client.is_connected()` and turns state transitions into
        audit events. Telethon's own `auto_reconnect` (default on) handles
        the actual reconnection — this loop only observes and reports it,
        it never re-registers event handlers (which would risk duplicate
        dispatch on reconnect)."""
        assert self._client is not None
        was_connected = True
        try:
            while True:
                await asyncio.sleep(self._settings.telegram_health_poll_seconds)
                is_connected = bool(self._client.is_connected())
                if was_connected and not is_connected:
                    self._connected = False
                    self._audit_log(
                        AuditEventType.TELEGRAM_DISCONNECTED,
                        "Telegram connection lost.",
                        severity=AuditSeverity.WARNING,
                    )
                    self._audit_log(
                        AuditEventType.TELEGRAM_RECONNECTING,
                        "Telethon is attempting to reconnect automatically...",
                        severity=AuditSeverity.WARNING,
                    )
                elif not was_connected and is_connected:
                    self._connected = True
                    self._audit_log(AuditEventType.TELEGRAM_CONNECTED, "Telegram reconnected.")
                was_connected = is_connected
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the monitor loop must never crash the app
            self._last_error = _sanitize_error(exc)
            self._audit_log(
                AuditEventType.TELEGRAM_ERROR,
                f"Telegram connection monitor failed: {type(exc).__name__}",
                severity=AuditSeverity.ERROR,
            )

    # -- message handling -------------------------------------------------

    def _make_event_callback(self, *, is_edit: bool):
        async def _callback(event) -> None:  # type: ignore[no-untyped-def]
            self._handle_event(event, is_edit=is_edit)

        return _callback

    def _handle_event(self, event, *, is_edit: bool) -> None:  # type: ignore[no-untyped-def]
        """Normalizes one Telethon event into `IncomingMessageData` and
        dispatches it. Extracted from the Telethon `@client.on(...)`
        callback specifically so it's directly unit-testable with a plain
        fake `event` object — no real Telethon connection required.

        Defensively re-checks the channel even though `events.NewMessage`
        was already constructed with `chats=allowed_channel`: messages from
        private chats, other channels, groups, Saved Messages, or bots must
        never reach the pipeline under any circumstance.
        """
        try:
            channel_id = str(event.chat_id)
            if channel_id != self._allowed_channel_id:
                logger.warning(
                    "Telegram event from non-allowed chat %s ignored (allowed=%s)",
                    channel_id,
                    self._allowed_channel_id,
                )
                return

            message_id = str(event.message.id)
            reply_to = str(event.message.reply_to_msg_id) if event.message.reply_to_msg_id else None
            self._last_message_at = datetime.now(UTC)

            self._audit_log(
                AuditEventType.TELEGRAM_MESSAGE_RECEIVED,
                f"Telegram message received from channel {channel_id}"
                + (" (edited)" if is_edit else ""),
                metadata={
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "reply_to_message_id": reply_to,
                    "is_edit": is_edit,
                },
            )

            if self._handler is None:
                return

            data = IncomingMessageData(
                source=self.source_name,
                channel_id=channel_id,
                message_id=message_id,
                text=event.raw_text or "",
                sent_at=event.message.date or datetime.now(UTC),
                received_at=datetime.now(UTC),
                reply_to_message_id=reply_to,
            )
            self._handler(data)
        except Exception as exc:  # noqa: BLE001 - a bad/unexpected event must never kill Telethon's dispatch loop
            self._last_error = _sanitize_error(exc)
            logger.exception("Failed to handle Telegram event")
            self._audit_log(
                AuditEventType.TELEGRAM_ERROR,
                f"Failed to handle a Telegram event: {type(exc).__name__}",
                severity=AuditSeverity.ERROR,
            )
