"""LLM providers: Anthropic (optional dependency), Null (no key) and Scripted (replay).

``AnthropicProvider`` uses structured outputs (``output_config.format`` with a JSON schema
derived from the stage's Pydantic model) and validates every response with Pydantic.
"""

from __future__ import annotations

import copy
import json
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from .base import (
    LLMError,
    LLMOutputError,
    LLMRefusal,
    LLMUnavailable,
    Message,
    ModelTier,
    T,
    Usage,
    UsageLog,
    estimate_cost,
)


@dataclass(frozen=True)
class ModelConfig:
    large: str = "claude-opus-5"
    default: str = "claude-sonnet-5"
    small: str = "claude-haiku-4-5-20251001"

    def for_tier(self, tier: ModelTier) -> str:
        return {"large": self.large, "default": self.default, "small": self.small}[tier]


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema adjusted for structured outputs: every object is closed
    (``additionalProperties: false``), refs are inlined and titles dropped."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                return resolve(copy.deepcopy(defs[name]))
            out = {k: resolve(v) for k, v in node.items() if k not in ("title", "default")}
            if out.get("type") == "object":
                out.setdefault("properties", {})
                out["additionalProperties"] = False
                out["required"] = sorted(out["properties"].keys())
            return out
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    result = resolve(schema)
    assert isinstance(result, dict)
    return result


class NullProvider:
    """Provider used when no LLM is configured. Every call raises ``LLMUnavailable``."""

    name = "none"

    @property
    def available(self) -> bool:
        return False

    def complete_structured(
        self,
        stage: str,
        system: str,
        messages: list[Message],
        schema: type[T],
        model_tier: ModelTier = "default",
    ) -> tuple[T, Usage]:
        raise LLMUnavailable("no LLM provider is configured; the deterministic engine handles this stage")


class AnthropicProvider:
    """Claude via the official ``anthropic`` SDK (install extra ``analystos-investigator[llm]``)."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: Any | None = None,
        models: ModelConfig | None = None,
        usage_log: UsageLog | None = None,
        max_tokens: int = 16000,
        timeout_s: float = 120.0,
    ) -> None:
        self.models = models or ModelConfig()
        self.usage_log = usage_log
        self.max_tokens = max_tokens
        self._client = client
        if self._client is None:
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if key:
                try:
                    import anthropic
                except ImportError as exc:  # pragma: no cover - depends on optional extra
                    raise LLMUnavailable(
                        "the 'anthropic' package is not installed; install analystos-investigator[llm]"
                    ) from exc
                self._client = anthropic.Anthropic(api_key=key, timeout=timeout_s, max_retries=2)

    @property
    def available(self) -> bool:
        return self._client is not None

    def complete_structured(
        self,
        stage: str,
        system: str,
        messages: list[Message],
        schema: type[T],
        model_tier: ModelTier = "default",
    ) -> tuple[T, Usage]:
        if self._client is None:
            raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
        model = self.models.for_tier(model_tier)
        api_messages = [
            {"role": m.role, "content": [{"type": "text", "text": block} for block in m.content]}
            for m in messages
        ]
        started = time.perf_counter()
        try:
            response = self._client.messages.create(
                model=model,
                max_tokens=self.max_tokens,
                system=system,
                messages=api_messages,
                output_config={"format": {"type": "json_schema", "schema": strict_json_schema(schema)}},
            )
        except Exception as exc:  # SDK errors are re-raised as LLMError so callers fall back
            usage = Usage(
                provider=self.name,
                model=model,
                stage=stage,
                ok=False,
                error=type(exc).__name__,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            self._log(usage)
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc
        latency = (time.perf_counter() - started) * 1000
        in_tok = int(getattr(response.usage, "input_tokens", 0) or 0)
        out_tok = int(getattr(response.usage, "output_tokens", 0) or 0)
        usage = Usage(
            provider=self.name,
            model=model,
            stage=stage,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency,
            estimated_cost_usd=estimate_cost(model, in_tok, out_tok),
        )
        if getattr(response, "stop_reason", None) == "refusal":
            usage.ok, usage.error = False, "refusal"
            self._log(usage)
            raise LLMRefusal(f"model declined stage {stage}")
        text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text")
        try:
            parsed = schema.model_validate_json(text)
        except ValidationError as exc:
            usage.ok, usage.error = False, "invalid_output"
            self._log(usage)
            raise LLMOutputError(f"stage {stage} output failed validation: {exc.errors()[:3]}") from exc
        self._log(usage)
        return parsed, usage

    def _log(self, usage: Usage) -> None:
        if self.usage_log is not None:
            self.usage_log.record(usage)


ScriptedResponse = BaseModel | dict[str, Any] | str | Exception | Callable[[str, str, list[Message]], Any]


class ScriptedProvider:
    """Deterministic provider that replays scripted responses per stage.

    Used for offline replays and tests. Responses are validated against the stage schema
    exactly like a real provider's output, and every call is recorded (including the full
    prompt) so tests can assert on what the model was shown.
    """

    name = "scripted"

    def __init__(
        self,
        script: dict[str, ScriptedResponse | list[ScriptedResponse]],
        *,
        usage_log: UsageLog | None = None,
        model: str = "scripted-model",
    ) -> None:
        self._script: dict[str, list[ScriptedResponse]] = {
            k: list(v) if isinstance(v, list) else [v] for k, v in script.items()
        }
        self.calls: list[dict[str, Any]] = []
        self.usage_log = usage_log
        self.model = model

    @property
    def available(self) -> bool:
        return True

    def complete_structured(
        self,
        stage: str,
        system: str,
        messages: list[Message],
        schema: type[T],
        model_tier: ModelTier = "default",
    ) -> tuple[T, Usage]:
        self.calls.append({"stage": stage, "system": system, "messages": messages, "tier": model_tier})
        queue = self._script.get(stage)
        if not queue:
            raise LLMUnavailable(f"no scripted response for stage {stage!r}")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(item) and not isinstance(item, type) and not isinstance(item, BaseModel):
            item = item(stage, system, messages)
        if isinstance(item, Exception):
            raise item
        prompt_chars = len(system) + sum(len(b) for m in messages for b in m.content)
        usage = Usage(
            provider=self.name, model=self.model, stage=stage, input_tokens=prompt_chars // 4, output_tokens=0
        )
        try:
            if isinstance(item, BaseModel):
                parsed = schema.model_validate(item.model_dump())
            elif isinstance(item, str):
                parsed = schema.model_validate_json(item)
            else:
                parsed = schema.model_validate(item)
        except ValidationError as exc:
            usage.ok, usage.error = False, "invalid_output"
            if self.usage_log:
                self.usage_log.record(usage)
            raise LLMOutputError(str(exc)) from exc
        usage.output_tokens = len(json.dumps(parsed.model_dump(mode="json"))) // 4
        if self.usage_log:
            self.usage_log.record(usage)
        return parsed, usage

    def prompts_for(self, stage: str) -> Iterable[dict[str, Any]]:
        return [c for c in self.calls if c["stage"] == stage]
