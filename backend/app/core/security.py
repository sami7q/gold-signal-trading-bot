"""Dashboard authentication primitives (PROJECT_SPEC.md section 27).

The current phase ships a single shared-secret API key: if
``DASHBOARD_API_KEY`` is set, every settings-mutating and simulation
endpoint requires an ``X-API-Key`` header matching it. If unset, the API
runs open — acceptable only for local development, never for a deployed
dashboard (see docs/13-security.md and docs/14-live-readiness-checklist.md).

Password hashing helpers are provided for the future login page (Phase 13
VPS deployment) so callers never need to add a third-party hashing
dependency later.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings

_PBKDF2_ITERATIONS = 390_000


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
    return hmac.compare_digest(actual, expected)


def require_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> None:
    settings = settings or get_settings()
    configured_key = os.environ.get("DASHBOARD_API_KEY", "")
    if not configured_key:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key, configured_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )
