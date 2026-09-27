"""Optional LLM layer: providers, usage log, untrusted-data rendering, verifier, tools,
SQL repair and the staged orchestrator. Nothing here is required by the deterministic engine."""

from .base import (
    LLMError,
    LLMOutputError,
    LLMProvider,
    LLMRefusal,
    LLMUnavailable,
    Message,
    ModelTier,
    Usage,
    UsageLog,
    estimate_cost,
)
from .providers import AnthropicProvider, ModelConfig, NullProvider, ScriptedProvider, strict_json_schema
from .untrusted import UNTRUSTED_DATA_POLICY, render_untrusted, render_user_request, sanitize_text
from .verifier import EvidenceNumbers, VerificationResult, extract_claims, hypothesis_leaks, verify_text

# The orchestrator, tools and SQL repair depend on the deterministic engine modules, which
# themselves import ``llm.base``; they are loaded lazily to keep the import graph acyclic.
_LAZY = {
    "STAGES": "orchestrator",
    "OrchestrationResult": "orchestrator",
    "Orchestrator": "orchestrator",
    "StageRecord": "orchestrator",
    "RepairAttempt": "sql_repair",
    "RepairOutcome": "sql_repair",
    "execute_with_repair": "sql_repair",
    "ToolRegistry": "tools",
    "ToolResult": "tools",
    "ToolState": "tools",
    "default_registry": "tools",
}


def __getattr__(name: str) -> object:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(f"{__name__}.{module}"), name)


__all__ = [
    "STAGES",
    "UNTRUSTED_DATA_POLICY",
    "AnthropicProvider",
    "EvidenceNumbers",
    "LLMError",
    "LLMOutputError",
    "LLMProvider",
    "LLMRefusal",
    "LLMUnavailable",
    "Message",
    "ModelConfig",
    "ModelTier",
    "NullProvider",
    "OrchestrationResult",
    "Orchestrator",
    "RepairAttempt",
    "RepairOutcome",
    "ScriptedProvider",
    "StageRecord",
    "ToolRegistry",
    "ToolResult",
    "ToolState",
    "Usage",
    "UsageLog",
    "VerificationResult",
    "default_registry",
    "estimate_cost",
    "execute_with_repair",
    "extract_claims",
    "hypothesis_leaks",
    "render_untrusted",
    "render_user_request",
    "sanitize_text",
    "strict_json_schema",
    "verify_text",
]
