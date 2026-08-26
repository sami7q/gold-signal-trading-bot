from __future__ import annotations


class TradingError(Exception):
    """Base class for trading-domain errors."""


class DuplicateExecutionError(TradingError):
    """Raised when an execution_key already has a non-failed/non-rejected trade."""


class BrokerUnavailableError(TradingError):
    """Raised when the configured broker cannot be reached or is unhealthy."""
