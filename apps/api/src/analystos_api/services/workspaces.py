"""Workspace lifecycle and settings."""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from ..models import Membership, User, Workspace
from ..state import AppState

DEFAULT_SETTINGS: dict[str, Any] = {
    "calendar": {"fiscal_year_start_month": 1, "week_start": "monday"},
    "ai": {
        "enabled": False,
        "provider": "anthropic",
        "model_large": "claude-opus-5",
        "model_default": "claude-sonnet-5",
        "model_small": "claude-haiku-4-5-20251001",
        "allow_result_samples": False,
        "monthly_budget_usd": None,
        "tool_loop": False,
    },
    "investigation": {
        "max_depth": 2,
        "top_segments": 2,
        "require_plan_approval": True,
        "reference_date": None,
    },
}


def merged_settings(ws: Workspace) -> dict[str, Any]:
    out = copy.deepcopy(DEFAULT_SETTINGS)
    for key, value in (ws.settings or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


def create_workspace(db: Session, state: AppState, user: User, name: str, description: str = "") -> Workspace:
    ws = Workspace(
        name=name, description=description, settings=copy.deepcopy(DEFAULT_SETTINGS), created_by=user.id
    )
    db.add(ws)
    db.flush()
    db.add(Membership(workspace_id=ws.id, user_id=user.id, role="owner"))
    db.flush()
    state.stores.workspace_dir(ws.id)
    return ws
