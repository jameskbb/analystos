"""Authentication: session bootstrap, signup/login/logout, profile and API tokens."""

from __future__ import annotations

import math
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import utcnow
from ..deps import DbDep, PrincipalDep, StateDep, client_ip
from ..errors import ApiError, Forbidden, NotFound, TooManyRequests, Unauthorized
from ..models import ApiToken, AuthSession, Membership, User
from ..schemas.auth import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenOut,
    LoginRequest,
    SessionInfo,
    SignupRequest,
    UpdateMeRequest,
    UserOut,
)
from ..schemas.common import ERROR_RESPONSES, OkResponse
from ..security.passwords import DUMMY_HASH, hash_password, needs_rehash, verify_password
from ..security.tokens import constant_time_equals, new_api_token
from ..services.auth import (
    SESSION_COOKIE,
    claim_local_user,
    clear_session_cookies,
    create_session,
    default_workspace_for,
    effective_auth_mode,
    ensure_local_user,
    is_local_request,
    lookup_session,
    password_users_exist,
    revoke_sessions,
    revoke_tokens,
    set_session_cookies,
)
from ..services.observability import audit
from ..state import AppState

router = APIRouter(prefix="/auth", tags=["auth"], responses=ERROR_RESPONSES)


def _bootstrap_configured(state: AppState) -> bool:
    tok = state.settings.bootstrap_token
    return tok is not None and bool(tok.get_secret_value())


def _local_proof(request: Request, db: Session, state: AppState) -> bool:
    """A valid local-analyst session cookie presented from this machine."""
    if not is_local_request(request, state.settings):
        return False
    sess = lookup_session(db, request.cookies.get(SESSION_COOKIE))
    user = db.get(User, sess.user_id) if sess else None
    return bool(user is not None and user.is_local)


def _signup_allowed(db: Session, state: AppState, request: Request) -> bool:
    settings = state.settings
    if settings.auth_mode == "local":
        return False
    if password_users_exist(db):
        return settings.allow_signup
    if settings.auth_mode == "auto" and settings.local_mode_allowed():
        # The first account takes over this machine's local data: it needs local access or the token.
        return _bootstrap_configured(state) or is_local_request(request, settings)
    return True


def _mode(mode: str) -> Literal["local", "password"]:
    return "local" if mode == "local" else "password"


def _session_info(
    db: Session,
    state: AppState,
    request: Request,
    *,
    user: User | None,
    sess: AuthSession | None,
    mode: str,
    local_mode_denied: bool = False,
) -> SessionInfo:
    first = not password_users_exist(db)
    signup_allowed = _signup_allowed(db, state, request)
    bootstrap_required = first and _bootstrap_configured(state)
    if sess is None or user is None:
        return SessionInfo(
            authenticated=False,
            auth_mode=_mode(mode),
            signup_allowed=signup_allowed,
            local_mode_denied=local_mode_denied,
            bootstrap_token_required=bootstrap_required,
        )
    ws = default_workspace_for(db, user)
    return SessionInfo(
        authenticated=True,
        auth_mode=_mode(mode),
        user=UserOut.model_validate(user),
        csrf_token=sess.csrf_token,
        default_workspace_id=ws.id if ws else None,
        signup_allowed=signup_allowed,
        local_mode_denied=local_mode_denied,
        bootstrap_token_required=bootstrap_required,
    )


@router.get("/session", response_model=SessionInfo, operation_id="getSession")
def get_session(request: Request, response: Response, db: DbDep, state: StateDep) -> SessionInfo:
    """Return the current session. In local mode this signs the local analyst in automatically, but only for
    requests from this machine (loopback peer and loopback Host; see docs/security.md)."""
    mode = effective_auth_mode(db, state.settings)
    local_ok = is_local_request(request, state.settings)
    sess = lookup_session(db, request.cookies.get(SESSION_COOKIE))
    user = db.get(User, sess.user_id) if sess else None
    if user is not None and user.is_local and (mode != "local" or not local_ok):
        user, sess = None, None
    denied = False
    if sess is None and mode == "local":
        if local_ok:
            user = ensure_local_user(db)
            raw, sess = create_session(
                db, state.settings, user, request.headers.get("user-agent"), client_ip(request)
            )
            set_session_cookies(response, state.settings, raw, sess)
            audit(
                db,
                action="auth.local_session",
                resource_type="user",
                resource_id=user.id,
                user_id=user.id,
                actor=user.email,
                ip=client_ip(request),
            )
        else:
            denied = True
            audit(
                db,
                action="auth.local_session_denied",
                resource_type="user",
                ip=client_ip(request),
                detail={"host": (request.headers.get("host") or "")[:200]},
            )
    return _session_info(db, state, request, user=user, sess=sess, mode=mode, local_mode_denied=denied)


