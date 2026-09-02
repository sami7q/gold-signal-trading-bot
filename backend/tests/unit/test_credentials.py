"""Fernet round-trip for MT5 account passwords at rest (app/core/credentials.py).

Reversible encryption, deliberately not `hash_password` -- see the module's
own docstring for why an MT5 password needs plaintext back."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.config import get_settings
from app.core.credentials import (
    CredentialsNotConfiguredError,
    decrypt_mt5_password,
    encrypt_mt5_password,
    reset_credentials_cache,
)


@pytest.fixture(autouse=True)
def _reset_cache():
    """Every test in this file mutates the env var -- always start and end
    with a clean cached Fernet instance and a cleared Settings cache."""
    reset_credentials_cache()
    get_settings.cache_clear()
    yield
    reset_credentials_cache()
    get_settings.cache_clear()


def test_round_trip_recovers_the_exact_plaintext():
    plaintext = "s3cret-mt5-password!"
    ciphertext = encrypt_mt5_password(plaintext)

    assert ciphertext != plaintext
    assert decrypt_mt5_password(ciphertext) == plaintext


def test_ciphertext_is_not_the_plaintext_substring():
    plaintext = "a-very-recognizable-password-123"
    ciphertext = encrypt_mt5_password(plaintext)
    assert plaintext not in ciphertext


def test_missing_key_raises_a_clear_configured_error(monkeypatch):
    monkeypatch.setenv("ACCOUNT_CREDENTIALS_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    reset_credentials_cache()

    with pytest.raises(CredentialsNotConfiguredError):
        encrypt_mt5_password("whatever")


def test_malformed_key_raises_a_clear_configured_error(monkeypatch):
    monkeypatch.setenv("ACCOUNT_CREDENTIALS_ENCRYPTION_KEY", "not-a-real-fernet-key")
    get_settings.cache_clear()
    reset_credentials_cache()

    with pytest.raises(CredentialsNotConfiguredError):
        encrypt_mt5_password("whatever")


def test_decrypting_with_a_different_key_than_it_was_encrypted_with_fails_closed(monkeypatch):
    ciphertext = encrypt_mt5_password("original-password")

    monkeypatch.setenv("ACCOUNT_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    reset_credentials_cache()

    with pytest.raises(CredentialsNotConfiguredError):
        decrypt_mt5_password(ciphertext)


def test_two_different_plaintexts_never_collide():
    a = encrypt_mt5_password("password-one")
    b = encrypt_mt5_password("password-two")
    assert a != b
    assert decrypt_mt5_password(a) == "password-one"
    assert decrypt_mt5_password(b) == "password-two"
