"""AI provider wiring: per-workspace settings, key resolution and usage logging.

Data only leaves the machine when *all* of these hold: the server allows AI
(``AI_ENABLED=true``), the workspace enabled it in its AI settings, and an API key is
available (workspace key, encrypted at rest, or ``ANTHROPIC_API_KEY``). Otherwise the
deterministic engine runs alone and every feature still works.
"""

from __future__ import annotations

from typing import Any

from ..models import Workspace
from ..security.secrets_box import SecretDecryptionError
from ..state import AppState
from .observability import record_ai_usage
from .workspaces import merged_settings


def workspace_key(state: AppState, ws: Workspace) -> str | None:
    token = (ws.settings or {}).get("ai", {}).get("api_key_encrypted")
    if not token:
        return None
    try:
        return str(state.secret_box.decrypt(token).get("api_key") or "") or None
    except SecretDecryptionError:
        return None


def ai_status(state: AppState, ws: Workspace) -> dict[str, Any]:
    ai = merged_settings(ws).get("ai", {})
    ws_key = workspace_key(state, ws)
    source = "workspace" if ws_key else ("environment" if state.settings.ai_key_available() else "none")
    active = bool(state.settings.ai_enabled and ai.get("enabled") and source != "none")
    return {
        "server_ai_enabled": state.settings.ai_enabled,
        "workspace_enabled": bool(ai.get("enabled")),
        "has_api_key": source != "none",
        "key_source": source,
        "active": active,
    }


def month_spend_usd(state: AppState, ws: Workspace) -> float:
    """Estimated AI spend of this workspace in the current calendar month (UTC)."""
    import datetime as dt

    from sqlalchemy import func, select

    from ..models import AIUsage
    from .writer import writer_for

    writer_for(state.session_factory).flush(5)
    start = dt.datetime.now(dt.UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with state.session_factory() as db:
        total = db.scalar(
            select(func.coalesce(func.sum(AIUsage.est_cost_usd), 0.0)).where(
                AIUsage.workspace_id == ws.id, AIUsage.created_at >= start
            )
        )
    return float(total or 0.0)


def over_budget(state: AppState, ws: Workspace) -> bool:
    budget = merged_settings(ws).get("ai", {}).get("monthly_budget_usd")
    return budget is not None and month_spend_usd(state, ws) >= float(budget)


def get_llm(state: AppState, ws: Workspace, *, investigation_id: str | None = None) -> Any | None:
    """An ``LLMProvider`` for this workspace, or None when AI is not fully configured (or the monthly budget is
    spent). Every call the provider makes is written to the AI usage log (provider, model, task, tokens,
    latency, estimated cost)."""
    status = ai_status(state, ws)
    if not status["active"] or over_budget(state, ws):
        return None
    from analystos_investigator.llm import AnthropicProvider, ModelConfig
    from analystos_investigator.llm.base import Usage, UsageLog

    ai = merged_settings(ws).get("ai", {})
    factory, ws_id = state.session_factory, ws.id

    class _DbUsageLog(UsageLog):
        def record(self, usage: Usage) -> None:
            super().record(usage)
            record_ai_usage(
                factory,
                workspace_id=ws_id,
                provider=usage.provider,
                model=usage.model,
                task=usage.stage,
                tokens_in=usage.input_tokens,
                tokens_out=usage.output_tokens,
                latency_ms=usage.latency_ms,
                est_cost_usd=usage.estimated_cost_usd,
                success=usage.ok,
                investigation_id=investigation_id,
            )

    if state.llm_factory is not None:
        return state.llm_factory(_DbUsageLog())
    key = workspace_key(state, ws) or (
        state.settings.anthropic_api_key.get_secret_value() if state.settings.anthropic_api_key else None
    )
    models = ModelConfig(
        large=ai.get("model_large", "claude-opus-5"),
        default=ai.get("model_default", "claude-sonnet-5"),
        small=ai.get("model_small", "claude-haiku-4-5-20251001"),
    )
    provider = AnthropicProvider(api_key=key, models=models, usage_log=_DbUsageLog())
    return provider if provider.available else None
