"""Clean Decimal (de)serialization for API responses.

SQLite has no native DECIMAL type; SQLAlchemy's `Numeric` falls back to
reconstructing `Decimal` values with a default return scale of 10 places,
so a stored `0.20` round-trips as `0.2000000000`. Storage precision is left
untouched (broker prices may legitimately need more than 2 decimals — see
PROJECT_SPEC.md section 10) — only the JSON representation is normalized.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer


def clean_decimal(value: Decimal) -> Decimal:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        # `Decimal.normalize()` renders whole numbers in scientific notation
        # (e.g. Decimal("100.00").normalize() == Decimal("1E+2")); avoid that.
        return normalized.quantize(Decimal(1))
    return normalized


CleanDecimal = Annotated[
    Decimal, PlainSerializer(clean_decimal, return_type=Decimal, when_used="json")
]


def fmt(value: object) -> str:
    """Human-readable form of any value for free-text audit log messages.

    `Decimal` fields read back from the DB carry the same 10-decimal-place
    padding `clean_decimal` exists to strip (see module docstring) — this is
    the same normalization applied to log *text*, so a message reads
    "lot=0.1" instead of "lot=0.1000000000". Structured `metadata_json` is
    untouched — it should keep the exact stored Decimal, this is only for
    the human-readable `message` string.
    """
    if isinstance(value, Decimal):
        return str(clean_decimal(value))
    return str(value)
