"""Encryption at rest for connector credentials (Fernet, key derived from AOS_SECRET_KEY)."""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class SecretDecryptionError(RuntimeError):
    """Raised when stored secrets cannot be decrypted (e.g. AOS_SECRET_KEY changed)."""


class SecretBox:
    def __init__(self, fernet_key: bytes) -> None:
        self._fernet = Fernet(fernet_key)

    def encrypt(self, secrets: dict[str, Any]) -> str:
        return self._fernet.encrypt(json.dumps(secrets, sort_keys=True).encode()).decode()

    def decrypt(self, token: str | None) -> dict[str, Any]:
        if not token:
            return {}
        try:
            return json.loads(self._fernet.decrypt(token.encode()))
        except InvalidToken as exc:
            raise SecretDecryptionError(
                "Stored credentials cannot be decrypted; was AOS_SECRET_KEY rotated?"
            ) from exc
