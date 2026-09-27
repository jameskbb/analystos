from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..security.passwords import MIN_PASSWORD_LENGTH
from .common import ApiModel


def _normalise_email(v: str) -> str:
    v = v.strip().lower()
    if "@" not in v or v.startswith("@") or v.endswith("@") or len(v) > 320 or " " in v:
        raise ValueError("Invalid email address")
    return v


class UserOut(ApiModel):
    id: str
    email: str
    name: str
    is_local: bool
    created_at: datetime
    last_login_at: datetime | None = None


class SessionInfo(BaseModel):
    authenticated: bool
    auth_mode: Literal["local", "password"]
    user: UserOut | None = None
    csrf_token: str | None = Field(
        default=None, description="Echo this in the X-CSRF-Token header on unsafe requests"
    )
    signup_allowed: bool
    default_workspace_id: str | None = None
    local_mode_denied: bool = Field(
        default=False,
        description="Local mode is on but this request is not from this machine; sign in instead",
    )
    bootstrap_token_required: bool = Field(
        default=False, description="The first account needs AOS_BOOTSTRAP_TOKEN in `bootstrap_token`"
    )


class SignupRequest(BaseModel):
    email: str
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=1024)
    bootstrap_token: str | None = Field(
        default=None,
        max_length=1024,
        description="AOS_BOOTSTRAP_TOKEN: required for the first account when the server sets one; it also "
        "lets that account take over the local analyst's workspaces",
    )

    _email = field_validator("email")(_normalise_email)


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=1024)

    _email = field_validator("email")(_normalise_email)


class UpdateMeRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    current_password: str | None = None
    new_password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH, max_length=1024)
    revoke_tokens: bool = Field(
        default=False, description="With new_password: also revoke every API token of this user"
    )


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    workspace_id: str | None = Field(default=None, description="Restrict the token to one workspace")
    read_only: bool = False
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiTokenOut(ApiModel):
    id: str
    name: str
    prefix: str
    workspace_id: str | None
    read_only: bool
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ApiTokenCreated(BaseModel):
    token: str = Field(description="The raw token. Shown exactly once; store it securely.")
    meta: ApiTokenOut
