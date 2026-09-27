"""In-process background worker backed by a persisted ``jobs`` table.

Long-running work (profiling, investigations, demo bootstrap) is submitted as a job and
executed on a bounded thread pool. Clients poll ``GET /api/v1/workspaces/{ws}/jobs/{id}``.
There is no external broker: if the process restarts, jobs that were running are marked
failed at startup ("interrupted") so they never look stuck. Setting
``AOS_JOB_EXECUTION=inline`` runs jobs synchronously (used by tests and CLI tooling).
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from .db import utcnow
from .logs import get_logger, request_id_var
from .models import Job
from .services.observability import record_event

log = get_logger("analystos.jobs")


@dataclass
class JobContext:
    job_id: str
    workspace_id: str | None
    user_id: str | None
    session_factory: sessionmaker[Session]

    def progress(self, fraction: float, message: str = "") -> None:
        with self.session_factory() as s:
            s.execute(
                update(Job)
                .where(Job.id == self.job_id)
                .values(progress=max(0.0, min(1.0, fraction)), message=message[:2000])
            )
            s.commit()


JobFn = Callable[[JobContext], dict[str, Any] | None]


class JobRunner:
    def __init__(self, factory: sessionmaker[Session], workers: int = 4, mode: str = "thread") -> None:
        self._factory = factory
        self._mode = mode
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="aos-job")
        self._futures: dict[str, Future[None]] = {}

    def recover_interrupted(self) -> int:
        with self._factory() as s:
            res = s.execute(
                update(Job)
                .where(Job.status.in_(["queued", "running"]))
                .values(
                    status="failed",
                    error="Interrupted: the server restarted before the job finished.",
                    finished_at=utcnow(),
                )
            )
            s.commit()
            return int(getattr(res, "rowcount", 0) or 0)

    def submit(
        self,
        *,
        kind: str,
        fn: JobFn,
        workspace_id: str | None,
        user_id: str | None,
        params: dict[str, Any] | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        db: Session | None = None,
    ) -> str:
        """Persist a job row and schedule it. Commits ``db`` first so the job sees the caller's writes."""
        if db is not None:
            db.commit()
        with self._factory() as s:
            job = Job(
                workspace_id=workspace_id,
                kind=kind,
                status="queued",
                params=params or {},
                resource_type=resource_type,
                resource_id=resource_id,
                created_by=user_id,
            )
            s.add(job)
            s.commit()
            job_id = job.id
        ctx = JobContext(
            job_id=job_id, workspace_id=workspace_id, user_id=user_id, session_factory=self._factory
        )
        rid = request_id_var.get()
        if self._mode == "inline":
            self._execute(ctx, kind, fn, rid)
        else:
            self._futures[job_id] = self._pool.submit(self._execute, ctx, kind, fn, rid)
        return job_id

    def wait(self, job_id: str, timeout: float | None = None) -> None:
        fut = self._futures.get(job_id)
        if fut is not None:
            fut.result(timeout=timeout)

    def _execute(self, ctx: JobContext, kind: str, fn: JobFn, request_id: str | None) -> None:
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        try:
            with self._factory() as s:
                s.execute(
                    update(Job).where(Job.id == ctx.job_id).values(status="running", started_at=utcnow())
                )
                s.commit()
            try:
                result = fn(ctx) or {}
            except Exception as exc:
                log.error(
                    "job_failed", job_id=ctx.job_id, kind=kind, error=str(exc), tb=traceback.format_exc()
                )
                with self._factory() as s:
                    s.execute(
                        update(Job)
                        .where(Job.id == ctx.job_id)
                        .values(
                            status="failed", error=f"{type(exc).__name__}: {exc}"[:4000], finished_at=utcnow()
                        )
                    )
                    s.commit()
                record_event(
                    self._factory,
                    category="job",
                    name=kind,
                    status="error",
                    duration_ms=(time.perf_counter() - start) * 1000,
                    workspace_id=ctx.workspace_id,
                    user_id=ctx.user_id,
                    detail={"job_id": ctx.job_id},
                    error=f"{type(exc).__name__}: {exc}",
                )
                return
            with self._factory() as s:
                s.execute(
                    update(Job)
                    .where(Job.id == ctx.job_id)
                    .values(status="succeeded", result=result, progress=1.0, finished_at=utcnow())
                )
                s.commit()
            record_event(
                self._factory,
                category="job",
                name=kind,
                duration_ms=(time.perf_counter() - start) * 1000,
                workspace_id=ctx.workspace_id,
                user_id=ctx.user_id,
                detail={"job_id": ctx.job_id},
            )
        finally:
            self._futures.pop(ctx.job_id, None)
            request_id_var.reset(token)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
