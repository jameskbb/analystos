"""Deterministic, content-derived identifiers.

The same data and definitions must yield the same tree (spec §60), so ids are hashes of
canonical JSON rather than random UUIDs.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_id(prefix: str, *parts: Any, length: int = 16) -> str:
    digest = hashlib.sha256(canonical_json(list(parts)).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:length]}"
