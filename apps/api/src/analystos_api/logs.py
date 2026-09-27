"""Structured logging (structlog) with secret redaction and request-id propagation."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from contextvars import ContextVar
from typing import Any

import structlog

request_id_var: ContextVar[str | None] = ContextVar("aos_request_id", default=None)

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|authorization|cookie|private[_-]?key|credential|"
    r"client[_-]?secret|dsn|connection[_-]?string|session)",
    re.IGNORECASE,
)
# Values that look like secrets regardless of the key they are stored under.
_SENSITIVE_VALUE = re.compile(
    r"(aos_[A-Za-z0-9_\-]{16,}|sk-ant-[A-Za-z0-9_\-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?<=://)[^:/@\s]+:[^@\s]+(?=@))"
)
# Keys whose names match the pattern above but carry no secret material.
_ALLOWED_KEYS = {"session_id_prefix", "token_prefix", "tokens_in", "tokens_out", "csrf_required"}


def redact(value: Any, key: str | None = None, _depth: int = 0) -> Any:
    """Recursively redact secrets from log payloads."""
    if _depth > 8:
        return value
    if key is not None and key not in _ALLOWED_KEYS and _SENSITIVE_KEY.search(key):
        if value is None or isinstance(value, bool | int | float):
            return value
        return REDACTED
    if isinstance(value, Mapping):
        return {k: redact(v, str(k), _depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v, None, _depth + 1) for v in value]
    if isinstance(value, str):
        return _SENSITIVE_VALUE.sub(REDACTED, value)
    return value


def redaction_processor(_logger: Any, _method: str, event_dict: MutableMapping[str, Any]) -> dict[str, Any]:
    return {
        k: (v if k in {"event", "level", "timestamp", "logger"} else redact(v, k))
        for k, v in event_dict.items()
    }


def _add_request_id(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    rid = request_id_var.get()
    if rid and "request_id" not in event_dict:
        event_dict["request_id"] = rid
    return event_dict


_configured = False


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=getattr(logging, level.upper(), 20))
    renderer: Any = structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_request_id,
            redaction_processor,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), 20)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "analystos") -> Any:
    return structlog.get_logger(name)
