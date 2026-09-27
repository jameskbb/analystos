"""HTTP routers, all mounted under /api/v1."""

from __future__ import annotations

import importlib

from fastapi import APIRouter

ROUTER_MODULES = [
    "auth",
    "workspaces",
    "invites",
    "data_sources",
    "datasets",
    "relationships",
    "semantic",
    "queries",
    "quality",
    "explore",
    "analysis",
    "notebooks",
    "investigations",
    "artifacts",
    "findings",
    "dashboards",
    "reports",
    "exports",
    "search",
    "ai",
    "platform",
]


def build_api_router() -> APIRouter:
    api = APIRouter(prefix="/api/v1")
    for name in ROUTER_MODULES:
        module = importlib.import_module(f"{__name__}.{name}")
        api.include_router(module.router)
        extra = getattr(module, "kinds_router", None)
        if extra is not None:
            api.include_router(extra)
    return api
