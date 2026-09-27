"""Typed SQLAlchemy 2 ORM models for the AnalystOS metadata database.

JSON columns are replaced wholesale on update (never mutated in place) so change
tracking works on every backend.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, new_id, utcnow


def _id() -> Mapped[str]:
    return mapped_column(String(32), primary_key=True, default=new_id)


def _ws_fk() -> Mapped[str]:
    return mapped_column(String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)


def _user_fk(nullable: bool = True) -> Mapped[str | None]:
    return mapped_column(String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=nullable)


def _created() -> Mapped[datetime]:
    return mapped_column(default=utcnow)


def _updated() -> Mapped[datetime]:
    return mapped_column(default=utcnow, onupdate=utcnow)


def _json_dict() -> Mapped[dict[str, Any]]:
    return mapped_column(JSON, default=dict)


def _json_list() -> Mapped[list[Any]]:
    return mapped_column(JSON, default=list)


# --------------------------------------------------------------------------- identity


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = _id()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_local: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    # sha256 of the opaque session token; the raw token only ever lives in the cookie.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column()
    last_seen_at: Mapped[datetime] = _created()
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ApiToken(Base):
    __tablename__ = "api_tokens"
    id: Mapped[str] = _id()
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    read_only: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    settings: Mapped[dict[str, Any]] = _json_dict()
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan", passive_deletes=True
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # owner | editor | viewer
    created_at: Mapped[datetime] = _created()
    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship()


class Invite(Base):
    """A single-use, expiring invitation to join a workspace with a role. Only the token's SHA-256 is stored."""

    __tablename__ = "invites"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    email: Mapped[str] = mapped_column(String(320), index=True)
    role: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column()
    accepted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    accepted_by: Mapped[str | None] = _user_fk()
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)


# --------------------------------------------------------------------------- data


class DataSource(Base):
    __tablename__ = "data_sources"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(
        String(32)
    )  # files | postgres | mysql | sqlserver | snowflake | bigquery
    description: Mapped[str] = mapped_column(Text, default="")
    config: Mapped[dict[str, Any]] = _json_dict()
    secrets_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_fields: Mapped[list[Any]] = _json_list()
    status: Mapped[str] = mapped_column(String(32), default="untested")
    last_tested_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_test_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    filename: Mapped[str] = mapped_column(String(400))
    stored_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    file_kind: Mapped[str] = mapped_column(String(16))  # csv | excel | parquet | json
    inspection: Mapped[dict[str, Any]] = _json_dict()
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("workspace_id", "table_name"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    data_source_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    table_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    source_kind: Mapped[str] = mapped_column(String(32))  # upload | connector | demo
    source_ref: Mapped[dict[str, Any]] = _json_dict()
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    columns: Mapped[list[Any]] = _json_list()
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    profile_status: Mapped[str] = mapped_column(String(32), default="pending")
    profiled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    current_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tags: Mapped[list[Any]] = _json_list()
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version_no"),)
    id: Mapped[str] = _id()
    dataset_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(128))
    row_count: Mapped[int] = mapped_column(Integer)
    columns: Mapped[list[Any]] = _json_list()
    ingest_options: Mapped[dict[str, Any]] = _json_dict()
    upload_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("uploads.id", ondelete="SET NULL"), nullable=True
    )
    captured_at: Mapped[datetime] = _created()
    created_by: Mapped[str | None] = _user_fk()


class RelationshipRecord(Base):
    __tablename__ = "relationships"
    __table_args__ = (UniqueConstraint("workspace_id", "from_table", "from_col", "to_table", "to_col"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    from_table: Mapped[str] = mapped_column(String(200))
    from_col: Mapped[str] = mapped_column(String(200))
    to_table: Mapped[str] = mapped_column(String(200))
    to_col: Mapped[str] = mapped_column(String(200))
    cardinality: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    signals: Mapped[list[Any]] = _json_list()
    overlap_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="suggested")  # suggested | approved | rejected
    origin: Mapped[str] = mapped_column(String(16), default="discovered")  # discovered | manual
    join_analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    decided_by: Mapped[str | None] = _user_fk()
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = _created()


class QualityRule(Base):
    __tablename__ = "quality_rules"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    dataset_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    table_name: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(32))
    column: Mapped[str | None] = mapped_column(String(200), nullable=True)
    params: Mapped[dict[str, Any]] = _json_dict()
    severity: Mapped[str] = mapped_column(String(16), default="warning")
    origin: Mapped[str] = mapped_column(String(16), default="manual")  # manual | suggested | demo
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | suggested | disabled
    description: Mapped[str] = mapped_column(Text, default="")
    last_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class QualityRun(Base):
    __tablename__ = "quality_runs"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    rule_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("quality_rules.id", ondelete="CASCADE"), index=True
    )
    passed: Mapped[bool] = mapped_column(Boolean)
    failing_count: Mapped[int] = mapped_column(Integer, default=0)
    total_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_failing_rows: Mapped[dict[str, Any]] = _json_dict()
    sql: Mapped[str] = mapped_column(Text, default="")
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dataset_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    run_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()


# --------------------------------------------------------------------------- semantic layer


