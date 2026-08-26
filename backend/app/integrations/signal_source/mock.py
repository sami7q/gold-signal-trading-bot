"""Mock signal source (PROJECT_SPEC.md section 9.1 / 15.7).

Used by the Simulation API and the test suite to feed messages into the
pipeline exactly as a real Telegram message would arrive — same
`IncomingMessageData` shape, same handler.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.integrations.signal_source.base import SignalSource, SourceHealth


class MockSignalSource(SignalSource):
    source_name = "mock"

    def __init__(self) -> None:
        super().__init__()
        self._started = False
        self._last_message_at: datetime | None = None

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False

    async def health(self) -> SourceHealth:
        return SourceHealth(
            healthy=self._started,
            detail="mock source (always healthy once started)",
            enabled=True,
            connected=self._started,
            authenticated=True,
            allowed_channel_configured=True,
            last_message_at=self._last_message_at,
        )

    def push(self, data) -> None:  # noqa: ANN001 - IncomingMessageData, kept loose to avoid import cycle in signature
        """Synchronously deliver one message to the registered handler.

        This is what `POST /api/simulation/messages` and the test suite call.
        """
        if self._handler is None:
            raise RuntimeError("MockSignalSource has no registered handler")
        self._last_message_at = datetime.now(UTC)
        self._handler(data)
