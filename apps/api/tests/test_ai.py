"""Optional AI wiring (review R-09) with a scripted provider: SQL generation with the bounded repair loop, the
staged orchestrator behind investigations, verified summary rewording, usage logging and the monthly budget.
Everything must also work with AI off."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from analystos_api.main import create_app
from analystos_investigator.llm import ScriptedProvider
from conftest import ApiClient, load_tables, make_settings
from test_workbench import MODEL_YAML, _data

API = "/api/v1"
EXTRA_DIMS = ("store", "amount_band", "weekday")
AI_MODEL_YAML = MODEL_YAML.replace(
    """relationships:""",
    """  - {name: store, entity: stores, expr: "CAST(store_id AS VARCHAR)", type: categorical}
  - {name: amount_band, entity: sales, expr: "CASE WHEN amount >= 12 THEN 'high' ELSE 'low' END", type: categorical}
  - {name: weekday, entity: sales, expr: "dayname(sale_date)", type: categorical}
relationships:""",
)


def _app(tmp_path: Path, script: dict[str, Any] | None) -> tuple[Any, list[ScriptedProvider]]:
    app = create_app(make_settings(tmp_path, AI_ENABLED=True, ANTHROPIC_API_KEY="sk-test-not-used"))
    made: list[ScriptedProvider] = []

    def factory(usage_log: Any) -> ScriptedProvider:
        p = ScriptedProvider(script or {}, usage_log=usage_log)
        made.append(p)
        return p

    if script is not None:
        app.state.aos.llm_factory = factory
    return app, made


def _ws(c: ApiClient, app: Any, *, ai: bool = True, **ai_settings: Any) -> str:
    c.get(f"{API}/auth/session")
    w = c.post(f"{API}/workspaces", json={"name": "AI"}).json()
    load_tables(app, w["id"], _data())
    r = c.put(f"{API}/workspaces/{w['id']}/semantic-models/current/yaml", json={"yaml": AI_MODEL_YAML})
    assert r.status_code == 200, r.text
    c.patch(
        f"{API}/workspaces/{w['id']}/settings",
        json={"investigation": {"require_plan_approval": False, "reference_date": "2026-09-15"}},
    )
    if ai:
        r = c.put(f"{API}/workspaces/{w['id']}/ai/settings", json={"enabled": True, **ai_settings})
        assert r.status_code == 200, r.text
        assert r.json()["active"] is ("monthly_budget_usd" not in ai_settings), r.text
    return f"{API}/workspaces/{w['id']}"


def test_generate_sql_repairs_a_failing_draft_and_logs_usage(tmp_path: Path) -> None:
    app, made = _app(
        tmp_path,
        {
            "generate_sql": {
                "sql": "SELECT channel, sum(amont) AS revenue FROM sales GROUP BY 1",
                "explanation": "Revenue by channel",
                "tables": ["sales"],
            },
            "repair_sql": {
                "sql": "SELECT channel, sum(amount) AS revenue FROM sales GROUP BY 1",
                "explanation": "the column is amount",
            },
        },
    )
    with ApiClient(app) as c:
        base = _ws(c, app)
        r = c.post(f"{base}/queries/generate", json={"prompt": "revenue by channel"})
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["valid"] and out["executable"] and out["repaired"]
        assert "sum(amount)" in out["sql"].lower() and "Repaired after a failed dry run" in out["explanation"]
        assert [a["ok"] for a in out["attempts"]] == [False, True]
        assert "amont" in out["attempts"][0]["error"]
        # The repair prompt carried the error and the schema of the referenced table (untrusted data blocks).
        prompt = "\n".join(made[-1].prompts_for("repair_sql")[0]["messages"][0].content)
        assert "amont" in prompt and "amount" in prompt
        usage = c.get(f"{base}/ai/usage").json()
        assert {t["task"] for t in usage["by_task"]} == {"generate_sql", "repair_sql"}
        assert usage["totals"]["calls"] == 2


def test_generate_sql_repair_is_bounded_and_failure_surfaced(tmp_path: Path) -> None:
    app, _ = _app(
        tmp_path,
        {
            "generate_sql": {"sql": "SELECT nope FROM sales", "explanation": "x", "tables": ["sales"]},
            "repair_sql": [
                {"sql": "SELECT nope2 FROM sales", "explanation": "try"},
                {"sql": "SELECT nope3 FROM sales", "explanation": "again"},
            ],
        },
    )
    with ApiClient(app) as c:
        base = _ws(c, app)
        out = c.post(f"{base}/queries/generate", json={"prompt": "something"}).json()
        assert out["valid"] is True and out["executable"] is False and out["repaired"] is False
        assert len(out["attempts"]) == 3 and not any(a["ok"] for a in out["attempts"])
        assert out["validation_error"]


def test_generated_unsafe_sql_is_never_executed(tmp_path: Path) -> None:
    app, made = _app(
        tmp_path, {"generate_sql": {"sql": "DELETE FROM sales", "explanation": "x", "tables": []}}
    )
    with ApiClient(app) as c:
        base = _ws(c, app)
        out = c.post(f"{base}/queries/generate", json={"prompt": "clean up"}).json()
        assert out["valid"] is False and out["attempts"] == [] and out["validation_error"]
        assert not made[-1].prompts_for("repair_sql")
        rows = c.post(f"{base}/queries/run", json={"sql": "SELECT count(*) AS n FROM sales"}).json()
        assert rows["result"]["rows"][0][0] == 54


def test_ai_disabled_everything_still_works(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, None)
    with ApiClient(app) as c:
        base = _ws(c, app, ai=False)
        r = c.post(f"{base}/queries/generate", json={"prompt": "revenue by channel"})
        assert r.status_code == 409 and r.json()["code"] == "ai_disabled"
        inv = c.post(
            f"{base}/investigations", json={"question": "Why did revenue change in August 2026?"}
        ).json()
        assert inv["status"] == "completed" and inv["orchestration"] is None
        assert c.get(f"{base}/ai/usage").json()["totals"]["calls"] == 0


def test_orchestrator_adds_validated_steps_and_rejects_unverified_text(tmp_path: Path) -> None:
    app, made = _app(
        tmp_path,
        {
            "formulate_analysis_plan": {
                "add_dimensions": [*EXTRA_DIMS[:2], "not_a_dimension"],
                "rationale": "mix",
            },
            "generate_followups": {"questions": ["Did the web channel lose share?", "Did 17 stores close?"]},
            "synthesize_findings": {"narrative": "Revenue fell 42% because a competitor opened next door."},
        },
    )
    with ApiClient(app) as c:
        base = _ws(c, app)
        inv = c.post(
            f"{base}/investigations", json={"question": "Break down August 2026 revenue by region"}
        ).json()
        assert inv["status"] == "completed", inv.get("failures")
        orch = inv["orchestration"]
        assert [s["stage"] for s in orch["stages"]][:2] == ["interpret_question", "identify_metrics"]
        assert orch["stages"][-1]["stage"] == "synthesize_findings"
        # Only an existing dimension becomes a plan step, marked as the assistant's.
        llm_steps = [s for s in inv["plan"]["steps"] if s["origin"] == "llm"]
        planned = [s["params"].get("dimension") for s in inv["plan"]["steps"] if s["origin"] != "llm"]
        added = [s["params"]["dimension"] for s in llm_steps]
        assert added and set(added) <= set(EXTRA_DIMS[:2]) and not set(added) & set(planned)
        assert orch["plan_steps_added"] == [s["id"] for s in llm_steps]
        assert any("not_a_dimension" in r for r in orch["rejected_outputs"])
        # The invented number is refused; the deterministic answer stays.
        assert orch["narrative_source"] == "template" and "42%" not in inv["brief_answer"]
        assert any(r.startswith("narrative rejected") for r in orch["rejected_outputs"])
        assert "Did the web channel lose share?" in inv["followups"]
        assert not any("17 stores" in q for q in inv["followups"])
        usage = c.get(f"{base}/ai/usage").json()
        assert {"formulate_analysis_plan", "generate_followups", "synthesize_findings"} <= {
            t["task"] for t in usage["by_task"]
        }
        assert all(i["investigation_id"] == inv["id"] for i in usage["items"])


def test_verified_ai_wording_for_answer_and_summary(tmp_path: Path) -> None:
    text = "Revenue moved between July and August; see the channel and region breakdowns."
    app, _ = _app(
        tmp_path,
        {
            "formulate_analysis_plan": {"add_dimensions": [], "rationale": "none"},
            "generate_followups": {"questions": []},
            "synthesize_findings": {"narrative": text},
        },
    )
    with ApiClient(app) as c:
        base = _ws(c, app)
        inv = c.post(
            f"{base}/investigations", json={"question": "Why did revenue change in August 2026?"}
        ).json()
        assert inv["orchestration"]["narrative_source"] == "llm_verified"
        assert inv["brief_answer"] == text and inv["orchestration"]["deterministic_brief"]
        root = inv["tree"]["root_id"]
        c.post(f"{base}/investigations/{inv['id']}/nodes/{root}/actions", json={"action": "confirm"})
        s = c.get(f"{base}/investigations/{inv['id']}/summary").json()["summary"]
        assert s["narrative_source"] == "llm_verified" and s["narrative"] == text
        assert s["observations"]  # the structured, deterministic parts are unchanged


def test_monthly_budget_pauses_ai(tmp_path: Path) -> None:
    app, _ = _app(tmp_path, {"generate_sql": {"sql": "SELECT 1 AS x", "explanation": "x", "tables": []}})
    with ApiClient(app) as c:
        base = _ws(c, app, monthly_budget_usd=0)
        s = c.get(f"{base}/ai/settings").json()
        assert s["over_budget"] is True and s["active"] is False
        r = c.post(f"{base}/queries/generate", json={"prompt": "anything"})
        assert r.status_code == 409 and r.json()["code"] == "ai_disabled"


@pytest.mark.parametrize("tool_loop", [True])
def test_tool_loop_setting_round_trips(tmp_path: Path, tool_loop: bool) -> None:
    app, _ = _app(tmp_path, {})
    with ApiClient(app) as c:
        base = _ws(c, app, tool_loop=tool_loop)
        assert c.get(f"{base}/ai/settings").json()["tool_loop"] is tool_loop