class Entity(Base):
    """A semantic entity; ``spec`` holds the engine ``Entity`` definition."""

    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    name: Mapped[str] = mapped_column(String(200))
    spec: Mapped[dict[str, Any]] = _json_dict()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class DimensionRecord(Base):
    """A semantic dimension; ``spec`` holds the engine ``Dimension`` definition."""

    __tablename__ = "dimensions"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    name: Mapped[str] = mapped_column(String(200))
    spec: Mapped[dict[str, Any]] = _json_dict()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class MetricRecord(Base):
    """Stable identity of a metric. Definitions live in immutable MetricVersion rows."""

    __tablename__ = "metrics"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    name: Mapped[str] = mapped_column(String(200))  # machine name, e.g. "revenue"
    current_version_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_version_no: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class MetricVersion(Base):
    __tablename__ = "metric_versions"
    __table_args__ = (UniqueConstraint("metric_id", "version_no"),)
    id: Mapped[str] = _id()
    metric_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("metrics.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str] = _ws_fk()
    version_no: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, Any]] = _json_dict()
    definition_hash: Mapped[str] = mapped_column(String(64))
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()


class MetricTreeRecord(Base):
    """A metric tree; ``spec`` holds the engine ``MetricTree`` (edges carry approved/suggested flags)."""

    __tablename__ = "metric_trees"
    __table_args__ = (UniqueConstraint("workspace_id", "root_metric"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    root_metric: Mapped[str] = mapped_column(String(200))
    spec: Mapped[dict[str, Any]] = _json_dict()
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class GlossaryTermRecord(Base):
    """A glossary term; ``spec`` holds the engine ``GlossaryTerm``."""

    __tablename__ = "glossary_terms"
    __table_args__ = (UniqueConstraint("workspace_id", "term"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    term: Mapped[str] = mapped_column(String(200))
    spec: Mapped[dict[str, Any]] = _json_dict()
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class SemanticSnapshot(Base):
    """Immutable, content-addressed snapshot of the assembled semantic model."""

    __tablename__ = "semantic_snapshots"
    __table_args__ = (UniqueConstraint("workspace_id", "content_hash"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    version_no: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[dict[str, Any]] = _json_dict()
    metric_version_ids: Mapped[dict[str, Any]] = _json_dict()
    reason: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()


# --------------------------------------------------------------------------- workbench


class SavedQuery(Base):
    __tablename__ = "saved_queries"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    sql: Mapped[str] = mapped_column(Text)
    parameters: Mapped[list[Any]] = _json_list()
    data_source_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tags: Mapped[list[Any]] = _json_list()
    chart: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class QueryRun(Base):
    __tablename__ = "query_runs"
    __table_args__ = (Index("ix_query_runs_ws_created", "workspace_id", "created_at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    user_id: Mapped[str | None] = _user_fk()
    origin: Mapped[str] = mapped_column(
        String(32), default="sql"
    )  # sql | explore | pivot | notebook | dashboard | mcp
    sql: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = _json_dict()
    saved_query_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    data_source_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    investigation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16))  # succeeded | failed | rejected
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    elapsed_ms: Mapped[float] = mapped_column(Float, default=0.0)
    columns: Mapped[list[Any]] = _json_list()
    result_snapshot: Mapped[list[Any]] = _json_list()
    dataset_versions: Mapped[dict[str, Any]] = _json_dict()
    metric_versions: Mapped[dict[str, Any]] = _json_dict()
    created_at: Mapped[datetime] = _created()


class Notebook(Base):
    __tablename__ = "notebooks"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    investigation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class NotebookCell(Base):
    __tablename__ = "notebook_cells"
    id: Mapped[str] = _id()
    notebook_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("notebooks.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # sql | python | markdown | chart | finding
    source: Mapped[str] = mapped_column(Text, default="")
    config: Mapped[dict[str, Any]] = _json_dict()
    output: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="idle")  # idle | ok | error
    execution_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# --------------------------------------------------------------------------- investigations


class Investigation(Base):
    """An investigation. ``document`` is the investigator's ``Investigation`` model (interpretation,
    plan, hypotheses, tree, brief answer) for the current run; runs keep their own copies."""

    __tablename__ = "investigations"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    question: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(400))
    template: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ready")
    document: Mapped[dict[str, Any]] = _json_dict()
    options: Mapped[dict[str, Any]] = _json_dict()
    metric_version_ids: Mapped[dict[str, Any]] = _json_dict()
    dataset_versions: Mapped[dict[str, Any]] = _json_dict()
    semantic_snapshot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    engine_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class InvestigationRun(Base):
    __tablename__ = "investigation_runs"
    __table_args__ = (UniqueConstraint("investigation_id", "run_no"),)
    id: Mapped[str] = _id()
    investigation_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str] = _ws_fk()
    run_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), default="run")  # run | rerun
    status: Mapped[str] = mapped_column(String(16), default="running")
    document: Mapped[dict[str, Any]] = _json_dict()
    metric_version_ids: Mapped[dict[str, Any]] = _json_dict()
    dataset_versions: Mapped[dict[str, Any]] = _json_dict()
    semantic_snapshot_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    triggered_by: Mapped[str | None] = _user_fk()
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ArtifactRecord(Base):
    """A persisted analytical artifact. ``document`` is the investigator ``Artifact``; the other
    columns are denormalised for listing, search and lineage. ``engine_artifact_id`` is the
    content-derived id referenced by tree nodes (unique within one run)."""

    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_run_engine", "run_id", "engine_artifact_id"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    investigation_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    engine_artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(400), default="")
    sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    python: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict[str, Any]] = _json_dict()
    filters: Mapped[list[Any]] = _json_list()
    filter_context: Mapped[list[Any]] = _json_list()
    metric_versions: Mapped[dict[str, Any]] = _json_dict()
    dataset_versions: Mapped[list[Any]] = _json_list()
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    chart_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    parent_ids: Mapped[list[Any]] = _json_list()
    document: Mapped[dict[str, Any]] = _json_dict()
    origin: Mapped[str] = mapped_column(String(32), default="investigation")
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    investigation_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("investigations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    node_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    statement: Mapped[str] = mapped_column(Text)
    statement_type: Mapped[str] = mapped_column(String(32), default="observation")
    evidence_strength: Mapped[str] = mapped_column(String(32), default="hypothesis_only")
    evidence_reasons: Mapped[list[Any]] = _json_list()
    status: Mapped[str] = mapped_column(
        String(16), default="draft"
    )  # draft | confirmed | rejected | needs_review
    notes: Mapped[str] = mapped_column(Text, default="")
    business_impact: Mapped[str] = mapped_column(Text, default="")
    metric_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    metric_version_ids: Mapped[dict[str, Any]] = _json_dict()
    values: Mapped[dict[str, Any]] = _json_dict()  # current/baseline/abs_change/pct_change/share
    filter_context: Mapped[list[Any]] = _json_list()
    segment: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    artifact_ids: Mapped[list[Any]] = _json_list()
    tags: Mapped[list[Any]] = _json_list()
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class FindingVersion(Base):
    __tablename__ = "finding_versions"
    __table_args__ = (UniqueConstraint("finding_id", "version_no"),)
    id: Mapped[str] = _id()
    finding_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = _json_dict()
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()


class FindingComment(Base):
    __tablename__ = "finding_comments"
    id: Mapped[str] = _id()
    finding_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("findings.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str | None] = _user_fk()
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# --------------------------------------------------------------------------- outputs


class Dashboard(Base):
    __tablename__ = "dashboards"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    layout: Mapped[list[Any]] = _json_list()  # [{i: tile_id, x, y, w, h}]
    filters: Mapped[list[Any]] = _json_list()  # dashboard-level filter controls
    date_range: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class DashboardTile(Base):
    __tablename__ = "dashboard_tiles"
    id: Mapped[str] = _id()
    dashboard_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("dashboards.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))  # kpi | chart | table | text
    title: Mapped[str] = mapped_column(String(300), default="")
    binding: Mapped[dict[str, Any]] = _json_dict()
    viz: Mapped[dict[str, Any]] = _json_dict()
    text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class DashboardVersion(Base):
    __tablename__ = "dashboard_versions"
    __table_args__ = (UniqueConstraint("dashboard_id", "version_no"),)
    id: Mapped[str] = _id()
    dashboard_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("dashboards.id", ondelete="CASCADE"), index=True
    )
    version_no: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = _json_dict()
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    title: Mapped[str] = mapped_column(String(400))
    kind: Mapped[str] = mapped_column(
        String(32), default="custom"
    )  # custom | business_review | investigation
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | in_review | published
    period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    investigation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    blocks: Mapped[list[Any]] = _json_list()
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class StoredFile(Base):
    """Exports and client-rendered chart images stored under DATA_DIR."""

    __tablename__ = "stored_files"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str] = _ws_fk()
    purpose: Mapped[str] = mapped_column(String(32))  # export | chart_image
    format: Mapped[str] = mapped_column(String(16))
    filename: Mapped[str] = mapped_column(String(400))
    media_type: Mapped[str] = mapped_column(String(128))
    stored_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    meta: Mapped[dict[str, Any]] = _json_dict()
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()


# --------------------------------------------------------------------------- platform


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | succeeded | failed
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[dict[str, Any]] = _json_dict()
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str | None] = _user_fk()
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_ws_created", "workspace_id", "created_at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor: Mapped[str] = mapped_column(String(320), default="")
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict[str, Any]] = _json_dict()
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _created()


class DiagnosticEvent(Base):
    __tablename__ = "diagnostic_events"
    __table_args__ = (Index("ix_diag_ws_created", "workspace_id", "created_at"),)
    id: Mapped[str] = _id()
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    category: Mapped[str] = mapped_column(
        String(32)
    )  # import | profiling | sql | python | ai | investigation | job | quality
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16))  # ok | error | rejected
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    detail: Mapped[dict[str, Any]] = _json_dict()
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = _created()


class AIUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[str] = _id()
    workspace_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(100))
    task: Mapped[str] = mapped_column(String(100))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    investigation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = _created()