@router.post("/signup", response_model=SessionInfo, status_code=201, operation_id="signup")
def signup(
    body: SignupRequest, request: Request, response: Response, db: DbDep, state: StateDep
) -> SessionInfo:
    """Create an account. The first account needs proof of local access (a local session from this machine) or
    ``AOS_BOOTSTRAP_TOKEN``, and only then takes over the local analyst's workspaces."""
    settings = state.settings
    ip = client_ip(request)
    ip_key = f"signup-ip:{ip}"
    wait = state.login_throttle.retry_after(ip_key)
    if wait > 0:
        raise TooManyRequests(
            "Too many sign-up attempts; try again later", headers={"Retry-After": str(math.ceil(wait))}
        )
    if not _signup_allowed(db, state, request):
        code = (
            "signup_requires_local_access"
            if settings.auth_mode == "auto" and not password_users_exist(db)
            else "signup_disabled"
        )
        raise Forbidden(
            "Sign-up is not available here"
            + (
                ": the first account must be created on the machine running AnalystOS, or with the bootstrap token"
                if code == "signup_requires_local_access"
                else ""
            ),
            code=code,
        )
    first = not password_users_exist(db)
    token_ok = _bootstrap_configured(state) and constant_time_equals(
        body.bootstrap_token,
        settings.bootstrap_token.get_secret_value() if settings.bootstrap_token else None,
    )
    if first and _bootstrap_configured(state) and not token_ok:
        state.login_throttle.failure(ip_key)
        audit(
            db,
            action="auth.signup_failed",
            resource_type="user",
            actor=body.email,
            ip=ip,
            detail={"reason": "bootstrap_token"},
        )
        db.commit()
        raise Forbidden(
            "The first account needs the server's bootstrap token", code="bootstrap_token_required"
        )
    local_proof = _local_proof(request, db, state)
    if (
        first
        and settings.auth_mode == "auto"
        and settings.local_mode_allowed()
        and not (token_ok or is_local_request(request, settings))
    ):
        raise Forbidden(
            "The first account must be created on the machine running AnalystOS, or with the bootstrap token",
            code="signup_requires_local_access",
        )
    if db.scalar(select(User.id).where(User.email == body.email)):
        hash_password(body.password)  # equalise timing with the success path
        state.login_throttle.failure(ip_key)
        audit(
            db,
            action="auth.signup_failed",
            resource_type="user",
            actor=body.email,
            ip=ip,
            detail={"reason": "email_taken"},
        )
        db.commit()  # keep the audit entry
        raise ApiError("Could not create an account with these details", code="signup_failed")
    pw_hash = hash_password(body.password)
    user = None
    claimed = False
    if first and (local_proof or token_ok):
        user = claim_local_user(db, body.email, body.name, pw_hash)
        claimed = user is not None
    if user is None:
        user = User(email=body.email, name=body.name, password_hash=pw_hash)
        db.add(user)
        db.flush()
    raw, sess = create_session(db, settings, user, request.headers.get("user-agent"), ip)
    set_session_cookies(response, settings, raw, sess)
    audit(
        db,
        action="auth.signup",
        resource_type="user",
        resource_id=user.id,
        user_id=user.id,
        actor=user.email,
        ip=ip,
        detail={
            "claimed_local_analyst": claimed,
            "via": "bootstrap_token" if token_ok else ("local_session" if local_proof else "open_signup"),
        },
    )
    return _session_info(db, state, request, user=user, sess=sess, mode="password")


@router.post("/login", response_model=SessionInfo, operation_id="login")
def login(
    body: LoginRequest, request: Request, response: Response, db: DbDep, state: StateDep
) -> SessionInfo:
    """Password sign-in. Repeated failures lock the account and the client address with exponential backoff
    (``429 too_many_attempts`` with ``Retry-After``)."""
    ip = client_ip(request)
    keys = (f"acct:{body.email}", f"ip:{ip}")
    throttle = state.login_throttle
    wait = throttle.retry_after(*keys)
    if wait > 0:
        audit(
            db,
            action="auth.login_throttled",
            resource_type="user",
            actor=body.email,
            ip=ip,
            detail={"retry_after_s": math.ceil(wait)},
        )
        db.commit()
        raise TooManyRequests(
            "Too many failed sign-in attempts; try again later", headers={"Retry-After": str(math.ceil(wait))}
        )
    user = db.scalar(select(User).where(User.email == body.email, User.is_local.is_(False)))
    if user is None or not user.password_hash:
        verify_password(DUMMY_HASH, body.password)  # equalise timing
        ok = False
    else:
        ok = verify_password(user.password_hash, body.password) and user.is_active
    if not ok or user is None:
        audit(
            db,
            action="auth.login_failed",
            resource_type="user",
            resource_id=user.id if user else None,
            actor=body.email,
            ip=ip,
        )
        throttle.failure(*keys)
        db.commit()  # keep the failed-login audit entry even though the request fails (nothing else is pending)
        raise Unauthorized("Invalid email or password", code="invalid_credentials")
    throttle.success(keys[0])
    if needs_rehash(user.password_hash or ""):
        user.password_hash = hash_password(body.password)
    raw, sess = create_session(db, state.settings, user, request.headers.get("user-agent"), ip)
    set_session_cookies(response, state.settings, raw, sess)
    audit(
        db,
        action="auth.login",
        resource_type="user",
        resource_id=user.id,
        user_id=user.id,
        actor=user.email,
        ip=ip,
    )
    return _session_info(
        db, state, request, user=user, sess=sess, mode=effective_auth_mode(db, state.settings)
    )


