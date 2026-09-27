"""Helpers to return job handles from endpoints."""

from __future__ import annotations

from ..models import Job
from ..schemas.common import JobAccepted, JobOut
from ..state import AppState


def job_accepted(state: AppState, workspace_id: str, job_id: str) -> JobAccepted:
    with state.session_factory() as db:
        job = db.get(Job, job_id)
        assert job is not None
        out = JobOut.model_validate(job)
    return JobAccepted(job=out, poll_url=f"/api/v1/workspaces/{workspace_id}/jobs/{job_id}")
