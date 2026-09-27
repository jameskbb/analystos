"""Runtime configuration (pydantic-settings).

Every setting can be supplied through the environment (or a `.env` file in the
working directory). Names match the deployment docs: ``DATABASE_URL``,
``DATA_DIR``, ``AOS_SECRET_KEY``, ``AUTH_MODE``, ``ANTHROPIC_API_KEY``,
``AI_ENABLED`` and the upload limits.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Storage
    database_url: str = Field(default="sqlite:///./data/analystos.db", alias="DATABASE_URL")
    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")

    # Security
    aos_secret_key: SecretStr | None = Field(default=None, alias="AOS_SECRET_KEY")
    environment: Literal["development", "production", "test"] = Field(default="development", alias="AOS_ENV")
    # auto: single-user local mode while no password users exist, password mode afterwards.
    # Local mode only ever serves requests from this machine (loopback peer and loopback Host);
    # see services/auth.py "Security model" and docs/security.md.
    auth_mode: Literal["auto", "local", "password"] = Field(default="auto", alias="AUTH_MODE")
    # Explicit opt-in to run local mode where it is unsafe: AOS_ENV=production, or requests that do
    # not come from loopback (e.g. behind a container proxy). Off by default.
    allow_insecure_local: bool = Field(default=False, alias="AOS_ALLOW_INSECURE_LOCAL")
    # Escape hatch: open self-service sign-up after the first account. Default off; people join through
    # workspace invites (POST /workspaces/{ws}/invites). The first account can always be created (see auth).
    allow_signup: bool = Field(default=False, alias="AOS_ALLOW_SIGNUP")
    # Shared secret the web server's proxy sends in X-AOS-Proxy-Secret together with X-AOS-Client-Addr (the
    # real browser address). Without it a proxied request cannot prove it comes from this machine.
    proxy_secret: SecretStr | None = Field(default=None, alias="AOS_PROXY_SECRET")
    invite_ttl_days: int = Field(default=7, ge=1, le=90, alias="AOS_INVITE_TTL_DAYS")
    # When set, creating the first account requires this token (``bootstrap_token`` in the body).
    bootstrap_token: SecretStr | None = Field(default=None, alias="AOS_BOOTSTRAP_TOKEN")
    login_max_failures: int = Field(default=5, ge=1, alias="AOS_LOGIN_MAX_FAILURES")
    login_lockout_s: float = Field(default=30.0, ge=0, alias="AOS_LOGIN_LOCKOUT_S")
    login_lockout_max_s: float = Field(default=900.0, ge=0, alias="AOS_LOGIN_LOCKOUT_MAX_S")
    session_ttl_hours: int = Field(default=24 * 14, alias="AOS_SESSION_TTL_HOURS")
    cookie_secure: bool = Field(default=False, alias="AOS_COOKIE_SECURE")
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"],
        alias="AOS_CORS_ORIGINS",
    )

    # AI
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    ai_enabled: bool = Field(default=False, alias="AI_ENABLED")

    # Uploads and execution limits
    max_upload_mb: int = Field(default=200, alias="AOS_MAX_UPLOAD_MB")
    query_row_limit: int = Field(default=10_000, alias="AOS_QUERY_ROW_LIMIT")
    analysis_row_limit: int = Field(default=100_000, ge=100, alias="AOS_ANALYSIS_ROW_LIMIT")
    query_timeout_s: float = Field(default=30.0, alias="AOS_QUERY_TIMEOUT_S")
    result_snapshot_rows: int = Field(default=500, alias="AOS_RESULT_SNAPSHOT_ROWS")
    python_timeout_s: float = Field(default=30.0, alias="AOS_PYTHON_TIMEOUT_S")
    python_mem_mb: int = Field(default=1024, alias="AOS_PYTHON_MEM_MB")

    auto_migrate: bool = Field(default=True, alias="AOS_AUTO_MIGRATE")

    # Background work
    job_workers: int = Field(default=4, alias="AOS_JOB_WORKERS")
    # "thread" runs jobs on the in-process pool; "inline" runs them synchronously (tests, CLI).
    job_execution: Literal["thread", "inline"] = Field(default="thread", alias="AOS_JOB_EXECUTION")

    # Server
    host: str = Field(default="127.0.0.1", alias="AOS_HOST")
    port: int = Field(default=8000, alias="AOS_PORT")
    log_level: str = Field(default="INFO", alias="AOS_LOG_LEVEL")
    log_json: bool = Field(default=True, alias="AOS_LOG_JSON")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    def workspace_dir(self, workspace_id: str) -> Path:
        return self.workspaces_dir / workspace_id

    def secret_key_bytes(self) -> bytes:
        """Return the master secret. In development a key is generated once and kept in DATA_DIR."""
        if self.aos_secret_key is not None and self.aos_secret_key.get_secret_value():
            return self.aos_secret_key.get_secret_value().encode()
        if self.environment == "production":
            raise RuntimeError("AOS_SECRET_KEY must be set when AOS_ENV=production")
        key_file = self.data_dir / ".secret_key"
        if key_file.exists():
            return key_file.read_bytes().strip()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key = secrets.token_urlsafe(48).encode()
        key_file.write_bytes(key)
        key_file.chmod(0o600)
        return key

    def fernet_key(self) -> bytes:
        """Derive a Fernet key (32 url-safe base64 bytes) from the master secret."""
        digest = hashlib.sha256(b"analystos-fernet-v1:" + self.secret_key_bytes()).digest()
        return base64.urlsafe_b64encode(digest)

    def local_mode_allowed(self) -> bool:
        """Local (password-less) mode only exists in development, unless explicitly opted into (decision Q2b)."""
        return self.environment == "development" or self.allow_insecure_local

    def check_secure_defaults(self) -> None:
        """Refuse insecure combinations at startup (review finding R-01)."""
        if (
            self.environment == "production"
            and self.auth_mode in {"auto", "local"}
            and not self.allow_insecure_local
        ):
            raise RuntimeError(
                f"AUTH_MODE={self.auth_mode} signs requests in without a password and is refused when "
                "AOS_ENV=production. Use AUTH_MODE=password, or set AOS_ALLOW_INSECURE_LOCAL=true if this "
                "server is only reachable from the local machine."
            )

    def ai_key_available(self) -> bool:
        return self.anthropic_api_key is not None and bool(self.anthropic_api_key.get_secret_value())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
