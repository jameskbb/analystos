"""Shared fixtures: an isolated app per test (temp SQLite DB + temp DATA_DIR) and CSRF-aware clients."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from analystos_api.config import Settings
from analystos_api.main import create_app
from fastapi.testclient import TestClient


class ApiClient(TestClient):
    """TestClient that echoes the CSRF cookie on unsafe requests, like the web app does.

    By default it connects like a browser on the same machine (loopback peer, ``Host: localhost``),
    which is what local mode requires. Pass ``client=("203.0.113.9", 1234)`` and a non-local
    ``base_url`` to act as a remote caller.
    """

    def __init__(
        self,
        app: Any,
        base_url: str = "http://localhost",
        client: tuple[str, int] = ("127.0.0.1", 50000),
        **kwargs: Any,
    ) -> None:
        super().__init__(app, base_url=base_url, client=client, **kwargs)

    def request(self, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            csrf = self.cookies.get("aos_csrf")
            headers = dict(kwargs.pop("headers", None) or {})
            if csrf and "X-CSRF-Token" not in headers and "Authorization" not in headers:
                headers["X-CSRF-Token"] = csrf
            kwargs["headers"] = headers
        return super().request(method, url, *args, **kwargs)


def make_settings(tmp: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "DATABASE_URL": f"sqlite:///{tmp}/meta.db",
        "DATA_DIR": tmp / "data",
        "AOS_SECRET_KEY": "test-secret-key-" + "x" * 40,
        # Local (password-less) mode only exists in development (decision Q2b); tests exercise it.
        "AOS_ENV": "development",
        "AOS_LOG_JSON": False,
        "AOS_LOG_LEVEL": "WARNING",
        "AOS_JOB_EXECUTION": "inline",
        "AOS_CORS_ORIGINS": ["http://localhost:3000"],
        # Most tests sign up several users; the closed-by-default behaviour is tested in test_security.py.
        "AOS_ALLOW_SIGNUP": True,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def app(settings: Settings) -> Any:
    return create_app(settings)


@pytest.fixture
def client(app: Any) -> Iterator[ApiClient]:
    with ApiClient(app) as c:
        yield c


def remote_client(app: Any) -> ApiClient:
    """A caller on another machine (public peer address and hostname)."""
    return ApiClient(app, base_url="http://analystos.example.com", client=("203.0.113.9", 40000))


def signup(
    client: ApiClient, email: str, password: str = "correct horse battery", name: str = "User", **extra: Any
) -> dict[str, Any]:
    r = client.post("/api/v1/auth/signup", json={"email": email, "name": name, "password": password, **extra})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
def local_client(client: ApiClient) -> ApiClient:
    """Signed in as the local analyst (AUTH_MODE auto, no accounts yet)."""
    r = client.get("/api/v1/auth/session")
    assert r.status_code == 200 and r.json()["authenticated"], r.text
    return client


@pytest.fixture
def workspace(local_client: ApiClient) -> dict[str, Any]:
    r = local_client.post("/api/v1/workspaces", json={"name": "Test WS"})
    assert r.status_code == 201, r.text
    return r.json()


def load_tables(app: Any, workspace_id: str, tables: dict[str, Any], user_id: str | None = None) -> None:
    """Write pandas DataFrames into the workspace store and register them as datasets."""
    from analystos_api.services.datasets import profile_dataset, register_table

    state = app.state.aos
    with state.stores.writing(workspace_id) as store:
        for name, df in tables.items():
            store.write_table(name, df, if_exists="replace")
    ids = []
    with state.session_factory() as db:
        for name in tables:
            ds, _ = register_table(
                db, state, workspace_id, name, name=name, source_kind="upload", source_ref={}, user_id=user_id
            )
            ids.append(ds.id)
        db.commit()
    for ds_id in ids:
        profile_dataset(state, workspace_id, ds_id, user_id)
