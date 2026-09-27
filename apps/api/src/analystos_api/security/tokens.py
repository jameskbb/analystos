"""Opaque tokens: session tokens, CSRF tokens and API tokens.

All tokens are 256-bit random values. Only their SHA-256 digest is stored, which is
safe for high-entropy secrets (no password-style stretching needed).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

API_TOKEN_PREFIX = "aos_"


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_api_token() -> tuple[str, str, str]:
    """Return (raw_token, token_hash, display_prefix)."""
    raw = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_token(raw), raw[: len(API_TOKEN_PREFIX) + 6]


def constant_time_equals(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode(), b.encode())
