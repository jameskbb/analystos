"""Workspace invites: how people join once sign-up is closed.

An owner invites an email address with a role and receives a single-use token (shown once; only its SHA-256
is stored) that expires after ``AOS_INVITE_TTL_DAYS`` (default 7, or ``expires_in_days``). The invitee opens
the link and accepts it by choosing a name and password, which creates the account, the membership and a
session in one step. If an account with that email already exists, the invitee signs in first and accepts
with the session instead. Owners can list and revoke invites; every step is audited.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..deps import DbDep, OwnerCtx, StateDep, client_ip
from ..errors import ApiError, Conflict, Forbidden, NotFound, TooManyRequests
from ..models import Invite, Membership, User, Workspace
from ..schemas.auth import SessionInfo, UserOut, _normalise_email
from ..schemas.common import ERROR_RESPONSES, OkResponse
from ..security.passwords import MIN_PASSWORD_LENGTH, hash_password
from ..security.tokens import constant_time_equals, hash_token, new_token
from ..services.auth import CSRF_HEADER, SESSION_COOKIE, create_session, lookup_session, set_session_cookies
from ..services.observability import audit
from ..state import AppState

router = APIRouter(prefix="/workspaces/{workspace_id}/invites", tags=["invites"], responses=ERROR_RESPONSES)
# Mounted by routers/__init__.py next to ``router`` (the invitee is not signed in or not yet a member).
kinds_router = APIRouter(prefix="/auth/invites", tags=["auth", "invites"], responses=ERROR_RESPONSES)

Role = Literal["owner", "editor", "viewer"]
InviteStatus = Literal["pending", "accepted", "revoked", "expired"]


class InviteCreate(BaseModel):
    email: str
    role: Role = "viewer"
    expires_in_days: int | None = Field(
        default=None, ge=1, le=90, description="Default AOS_INVITE_TTL_DAYS (7)"
    )

    _email = field_validator("email")(_normalise_email)


class InviteOut(BaseModel):
    id: str
    email: str
    role: Role
    status: InviteStatus
    created_by: str | None
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None


class InviteCreated(BaseModel):
    token: str = Field(description="Single-use invite token. Shown exactly once; send it to the invitee.")
    accept_path: str = Field(description="Web path to hand out: /invite/{token}")
    invite: InviteOut


class InvitePreview(BaseModel):
    workspace_name: str
    email: str
    role: Role
    expires_at: datetime
    account_exists: bool = Field(description="Sign in as this email and accept with the session instead")


class InviteAccept(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH, max_length=1024)


def _status(inv: Invite) -> InviteStatus:
    if inv.revoked_at is not None:
        return "revoked"
    if inv.accepted_at is not None:
        return "accepted"
    if inv.expires_at <= utcnow():
        return "expired"
    return "pending"


def _out(inv: Invite) -> InviteOut:
    return InviteOut.model_validate(
        {
            "id": inv.id,
            "email": inv.email,
            "role": inv.role,
            "status": _status(inv),
            "created_by": inv.created_by,
            "created_at": inv.created_at,
            "expires_at": inv.expires_at,
            "accepted_at": inv.accepted_at,
            "revoked_at": inv.revoked_at,
        }
    )


@router.post("", response_model=InviteCreated, status_code=201, operation_id="createInvite")
def create_invite(body: InviteCreate, ctx: OwnerCtx, db: DbDep, state: StateDep) -> InviteCreated:
    """Invite an email address to this workspace with a role (owner only). Returns the token once."""
    user = db.scalar(select(User).where(User.email == body.email))
    if user is not None and db.scalar(
        select(Membership.id).where(
            Membership.workspace_id == ctx.workspace_id, Membership.user_id == user.id
        )
    ):
        raise Conflict("This person is already a member", code="already_member")
    now = utcnow()
    for old in db.scalars(
        select(Invite).where(
            Invite.workspace_id == ctx.workspace_id,
            Invite.email == body.email,
            Invite.accepted_at.is_(None),
            Invite.revoked_at.is_(None),
        )
    ):
        old.revoked_at = now  # one live invite per email and workspace: the newest wins
    raw = new_token()
    days = body.expires_in_days or state.settings.invite_ttl_days
    inv = Invite(
        workspace_id=ctx.workspace_id,
        email=body.email,
        role=body.role,
        token_hash=hash_token(raw),
        created_by=ctx.user_id,
        expires_at=now + timedelta(days=days),
    )
    db.add(inv)
    db.flush()
    audit(
        db,
        action="invite.create",
        resource_type="invite",
        resource_id=inv.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"email": body.email, "role": body.role, "expires_in_days": days},
    )
    return InviteCreated(token=raw, accept_path=f"/invite/{raw}", invite=_out(inv))


@router.get("", response_model=list[InviteOut], operation_id="listInvites")
def list_invites(ctx: OwnerCtx, db: DbDep) -> list[InviteOut]:
    rows = db.scalars(
        select(Invite).where(Invite.workspace_id == ctx.workspace_id).order_by(Invite.created_at.desc())
    ).all()
    return [_out(r) for r in rows]


@router.delete("/{invite_id}", response_model=OkResponse, operation_id="revokeInvite")
def revoke_invite(invite_id: str, ctx: OwnerCtx, db: DbDep) -> OkResponse:
    inv = db.get(Invite, invite_id)
    if inv is None or inv.workspace_id != ctx.workspace_id:
        raise NotFound("Invite not found")
    if inv.accepted_at is not None:
        raise Conflict(
            "The invite was already accepted; change the member's role instead", code="invite_accepted"
        )
    if inv.revoked_at is None:
        inv.revoked_at = utcnow()
    audit(
        db,
        action="invite.revoke",
        resource_type="invite",
        resource_id=inv.id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user_id,
        actor=ctx.user.email,
        ip=ctx.ip,
        detail={"email": inv.email},
    )
    return OkResponse()


class InviteInvalid(ApiError):
    status_code = 404
    code = "invite_invalid"


def _live_invite(db: Session, state: AppState, request: Request, token: str) -> Invite:
    """The pending invite for ``token``. Unknown, used, revoked and expired tokens all give the same 404, and
    repeated bad tokens from one address are throttled like failed logins."""
    ip_key = f"ip:invite:{client_ip(request)}"  # per-address limit (4x the per-account one)
    wait = state.login_throttle.retry_after(ip_key)
    if wait > 0:
        raise TooManyRequests(
            "Too many invalid invite attempts; try again later", headers={"Retry-After": str(math.ceil(wait))}
        )
    inv = db.scalar(select(Invite).where(Invite.token_hash == hash_token(token)))
    if inv is None or _status(inv) != "pending":
        state.login_throttle.failure(ip_key)
        audit(
            db,
            action="invite.accept_failed",
            resource_type="invite",
            resource_id=inv.id if inv else None,
            workspace_id=inv.workspace_id if inv else None,
            ip=client_ip(request),
            detail={"reason": _status(inv) if inv else "unknown_token"},
        )
        db.commit()  # keep the audit entry although the request fails
        raise InviteInvalid("This invite link is invalid, used, revoked or expired")
    return inv


@kinds_router.get("/{token}", response_model=InvitePreview, operation_id="previewInvite")
def preview_invite(token: str, request: Request, db: DbDep, state: StateDep) -> InvitePreview:
    """What the invite is for (no authentication; the token is the credential)."""
    inv = _live_invite(db, state, request, token)
    ws = db.get(Workspace, inv.workspace_id)
    exists = db.scalar(select(User.id).where(User.email == inv.email)) is not None
    return InvitePreview(
        workspace_name=ws.name if ws else "",
        email=inv.email,
        role=inv.role,  # type: ignore[arg-type]
        expires_at=inv.expires_at,
        account_exists=exists,
    )


@kinds_router.post(
    "/accept",
    response_model=SessionInfo,
    status_code=201,
    operation_id="acceptInvite",
    responses={200: {"model": SessionInfo, "description": "Accepted into an existing account"}},
)
def accept_invite(
    body: InviteAccept, request: Request, response: Response, db: DbDep, state: StateDep
) -> SessionInfo:
    """Accept an invite. New people send ``name`` + ``password``: the account, membership and a session are
    created (201). If an account with the invited email exists, sign in as it first and send just the token:
    the membership is added to that account (200). The token works once."""
    inv = _live_invite(db, state, request, body.token)
    ip = client_ip(request)
    existing = db.scalar(select(User).where(User.email == inv.email))
    if existing is not None:
        sess = lookup_session(db, request.cookies.get(SESSION_COOKIE))
        if sess is None or sess.user_id != existing.id:
            raise Conflict(
                "An account with this email exists: sign in as it, then accept the invite",
                code="account_exists",
            )
        if not constant_time_equals(request.headers.get(CSRF_HEADER), sess.csrf_token):
            raise Forbidden("Missing or invalid CSRF token", code="csrf_failed")
        user, created = existing, False
    else:
        if not body.password or not body.name:
            raise ApiError("Choose a name and a password to create your account", code="password_required")
        user = User(email=inv.email, name=body.name, password_hash=hash_password(body.password))
        db.add(user)
        db.flush()
        created = True
    if (
        db.scalar(
            select(Membership.id).where(
                Membership.workspace_id == inv.workspace_id, Membership.user_id == user.id
            )
        )
        is None
    ):
        db.add(Membership(workspace_id=inv.workspace_id, user_id=user.id, role=inv.role))
    inv.accepted_at = utcnow()
    inv.accepted_by = user.id
    db.flush()
    raw, new_sess = create_session(db, state.settings, user, request.headers.get("user-agent"), ip)
    set_session_cookies(response, state.settings, raw, new_sess)
    audit(
        db,
        action="invite.accept",
        resource_type="invite",
        resource_id=inv.id,
        workspace_id=inv.workspace_id,
        user_id=user.id,
        actor=user.email,
        ip=ip,
        detail={"role": inv.role, "account_created": created},
    )
    if not created:
        response.status_code = 200
    return SessionInfo(
        authenticated=True,
        auth_mode="password",
        user=UserOut.model_validate(user),
        csrf_token=new_sess.csrf_token,
        signup_allowed=state.settings.allow_signup,
        default_workspace_id=inv.workspace_id,
    )
