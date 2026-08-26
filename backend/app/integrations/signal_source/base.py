"""Signal source adapter interface (PROJECT_SPEC.md section 5.1).

The trading engine — and everything downstream of it — must never know
whether a message came from Telegram, a test fixture, or the dashboard's
Simulation page. Every implementation emits the same `IncomingMessageData`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.signals.models import IncomingMessageData

#: Called by a source implementation for every normalized message it receives.
#: Synchronous by design: the whole ingestion -> parser -> rules -> broker
#: pipeline is synchronous (see docs/01-architecture.md), and the only
#: source that truly needs an asyncio event loop (Telegram/Telethon) simply
#: calls this handler directly from its async callback — the handler itself
#: does a handful of fast local DB writes, never network I/O.
MessageHandler = Callable[[IncomingMessageData], None]


@dataclass(frozen=True, slots=True)
class SourceHealth:
    """Enough detail for `/api/health/signal-source` and the dashboard status
    strip to distinguish "mock, always fine" from every real Telegram state
    an operator running this unattended actually needs to see."""

    healthy: bool
    detail: str = ""
    enabled: bool = True
    connected: bool = False
    authenticated: bool = False
    allowed_channel_configured: bool = False
    last_message_at: datetime | None = None
    #: Never a raw exception/credential string — see
    #: TelegramSignalSource's own docstring for the sanitization rule.
    last_error: str | None = None


class SignalSource(ABC):
    """Base contract every signal source (mock or real) must satisfy."""

    #: Set by every concrete subclass ("mock", "telegram", ...).
    source_name: str

    def __init__(self) -> None:
        self._handler: MessageHandler | None = None

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def health(self) -> SourceHealth: ...
