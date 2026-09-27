"""Audit log and diagnostic events.

* Audit entries are written in the caller's transaction: if the action rolls back, so does
  its audit entry.
* Diagnostic events and AI usage rows go through the background writer (their own
  transaction) so failures are recorded even when the surrounding work rolls back.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..logs import get_logger, redact, request_id_var
from ..models import AIUsage, AuditLog, DiagnosticEvent
from .writer import writer_for

log = get_logger("analystos.events")


def audit(
    db: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    actor: str = "",
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    db.add(
        AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=redact(detail or {}),
            ip=ip,
            request_id=request_id_var.get(),
        )
    )
    log.info(
        "audit",
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        workspace_id=workspace_id,
    )


def record_event(
    factory: sessionmaker[Session],
    *,
    category: str,
    name: str,
    status: str = "ok",
    duration_ms: float = 0.0,
    workspace_id: str | None = None,
    user_id: str | None = None,
    detail: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    safe_detail = redact(detail or {})
    safe_error = redact(error) if error else None
    log_fn = log.warning if status != "ok" else log.info
    log_fn(
        "diagnostic",
        category=category,
        name=name,
        status=status,
        duration_ms=round(duration_ms, 2),
        workspace_id=workspace_id,
        error=safe_error,
    )
    event = DiagnosticEvent(
        workspace_id=workspace_id,
        category=category,
        name=name,
        status=status,
        duration_ms=duration_ms,
        detail=safe_detail,
        error=safe_error,
        request_id=request_id_var.get(),
        user_id=user_id,
    )
    writer_for(factory).submit(lambda s: s.add(event))


@dataclass
class EventTimer:
    detail: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"
    error: str | None = None


@contextmanager
def timed_event(
    factory: sessionmaker[Session],
    *,
    category: str,
    name: str,
    workspace_id: str | None = None,
    user_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> Iterator[EventTimer]:
    """Record a diagnostic event around a block; exceptions are recorded then re-raised."""
    timer = EventTimer(detail=dict(detail or {}))
    start = time.perf_counter()
    try:
        yield timer
    except Exception as exc:
        timer.status = "error"
        timer.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        record_event(
            factory,
            category=category,
            name=name,
            status=timer.status,
            duration_ms=(time.perf_counter() - start) * 1000,
            workspace_id=workspace_id,
            user_id=user_id,
            detail=timer.detail,
            error=timer.error,
        )


def record_ai_usage(
    factory: sessionmaker[Session],
    *,
    workspace_id: str | None,
    provider: str,
    model: str,
    task: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: float,
    est_cost_usd: float,
    success: bool = True,
    investigation_id: str | None = None,
) -> None:
    usage = AIUsage(
        workspace_id=workspace_id,
        provider=provider,
        model=model,
        task=task,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        est_cost_usd=est_cost_usd,
        success=success,
        investigation_id=investigation_id,
    )
    writer_for(factory).submit(lambda s: s.add(usage))
    record_event(
        factory,
        category="ai",
        name=task,
        status="ok" if success else "error",
        duration_ms=latency_ms,
        workspace_id=workspace_id,
        detail={"provider": provider, "model": model, "tokens_in": tokens_in, "tokens_out": tokens_out},
    )
