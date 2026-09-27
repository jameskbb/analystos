"""Provider protocol, usage accounting and errors for the optional LLM layer.

The deterministic engine never needs a provider. When one is configured it may only
assist inside stages whose outputs are validated against known semantic-model entities
and executed artifacts (see :mod:`analystos_investigator.llm.orchestrator`).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, Field

ModelTier = Literal["small", "default", "large"]
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Base class for provider failures. Callers fall back to deterministic behaviour."""


class LLMUnavailable(LLMError):
    """No provider is configured (for example no API key)."""


class LLMRefusal(LLMError):
    """The model declined the request."""


class LLMOutputError(LLMError):
    """The model returned output that does not validate against the stage schema."""


class Message(BaseModel):
    """One chat message. ``content`` is a list of text blocks kept separate on purpose:
    instructions and untrusted data are never concatenated into one string."""

    role: Literal["user", "assistant"]
    content: list[str]


class Usage(BaseModel):
    provider: str
    model: str
    stage: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    estimated_cost_usd: float = 0.0
    ok: bool = True
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    def complete_structured(
        self,
        stage: str,
        system: str,
        messages: list[Message],
        schema: type[T],
        model_tier: ModelTier = "default",
    ) -> tuple[T, Usage]: ...


@dataclass(frozen=True)
class Pricing:
    input_per_mtok: float
    output_per_mtok: float

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok) / 1_000_000


# USD per million tokens, Anthropic first-party list prices.
DEFAULT_PRICING: dict[str, Pricing] = {
    "claude-opus-5": Pricing(5.0, 25.0),
    "claude-sonnet-5": Pricing(2.0, 10.0),
    "claude-haiku-4-5-20251001": Pricing(1.0, 5.0),
    "claude-haiku-4-5": Pricing(1.0, 5.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p = DEFAULT_PRICING.get(model)
    return p.cost(input_tokens, output_tokens) if p else 0.0


class UsageLog:
    """Thread-safe log of every LLM call (spec §54). Optionally appends JSON lines to a file."""

    def __init__(self, path: Path | str | None = None) -> None:
        self._records: list[Usage] = []
        self._lock = threading.Lock()
        self._path = Path(path) if path else None

    def record(self, usage: Usage) -> None:
        with self._lock:
            self._records.append(usage)
            if self._path is not None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(usage.model_dump_json() + "\n")

    @property
    def records(self) -> list[Usage]:
        with self._lock:
            return list(self._records)

    def totals(self) -> dict[str, float]:
        recs = self.records
        return {
            "calls": float(len(recs)),
            "input_tokens": float(sum(r.input_tokens for r in recs)),
            "output_tokens": float(sum(r.output_tokens for r in recs)),
            "estimated_cost_usd": round(sum(r.estimated_cost_usd for r in recs), 6),
            "latency_ms": round(sum(r.latency_ms for r in recs), 3),
        }

    def by_stage(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for r in self.records:
            s = out.setdefault(
                r.stage, {"calls": 0.0, "input_tokens": 0.0, "output_tokens": 0.0, "cost": 0.0}
            )
            s["calls"] += 1
            s["input_tokens"] += r.input_tokens
            s["output_tokens"] += r.output_tokens
            s["cost"] += r.estimated_cost_usd
        return out

    def to_jsonl(self) -> str:
        return "".join(json.dumps(r.model_dump(mode="json")) + "\n" for r in self.records)
