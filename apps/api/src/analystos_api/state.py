"""Process-wide application state shared by routers, services and background jobs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .security.secrets_box import SecretBox

if TYPE_CHECKING:
    from .jobs import JobRunner
    from .services.auth import LoginThrottle
    from .services.stores import StoreRegistry


@dataclass
class AppState:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    secret_box: SecretBox
    jobs: JobRunner = field(init=False)
    stores: StoreRegistry = field(init=False)
    login_throttle: LoginThrottle = field(init=False)
    # Optional override used to build the workspace LLM provider: ``(usage_log) -> LLMProvider``.
    # Production leaves it unset (the Anthropic provider is used); tests inject a scripted provider
    # so the AI paths run end to end without network access.
    llm_factory: Callable[[Any], Any] | None = None

    def __post_init__(self) -> None:
        from .services.auth import LoginThrottle

        s = self.settings
        self.login_throttle = LoginThrottle(
            max_failures=s.login_max_failures, base_s=s.login_lockout_s, max_s=s.login_lockout_max_s
        )
