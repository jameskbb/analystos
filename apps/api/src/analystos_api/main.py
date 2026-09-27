"""FastAPI application factory and the ``analystos-api`` console entry point."""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from . import __version__
from .config import Settings, get_settings
from .db import make_engine, make_session_factory
from .errors import install_error_handlers
from .jobs import JobRunner
from .logs import configure_logging, get_logger, request_id_var
from .migrate import upgrade_to_head
from .routers import build_api_router
from .security.secrets_box import SecretBox
from .services.auth import client_address
from .services.stores import StoreRegistry
from .services.writer import close_writer
from .state import AppState

log = get_logger("analystos.http")
_REQUEST_ID_OK = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")


def _operation_id(route: APIRoute) -> str:
    return route.name


def create_app(settings: Settings | None = None, *, run_migrations: bool | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.check_secure_defaults()
    configure_logging(settings.log_level, settings.log_json)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    factory = make_session_factory(engine)
    state = AppState(
        settings=settings, engine=engine, session_factory=factory, secret_box=SecretBox(settings.fernet_key())
    )
    state.jobs = JobRunner(factory, workers=settings.job_workers, mode=settings.job_execution)
    state.stores = StoreRegistry(settings)

    if settings.auto_migrate if run_migrations is None else run_migrations:
        upgrade_to_head(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        recovered = state.jobs.recover_interrupted()
        if recovered:
            log.warning("jobs_recovered_as_failed", count=recovered)
        log.info("startup", version=__version__, auth_mode=settings.auth_mode, ai_enabled=settings.ai_enabled)
        yield
        state.jobs.shutdown()
        close_writer(factory)
        state.stores.close_all()
        engine.dispose()

    app = FastAPI(
        title="AnalystOS API",
        version=__version__,
        description=(
            "REST API for AnalystOS, an AI-native analytical workbench. All resources are workspace-scoped. "
            "Authenticate with the `aos_session` cookie (send `X-CSRF-Token` on unsafe methods) or with "
            "`Authorization: Bearer <api token>`. Errors use the envelope "
            "`{detail, code, request_id, errors}`."
        ),
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
        generate_unique_id_function=_operation_id,
    )
    app.state.aos = state
    install_error_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Authorization", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Content-Disposition"],
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _REQUEST_ID_OK.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        elapsed = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(elapsed, 2),
            request_id=rid,
            client=client_address(request, settings),
        )
        return response

    app.include_router(build_api_router())

    @app.get("/api/health", tags=["meta"], operation_id="health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


def run() -> None:
    """Console script: ``uv run analystos-api``."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "analystos_api.main:app_factory",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
    )


def app_factory() -> FastAPI:
    return create_app()
