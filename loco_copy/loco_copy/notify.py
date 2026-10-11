"""Every decision goes to three places: the `log` table, `data/logs/loco.log` (JSON lines) and the console.
WARNING and above can also go to an optional outbound-only Telegram bot (send-only; it never reads anything).
"""

from __future__ import annotations

import asyncio
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import httpx
import structlog

from loco_copy.store import Store

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def configure_logging(log_dir: Path, *, console: bool = True, filename: str = "loco.log") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(log_dir / filename, maxBytes=10_000_000, backupCount=5, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=handlers, force=True)
    for noisy in ("httpx", "httpcore", "websockets", "pybit", "urllib3", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # a request every 2 s would bury every decision
    structlog.configure(
        processors=[structlog.processors.TimeStamper(fmt="iso", utc=True), structlog.processors.add_log_level,
                    structlog.processors.JSONRenderer()],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


class Notifier:
    def __init__(self, store: Store, *, telegram_token: str = "", telegram_chat_id: str = "",
                 dedupe_seconds: int = 600, echo: bool = True) -> None:
        self._store = store
        self._echo = echo
        self._token = telegram_token
        self._chat = telegram_chat_id
        self._dedupe = dedupe_seconds
        self._last_sent: dict[str, float] = {}
        self._pending: set[asyncio.Task[None]] = set()
        self._log = structlog.get_logger("loco_copy")

    def emit(self, level: str, type_: str, **payload: Any) -> None:
        self._store.log(level, type_, payload)
        if self._echo:
            self._log.log(_LEVELS[level], type_, **payload)
        if _LEVELS[level] >= _LEVELS["WARNING"] and self._token and self._chat:
            key = f"{type_}:{payload.get('coin', '')}:{payload.get('symbol', '')}"
            now = time.monotonic()
            if now - self._last_sent.get(key, -1e9) >= self._dedupe:
                self._last_sent[key] = now
                self._send_later(f"[loco-copy] {level} {type_}\n{payload}")

    def info(self, type_: str, **payload: Any) -> None:
        self.emit("INFO", type_, **payload)

    def warning(self, type_: str, **payload: Any) -> None:
        self.emit("WARNING", type_, **payload)

    def critical(self, type_: str, **payload: Any) -> None:
        self.emit("CRITICAL", type_, **payload)

    def _send_later(self, text: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._send(text))
        self._pending.add(task)  # keep a reference so the task isn't garbage-collected mid-send
        task.add_done_callback(self._pending.discard)

    async def _send(self, text: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"https://api.telegram.org/bot{self._token}/sendMessage",
                                  json={"chat_id": self._chat, "text": text[:3900]})
        except httpx.HTTPError as exc:
            self._log.warning("ALERT_SEND_FAILED", error=str(exc))
