from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import ApiModel

Cardinality = Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]


class RelationshipOut(ApiModel):
    id: str
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    cardinality: str
    confidence: str
    signals: list[Any]
    overlap_pct: float | None
    status: Literal["suggested", "approved", "rejected"]
    origin: str
    join_analysis: dict[str, Any] | None = Field(
        default=None,
        description="Engine JoinAnalysis: observed cardinality, fan-out factor, orphan %, warnings, SQL",
    )
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime


class RelationshipCreate(BaseModel):
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    cardinality: Cardinality = "many_to_one"
    approve: bool = True


class RelationshipDecision(BaseModel):
    cardinality: Cardinality | None = Field(default=None, description="Override the suggested cardinality")
    note: str | None = None
