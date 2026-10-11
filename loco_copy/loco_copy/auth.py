"""Dashboard logins. Same password format as the gold bot (`pbkdf2_sha256$iterations$salt$digest`), so its
existing users can be imported with their passwords unchanged (`loco-copy import-users`). Session tokens are
random; only their SHA-256 is stored."""

from __future__ import annotations

import hashlib
import hmac
import secrets

ITERATIONS = 390_000


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
        salt, expected, rounds = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex), int(iterations)
    except (ValueError, AttributeError):
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds), expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
