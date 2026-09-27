"""Workspaces, members, settings and background jobs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..deps import DbDep, OwnerCtx, PrincipalDep, StateDep, ViewerCtx
from ..errors import Conflict, NotFound, Unprocessable
from ..models import Job, Membership, User, Workspace
from ..schemas.common import ERROR_RESPONSES, JobOut, OkResponse
from ..schemas.workspaces import (
    MemberAdd,
    MemberOut,
    MemberUpdate,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceSettings,
    WorkspaceSettingsUpdate,
    WorkspaceUpdate,
)
from ..services.observability import audit
from ..services.workspaces import create_workspace, merged_settings

router = APIRouter(prefix="/workspaces", tags=["workspaces"], responses=ERROR_RESPONSES)


def _out(ws: Workspace, role: str) -> WorkspaceOut:
    return WorkspaceOut(
        id=ws.id,
        name=ws.name,
        description=ws.description,
        role=role,  # type: ignore[arg-type]
        created_at=ws.created_at,
        updated_at=ws.updated_at,
    )


@router.get("", response_model=list[WorkspaceOut], operation_id="listWorkspaces")
def list_workspaces(principal: PrincipalDep, db: DbDep) -> list[WorkspaceOut]:
    stmt = (
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == principal.user.id)
        .order_by(Workspace.created_at)
    )
    if principal.token_workspace_id:
        stmt = stmt.where(Workspace.id == principal.token_workspace_id)
    return [_out(ws, role) for ws, role in db.execute(stmt).all()]


@router.post("", response_model=WorkspaceOut, status_code=201, operation_id="createWorkspace")
def create(body: WorkspaceCreate, principal: PrincipalDep, db: DbDep, state: StateDep) -> WorkspaceOut:
    if principal.token_workspace_id:
        raise Conflict("Workspace-scoped tokens cannot create workspaces", code="token_scope")
    ws = create_workspace(db, state, principal.user, body.name, body.description)
    audit(
        db,
        action="workspace.create",
        resource_type="workspace",
        resource_id=ws.id,
        workspace_id=ws.id,
        user_id=principal.user.id,
        actor=principal.user.email,
        detail={"name": body.name},
    )
    return _out(ws, "owner")


@router.get("/{workspace_id}", response_model=WorkspaceOut, operation_id="getWorkspace")
def get_workspace(ctx: ViewerCtx) -> WorkspaceOut:
    return _out(ctx.workspace, ctx.role)


@router.patch("/{workspace_id}", response_model=WorkspaceOut, operation_id="updateWorkspace")
def update_workspace(body: WorkspaceUpdate, ctx: OwnerCtx, db: DbDep) -> WorkspaceOut:
    if body.name is not None:
        ctx.workspace.name = body.name
    if body.description is not None:
        ctx.workspace.description = body.description
    audit(
        db,
        action="workspace.update",
        resource_type="workspace",
        resource_id=ctx.workspace_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail=body.model_dump(exclude_none=True),
    )
    db.flush()
    return _out(ctx.workspace, ctx.role)


@router.delete("/{workspace_id}", response_model=OkResponse, operation_id="deleteWorkspace")
def delete_workspace(ctx: OwnerCtx, db: DbDep, state: StateDep) -> OkResponse:
    ws_id = ctx.workspace_id
    audit(
        db,
        action="workspace.delete",
        resource_type="workspace",
        resource_id=ws_id,
        workspace_id=None,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"name": ctx.workspace.name},
    )
    db.delete(ctx.workspace)
    db.commit()
    state.stores.destroy(ws_id)
    return OkResponse()


# ---------------------------------------------------------------- settings


@router.get("/{workspace_id}/settings", response_model=WorkspaceSettings, operation_id="getWorkspaceSettings")
def get_settings(ctx: ViewerCtx) -> WorkspaceSettings:
    return WorkspaceSettings.model_validate(merged_settings(ctx.workspace))


@router.patch(
    "/{workspace_id}/settings", response_model=WorkspaceSettings, operation_id="updateWorkspaceSettings"
)
def update_settings(body: WorkspaceSettingsUpdate, ctx: OwnerCtx, db: DbDep) -> WorkspaceSettings:
    current = merged_settings(ctx.workspace)
    for key, value in body.model_dump(mode="json", exclude_none=True).items():
        current[key] = {**current.get(key, {}), **value} if isinstance(value, dict) else value
    ctx.workspace.settings = current
    audit(
        db,
        action="workspace.settings_update",
        resource_type="workspace",
        resource_id=ctx.workspace_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail=body.model_dump(mode="json", exclude_none=True),
    )
    return WorkspaceSettings.model_validate(current)


# ---------------------------------------------------------------- members


def _member_out(m: Membership, u: User) -> MemberOut:
    return MemberOut(user_id=u.id, email=u.email, name=u.name, role=m.role, created_at=m.created_at)  # type: ignore[arg-type]


@router.get("/{workspace_id}/members", response_model=list[MemberOut], operation_id="listMembers")
def list_members(ctx: ViewerCtx, db: DbDep) -> list[MemberOut]:
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.workspace_id == ctx.workspace_id)
        .order_by(Membership.created_at)
    ).all()
    return [_member_out(m, u) for m, u in rows]


@router.post("/{workspace_id}/members", response_model=MemberOut, status_code=201, operation_id="addMember")
def add_member(body: MemberAdd, ctx: OwnerCtx, db: DbDep) -> MemberOut:
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None:
        raise NotFound(
            "No user with that email; invite them instead (POST /workspaces/{ws}/invites)",
            code="user_not_found",
        )
    existing = db.scalar(
        select(Membership).where(Membership.workspace_id == ctx.workspace_id, Membership.user_id == user.id)
    )
    if existing is not None:
        raise Conflict("User is already a member", code="already_member")
    m = Membership(workspace_id=ctx.workspace_id, user_id=user.id, role=body.role)
    db.add(m)
    db.flush()
    audit(
        db,
        action="member.add",
        resource_type="membership",
        resource_id=user.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"email": user.email, "role": body.role},
    )
    return _member_out(m, user)


def _owner_count(db: DbDep, workspace_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.workspace_id == workspace_id, Membership.role == "owner")
        )
        or 0
    )


@router.patch("/{workspace_id}/members/{user_id}", response_model=MemberOut, operation_id="updateMember")
def update_member(user_id: str, body: MemberUpdate, ctx: OwnerCtx, db: DbDep) -> MemberOut:
    m = db.scalar(
        select(Membership).where(Membership.workspace_id == ctx.workspace_id, Membership.user_id == user_id)
    )
    if m is None:
        raise NotFound("Member not found")
    if m.role == "owner" and body.role != "owner" and _owner_count(db, ctx.workspace_id) <= 1:
        raise Unprocessable("A workspace must keep at least one owner", code="last_owner")
    m.role = body.role
    audit(
        db,
        action="member.update",
        resource_type="membership",
        resource_id=user_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"role": body.role},
    )
    user = db.get(User, user_id)
    assert user is not None
    return _member_out(m, user)


@router.delete("/{workspace_id}/members/{user_id}", response_model=OkResponse, operation_id="removeMember")
def remove_member(user_id: str, ctx: OwnerCtx, db: DbDep) -> OkResponse:
    m = db.scalar(
        select(Membership).where(Membership.workspace_id == ctx.workspace_id, Membership.user_id == user_id)
    )
    if m is None:
        raise NotFound("Member not found")
    if m.role == "owner" and _owner_count(db, ctx.workspace_id) <= 1:
        raise Unprocessable("A workspace must keep at least one owner", code="last_owner")
    db.delete(m)
    audit(
        db,
        action="member.remove",
        resource_type="membership",
        resource_id=user_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
    )
    return OkResponse()


# ---------------------------------------------------------------- jobs


@router.get("/{workspace_id}/jobs", response_model=list[JobOut], operation_id="listJobs", tags=["jobs"])
def list_jobs(
    ctx: ViewerCtx,
    db: DbDep,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[JobOut]:
    stmt = (
        select(Job).where(Job.workspace_id == ctx.workspace_id).order_by(Job.created_at.desc()).limit(limit)
    )
    if status:
        stmt = stmt.where(Job.status == status)
    return [JobOut.model_validate(j) for j in db.scalars(stmt).all()]


@router.get("/{workspace_id}/jobs/{job_id}", response_model=JobOut, operation_id="getJob", tags=["jobs"])
def get_job(job_id: str, ctx: ViewerCtx, db: DbDep) -> JobOut:
    job = db.get(Job, job_id)
    if job is None or job.workspace_id != ctx.workspace_id:
        raise NotFound("Job not found")
    return JobOut.model_validate(job)
