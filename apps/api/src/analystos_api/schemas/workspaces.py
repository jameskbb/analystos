from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from .common import ApiModel, Role


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)


class WorkspaceOut(ApiModel):
    id: str
    name: str
    description: str
    role: Role
    created_at: datetime
    updated_at: datetime


class CalendarSettings(BaseModel):
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    week_start: Literal["monday", "sunday"] = "monday"
    fiscal_year_naming: Literal["end_year", "start_year"] = "end_year"


class AISettings(BaseModel):
    enabled: bool = False
    provider: Literal["anthropic", "none"] = "anthropic"
    model_large: str = "claude-opus-5"
    model_default: str = "claude-sonnet-5"
    model_small: str = "claude-haiku-4-5-20251001"
    allow_result_samples: bool = Field(
        default=False,
        description="When false only schemas, metric definitions and aggregates are sent to the provider",
    )
    monthly_budget_usd: float | None = Field(default=None, ge=0, description="AI calls pause once reached")
    tool_loop: bool = Field(
        default=False, description="Allow bounded exploratory tool calls by the assistant"
    )


class InvestigationSettings(BaseModel):
    max_depth: int = Field(default=2, ge=0, le=4, description="How many levels deep investigations drill")
    top_segments: int = Field(default=2, ge=0, le=10, description="How many top segments are drilled into")
    require_plan_approval: bool = Field(default=True, description="Expensive plans wait for approval")
    reference_date: date | None = Field(
        default=None, description="'Today' for resolving relative periods; empty = the real date"
    )


class WorkspaceSettings(BaseModel):
    calendar: CalendarSettings = CalendarSettings()
    ai: AISettings = AISettings()
    investigation: InvestigationSettings = InvestigationSettings()


class WorkspaceSettingsUpdate(BaseModel):
    calendar: CalendarSettings | None = None
    ai: AISettings | None = None
    investigation: InvestigationSettings | None = None


class MemberOut(BaseModel):
    user_id: str
    email: str
    name: str
    role: Role
    created_at: datetime


class MemberAdd(BaseModel):
    email: str
    role: Role = "viewer"


class MemberUpdate(BaseModel):
    role: Role
