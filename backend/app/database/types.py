"""Custom SQLAlchemy column types shared by every table model."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    """Always round-trips a timezone-aware UTC `datetime`.

    SQLite has no native timezone-aware timestamp type, so a plain
    `DateTime` column would silently return naive values from it — which
    then blow up (`TypeError: can't compare offset-naive and offset-aware
    datetimes`) the moment they meet a fresh `datetime.now(UTC)` anywhere
    else in the codebase. This type normalizes on both write and read so
    every model's datetime fields behave identically on SQLite and
    PostgreSQL.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