@router.post("/logout", response_model=OkResponse, operation_id="logout")
def logout(
    request: Request, response: Response, principal: PrincipalDep, db: DbDep, state: StateDep
) -> OkResponse:
    if principal.session is not None:
        principal.session.revoked_at = utcnow()
    clear_session_cookies(response, state.settings)
    audit(
        db,
        action="auth.logout",
        resource_type="user",
        resource_id=principal.user.id,
        user_id=principal.user.id,
        actor=principal.user.email,
        ip=client_ip(request),
    )
    return OkResponse()


@router.get("/me", response_model=UserOut, operation_id="getMe")
def get_me(principal: PrincipalDep) -> UserOut:
    return UserOut.model_validate(principal.user)


@router.patch("/me", response_model=UserOut, operation_id="updateMe")
def update_me(body: UpdateMeRequest, request: Request, principal: PrincipalDep, db: DbDep) -> UserOut:
    user = principal.user
    if body.name is not None:
        user.name = body.name
    if body.new_password is not None:
        if user.is_local:
            raise Forbidden("The local analyst has no password; sign up to create an account")
        if not body.current_password or not verify_password(user.password_hash or "", body.current_password):
            raise Forbidden("Current password is incorrect", code="invalid_credentials")
        user.password_hash = hash_password(body.new_password)
        keep = principal.session.id if principal.session is not None else None
        sessions = revoke_sessions(db, user.id, keep=keep)
        tokens = revoke_tokens(db, user.id) if body.revoke_tokens else 0
        audit(
            db,
            action="auth.password_changed",
            resource_type="user",
            resource_id=user.id,
            user_id=user.id,
            actor=user.email,
            ip=client_ip(request),
            detail={"revoked_sessions": sessions, "revoked_tokens": tokens},
        )
    return UserOut.model_validate(user)


@router.get("/tokens", response_model=list[ApiTokenOut], operation_id="listApiTokens", tags=["api-tokens"])
def list_tokens(principal: PrincipalDep, db: DbDep) -> list[ApiTokenOut]:
    rows = db.scalars(
        select(ApiToken).where(ApiToken.user_id == principal.user.id).order_by(ApiToken.created_at.desc())
    ).all()
    return [ApiTokenOut.model_validate(r) for r in rows]


@router.post(
    "/tokens",
    response_model=ApiTokenCreated,
    status_code=201,
    operation_id="createApiToken",
    tags=["api-tokens"],
)
def create_token(
    body: ApiTokenCreate, request: Request, principal: PrincipalDep, db: DbDep
) -> ApiTokenCreated:
    if principal.via != "session":
        raise Forbidden("API tokens can only be created from an interactive session", code="session_required")
    if body.workspace_id is not None:
        member = db.scalar(
            select(Membership).where(
                Membership.workspace_id == body.workspace_id, Membership.user_id == principal.user.id
            )
        )
        if member is None:
            raise NotFound("Workspace not found")
    raw, digest, prefix = new_api_token()
    tok = ApiToken(
        user_id=principal.user.id,
        workspace_id=body.workspace_id,
        name=body.name,
        token_hash=digest,
        prefix=prefix,
        read_only=body.read_only,
        expires_at=(utcnow() + timedelta(days=body.expires_in_days)) if body.expires_in_days else None,
    )
    db.add(tok)
    db.flush()
    audit(
        db,
        action="api_token.create",
        resource_type="api_token",
        resource_id=tok.id,
        user_id=principal.user.id,
        workspace_id=body.workspace_id,
        actor=principal.user.email,
        ip=client_ip(request),
        detail={"name": body.name, "read_only": body.read_only},
    )
    return ApiTokenCreated(token=raw, meta=ApiTokenOut.model_validate(tok))


@router.delete(
    "/tokens/{token_id}", response_model=OkResponse, operation_id="revokeApiToken", tags=["api-tokens"]
)
def revoke_token(token_id: str, request: Request, principal: PrincipalDep, db: DbDep) -> OkResponse:
    tok = db.get(ApiToken, token_id)
    if tok is None or tok.user_id != principal.user.id:
        raise NotFound("Token not found")
    tok.revoked_at = utcnow()
    audit(
        db,
        action="api_token.revoke",
        resource_type="api_token",
        resource_id=tok.id,
        user_id=principal.user.id,
        actor=principal.user.email,
        ip=client_ip(request),
    )
    return OkResponse()
