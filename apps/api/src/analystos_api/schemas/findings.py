from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import ApiModel

StatementType = Literal["observation", "supported_explanation", "hypothesis"]
EvidenceStrength = Literal["strong", "moderate", "weak", "hypothesis_only"]
FindingStatus = Literal["draft", "confirmed", "rejected", "needs_review"]


class FindingOut(ApiModel):
    id: str
    statement: str
    statement_type: StatementType
    evidence_strength: EvidenceStrength
    evidence_reasons: list[str]
    status: FindingStatus
    notes: str
    business_impact: str
    investigation_id: str | None
    node_id: str | None
    metric_id: str | None
    metric_version_ids: dict[str, Any]
    values: dict[str, Any] = Field(
        description="current, baseline, abs_change, pct_change, share, effect, format"
    )
    filter_context: list[dict[str, Any]]
    segment: dict[str, Any] | None
    artifact_ids: list[str]
    tags: list[str]
    version_no: int
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    comment_count: int = 0


class FindingCreate(BaseModel):
    statement: str = Field(min_length=3, max_length=5000)
    statement_type: StatementType = "hypothesis"
    evidence_strength: EvidenceStrength | None = Field(
        default=None, description="Only allowed with supporting artifacts; otherwise hypothesis_only"
    )
    evidence_reasons: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    investigation_id: str | None = None
    notes: str = ""
    business_impact: str = ""
    tags: list[str] = Field(default_factory=list)


class FindingUpdate(BaseModel):
    statement: str | None = Field(default=None, min_length=3, max_length=5000)
    notes: str | None = None
    business_impact: str | None = None
    tags: list[str] | None = None
    change_note: str = ""


class StatusChange(BaseModel):
    status: FindingStatus
    note: str | None = None


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)


class CommentOut(ApiModel):
    id: str
    finding_id: str
    user_id: str | None
    user_name: str | None = None
    body: str
    created_at: datetime


class FindingVersionOut(ApiModel):
    id: str
    version_no: int
    snapshot: dict[str, Any]
    change_note: str
    created_by: str | None
    created_at: datetime
