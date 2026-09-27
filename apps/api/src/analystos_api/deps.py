"""FastAPI dependencies: database session, authentication and workspace authorization."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Path, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import utcnow
from .errors import Forbidden, NotFound, Unauthorized
from .models import Membership, User, Workspace
from .security.tokens import constant_time_equals
from .services.auth import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    Principal,
    client_address,
    effective_auth_mode,
    is_local_request,
    lookup_api_token,
    lookup_session,
)
from .state import AppState

ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_state(request: Request) -> AppState:
    return request.app.state.aos


def get_db(request: Request) -> Iterator[Session]:
    state: AppState = request.app.state.aos
    db = state.session_factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


StateDep = Annotated[AppState, Depends(get_state)]
DbDep = Annotated[Session, Depends(get_db)]


def client_ip(request: Request) -> str | None:
    """The requesting client's address: the authenticated proxy header (``X-AOS-Client-Addr``) when present,
    else the socket peer (see ``services.auth.client_address``)."""
    state: AppState = request.app.state.aos
    return client_address(request, state.settings)


def get_principal(request: Request, db: DbDep, state: StateDep) -> Principal:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        raw = auth[7:].strip()
        tok = lookup_api_token(db, raw)
        if tok is None:
            raise Unauthorized("Invalid or expired API token", code="invalid_token")
        user = db.get(User, tok.user_id)
        if user is None or not user.is_active:
            raise Unauthorized("Token owner is disabled", code="invalid_token")
        if tok.read_only and request.method not in SAFE_METHODS and not _read_only_allowed(request):
            raise Forbidden("This API token is read-only", code="token_read_only")
        tok.last_used_at = utcnow()
        principal = Principal(user=user, via="token", token=tok)
        request.state.principal = principal
        return principal

    sess = lookup_session(db, request.cookies.get(SESSION_COOKIE))
    if sess is None:
        raise Unauthorized("Authentication required", code="not_authenticated")
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise Unauthorized("Authentication required", code="not_authenticated")
    if user.is_local and effective_auth_mode(db, state.settings) != "local":
        raise Unauthorized("Local mode is disabled; please sign in", code="not_authenticated")
    if user.is_local and not is_local_request(request, state.settings):
        raise Unauthorized(
            "Local mode only serves requests from this machine; please sign in", code="not_authenticated"
        )
    if request.method not in SAFE_METHODS:
        header = request.headers.get(CSRF_HEADER)
        cookie = request.cookies.get(CSRF_COOKIE)
        if not (
            constant_time_equals(header, sess.csrf_token) and constant_time_equals(cookie, sess.csrf_token)
        ):
            raise Forbidden("Missing or invalid CSRF token", code="csrf_failed")
    now = utcnow()
    if (now - sess.last_seen_at).total_seconds() > 60:
        sess.last_seen_at = now
    principal = Principal(user=user, via="session", session=sess)
    request.state.principal = principal
    return principal


def _read_only_allowed(request: Request) -> bool:
    """POST endpoints that only read data (running a read-only query/analysis) are allowed for read-only tokens."""
    route = request.scope.get("route")
    extra = getattr(route, "openapi_extra", None) or {}
    return bool(extra.get("x-read-only-post"))


PrincipalDep = Annotated[Principal, Depends(get_principal)]


@dataclass
class WorkspaceContext:
    workspace: Workspace
    user: User
    role: str
    principal: Principal
    ip: str | None

    @property
    def workspace_id(self) -> str:
        return self.workspace.id

    @property
    def user_id(self) -> str:
        return self.user.id

    def can(self, role: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]


def _workspace_context(
    request: Request,
    principal: PrincipalDep,
    db: DbDep,
    workspace_id: Annotated[str, Path(description="Workspace id")],
) -> WorkspaceContext:
    if principal.token_workspace_id and principal.token_workspace_id != workspace_id:
        raise NotFound("Workspace not found")
    row = db.execute(
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Workspace.id == workspace_id, Membership.user_id == principal.user.id)
    ).first()
    if row is None:
        # Same response whether the workspace does not exist or the caller is not a member.
        raise NotFound("Workspace not found")
    ws, role = row
    return WorkspaceContext(
        workspace=ws, user=principal.user, role=role, principal=principal, ip=client_ip(request)
    )


def require_role(role: str) -> Callable[..., WorkspaceContext]:
    def dependency(ctx: Annotated[WorkspaceContext, Depends(_workspace_context)]) -> WorkspaceContext:
        if not ctx.can(role):
            raise Forbidden(
                f"This action requires the {role} role in this workspace", code="insufficient_role"
            )
        return ctx

    dependency.__name__ = f"require_{role}"
    return dependency


ViewerCtx = Annotated[WorkspaceContext, Depends(require_role("viewer"))]
EditorCtx = Annotated[WorkspaceContext, Depends(require_role("editor"))]
OwnerCtx = Annotated[WorkspaceContext, Depends(require_role("owner"))]

READ_ONLY_POST = {"x-read-only-post": True}
