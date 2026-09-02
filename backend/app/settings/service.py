"""Trading settings service (PROJECT_SPEC.md section 13.5 / 27 / 28).

Every change is audited. Switching `mode` to LIVE additionally requires the
caller to pass the exact confirmation phrase — the API layer surfaces
`ValueError` from here as HTTP 400/422 so the dashboard can show a clear
warning instead of silently flipping into real-money trading.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, select

from app.audit.service import AuditService
from app.core.constants import AuditEventType, AuditSeverity, TradingMode
from app.core.decimals import fmt
from app.database.models.trading_settings import TradingSettings
from app.settings.schemas import LIVE_CONFIRMATION_PHRASE, TradingSettingsUpdate


class LiveModeConfirmationError(ValueError):
    pass


class SettingsService:
    def __init__(self, session: Session, audit: AuditService | None = None) -> None:
        self._session = session
        self._audit = audit or AuditService(session)

    def get_for_account(self, account_id: int) -> TradingSettings:
        """One settings row per account (multi-account feature, 2026-09) --
        the most-recently-updated row *for this account_id specifically*;
        a fresh default row is created for it on first access if none
        exists yet, exactly like the old singleton behavior did globally."""
        statement = (
            select(TradingSettings)
            .where(TradingSettings.account_id == account_id)
            .order_by(TradingSettings.updated_at.desc())  # type: ignore[attr-defined]
        )
        settings = self._session.exec(statement).first()
        if settings is None:
            settings = TradingSettings(account_id=account_id)
            self._session.add(settings)
            self._session.commit()
            self._session.refresh(settings)
            self._audit.log(
                AuditEventType.SETTINGS_CHANGED,
                "Default trading settings created (SIMULATION, auto trading OFF).",
                metadata={"account_id": account_id},
            )
        return settings

    def update(self, account_id: int, patch: TradingSettingsUpdate) -> TradingSettings:
        settings = self.get_for_account(account_id)
        changes: dict[str, tuple[object, object]] = {}

        data = patch.model_dump(exclude={"confirm_live_phrase"}, exclude_unset=True)

        if data.get("mode") == TradingMode.LIVE and settings.mode != TradingMode.LIVE:
            if patch.confirm_live_phrase != LIVE_CONFIRMATION_PHRASE:
                raise LiveModeConfirmationError(
                    f'Switching to LIVE requires confirm_live_phrase="{LIVE_CONFIRMATION_PHRASE}".'
                )

        for field, new_value in data.items():
            old_value = getattr(settings, field)
            if old_value != new_value:
                changes[field] = (old_value, new_value)
                setattr(settings, field, new_value)

        if not changes:
            return settings

        settings.updated_at = datetime.now(UTC)
        self._session.add(settings)
        self._session.commit()
        self._session.refresh(settings)

        severity = AuditSeverity.WARNING if "mode" in changes else AuditSeverity.INFO
        change_summary = ", ".join(f"{k}: {fmt(v[0])}->{fmt(v[1])}" for k, v in changes.items())
        self._audit.log(
            AuditEventType.SETTINGS_CHANGED,
            f"Trading settings changed: {change_summary}",
            severity=severity,
            metadata={k: {"from": str(v[0]), "to": str(v[1])} for k, v in changes.items()},
        )
        return settings
