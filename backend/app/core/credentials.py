"""At-rest encryption for MT5Account passwords (multi-account feature,
2026-09). Fernet, not `hash_password` -- a login password is only ever
compared, never needed back in plaintext, so a one-way hash is correct
there. An MT5 account password must be recovered in plaintext to actually
log into MT5 (Phase 2), so it needs *reversible* encryption instead.

The key is read lazily, not at import/app-startup time: nothing in the
Phase 1 live trading path (MT5Broker.connect(), still reading from
`.env`/Settings directly, unchanged by this feature) depends on this key,
so a missing/malformed key must never block the app from booting or
trading -- it only needs to exist once an operator actually tries to
add/edit an MT5Account row via the dashboard.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class CredentialsNotConfiguredError(RuntimeError):
    """ACCOUNT_CREDENTIALS_ENCRYPTION_KEY is unset or malformed."""


@lru_cache
def _get_fernet() -> Fernet:
    key = get_settings().account_credentials_encryption_key
    if not key:
        raise CredentialsNotConfiguredError(
            "ACCOUNT_CREDENTIALS_ENCRYPTION_KEY is not set. Generate one with "
            '`python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"` and add it to .env '
            "(never commit it) before adding or editing any MT5 account."
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise CredentialsNotConfiguredError(
            "ACCOUNT_CREDENTIALS_ENCRYPTION_KEY is set but not a valid Fernet key."
        ) from exc


def encrypt_mt5_password(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_mt5_password(ciphertext: str) -> str:
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CredentialsNotConfiguredError(
            "Stored MT5 credential could not be decrypted -- the encryption "
            "key may have changed since it was saved."
        ) from exc


def reset_credentials_cache() -> None:
    """Test-only: drop the cached Fernet instance so a test can change the
    configured key mid-session and see the new one take effect."""
    _get_fernet.cache_clear()
