"""Provider layer: schema conversion, Anthropic provider against a fake client, usage/cost log."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from analystos_investigator.llm import (
    AnthropicProvider,
    LLMOutputError,
    LLMRefusal,
    LLMUnavailable,
    Message,
    ModelConfig,
    NullProvider,
    ScriptedProvider,
    UsageLog,
    estimate_cost,
    strict_json_schema,
)
from pydantic import BaseModel


class Inner(BaseModel):
    name: str
    score: float | None = None


class Outer(BaseModel):
    items: list[Inner]
    note: str = ""


class FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _response(text: str, stop: str = "end_turn") -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop,
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
    )


def test_strict_schema_closes_objects_and_inlines_refs() -> None:
    schema = strict_json_schema(Outer)
    assert schema["additionalProperties"] is False
    assert "$defs" not in json.dumps(schema)
    item = schema["properties"]["items"]["items"]
    assert item["additionalProperties"] is False and item["required"] == ["name", "score"]
    assert "title" not in json.dumps(schema)


def test_null_provider_is_unavailable() -> None:
    p = NullProvider()
    assert not p.available
    with pytest.raises(LLMUnavailable):
        p.complete_structured("x", "sys", [], Outer)


def test_anthropic_provider_without_key_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = AnthropicProvider()
    assert not p.available
    with pytest.raises(LLMUnavailable):
        p.complete_structured("x", "sys", [], Outer)


def test_anthropic_provider_structured_call_and_usage(tmp_path: Path) -> None:
    log = UsageLog(tmp_path / "usage.jsonl")
    fake = FakeMessages(_response('{"items": [{"name": "a", "score": 1.5}], "note": "ok"}'))
    p = AnthropicProvider(client=SimpleNamespace(messages=fake), usage_log=log)
    out, usage = p.complete_structured(
        "formulate_analysis_plan",
        "system text",
        [Message(role="user", content=["instruction", "<untrusted_data>...</untrusted_data>"])],
        Outer,
        "large",
    )
    assert out.items[0].name == "a"
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["system"] == "system text"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert [b["text"] for b in call["messages"][0]["content"]] == [
        "instruction",
        "<untrusted_data>...</untrusted_data>",
    ]
    assert usage.input_tokens == 1000 and usage.output_tokens == 200
    assert usage.estimated_cost_usd == pytest.approx(1000 * 5 / 1e6 + 200 * 25 / 1e6)
    assert log.totals()["calls"] == 1
    assert (
        json.loads((tmp_path / "usage.jsonl").read_text().splitlines()[0])["stage"]
        == "formulate_analysis_plan"
    )


def test_model_tiers() -> None:
    cfg = ModelConfig()
    assert (cfg.for_tier("large"), cfg.for_tier("default"), cfg.for_tier("small")) == (
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5-20251001",
    )
    assert estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 0) == pytest.approx(1.0)
    assert estimate_cost("unknown-model", 10, 10) == 0.0


def test_refusal_and_invalid_output_are_errors() -> None:
    log = UsageLog()
    refusing = AnthropicProvider(
        client=SimpleNamespace(messages=FakeMessages(_response("", "refusal"))), usage_log=log
    )
    with pytest.raises(LLMRefusal):
        refusing.complete_structured("s", "sys", [], Outer)
    bad = AnthropicProvider(
        client=SimpleNamespace(messages=FakeMessages(_response('{"wrong": 1}'))), usage_log=log
    )
    with pytest.raises(LLMOutputError):
        bad.complete_structured("s", "sys", [], Outer)
    broken = AnthropicProvider(
        client=SimpleNamespace(messages=FakeMessages(RuntimeError("network down"))), usage_log=log
    )
    with pytest.raises(Exception, match="network down"):
        broken.complete_structured("s", "sys", [], Outer)
    assert [r.ok for r in log.records] == [False, False, False]
    assert log.by_stage()["s"]["calls"] == 3


def test_scripted_provider_validates_and_records_prompts() -> None:
    log = UsageLog()
    p = ScriptedProvider(
        {"a": [{"items": [], "note": "first"}, {"items": [], "note": "second"}], "b": {"bad": True}},
        usage_log=log,
    )
    msgs = [Message(role="user", content=["hello"])]
    assert p.complete_structured("a", "sys", msgs, Outer)[0].note == "first"
    assert p.complete_structured("a", "sys", msgs, Outer)[0].note == "second"
    assert p.complete_structured("a", "sys", msgs, Outer)[0].note == "second"  # last response repeats
    with pytest.raises(LLMOutputError):
        p.complete_structured("b", "sys", msgs, Outer)
    with pytest.raises(LLMUnavailable):
        p.complete_structured("missing", "sys", msgs, Outer)
    assert len(p.prompts_for("a")) == 3
    assert len(log.records) == 4
    assert log.to_jsonl().count("\n") == 4
