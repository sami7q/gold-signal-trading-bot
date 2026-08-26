"""Structured JSON logging (PROJECT_SPEC.md section 23).

Every record is emitted as one JSON line so it can be shipped to any log
aggregator unchanged. Correlation IDs (request_id / signal_id / trade_id /
message_id) are passed via the `extra=` kwarg on the standard logging calls.

Secrets (Telegram api_hash/session, MT5 password, 2FA codes) must never be
passed into `extra` — this module does not attempt to redact after the fact,
callers are responsible for not logging them in the first place.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

#: Attribute names every stdlib `LogRecord` carries — anything else on a
#: record is caller-supplied `extra=` data and gets folded into the JSON
#: payload as-is (e.g. request_id/signal_id/trade_id/message_id).
_RESERVED = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
