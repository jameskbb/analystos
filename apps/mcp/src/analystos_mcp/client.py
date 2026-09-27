"""Thin, typed-enough HTTP client for the AnalystOS REST API (bearer API token).

The MCP server never touches the database or the analytical store directly: every tool goes
through the REST API with the caller's API token, so workspace membership, roles, read-only
tokens and workspace-restricted tokens apply exactly as they do for the web app.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(f"{status} {code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail


class AnalystOSClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        http: httpx.Client | None = None,
        timeout: float = 120.0,
        poll_interval: float = 0.5,
    ) -> None:
        self.base_url = (base_url or os.environ.get("AOS_API_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.token = token if token is not None else os.environ.get("AOS_API_TOKEN", "")
        self.poll_interval = poll_interval
        self._http = http or httpx.Client(base_url=self.base_url, timeout=timeout)
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def request(
        self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None
    ) -> Any:
        url = path if path.startswith("/api/") else f"/api/v1{path}"
        r = self._http.request(method, url, json=json, params=params, headers=self._headers())
        if r.status_code >= 400:
            try:
                body = r.json()
                raise ApiError(r.status_code, str(body.get("code", "error")), str(body.get("detail", r.text)))
            except ValueError:
                raise ApiError(r.status_code, "error", r.text[:500]) from None
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.text

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params={k: v for k, v in params.items() if v is not None} or None)

    def post(self, path: str, body: Any = None) -> Any:
        return self.request("POST", path, json=body if body is not None else {})

    def wait_job(self, poll_url: str, timeout: float | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + (timeout or self._timeout)
        while True:
            job = self.request("GET", poll_url)
            if job["status"] in ("succeeded", "failed"):
                return job
            if time.monotonic() > deadline:
                raise ApiError(408, "job_timeout", f"job {job['id']} is still {job['status']}")
            time.sleep(self.poll_interval)

    def close(self) -> None:
        self._http.close()
