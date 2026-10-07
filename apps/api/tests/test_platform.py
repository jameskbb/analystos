"""Migrations, logging redaction, jobs, upload sniffing units."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from analystos_api.db import Base
from analystos_api.logs import redact
from analystos_api.migrate import upgrade_to_head
from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_head_on_sqlite_matches_models(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/m.db")
    upgrade_to_head(engine)
    upgrade_to_head(engine)  # idempotent
    tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= tables and "alembic_version" in tables
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff


def test_migration_renders_for_postgres() -> None:
    """Offline SQL generation for PostgreSQL (no server needed) must succeed."""
    import io

    from alembic import command
    from analystos_api.migrate import alembic_config

    buf = io.StringIO()
    cfg = alembic_config("postgresql://u:p@localhost/db")
    cfg.output_buffer = buf
    command.upgrade(cfg, "head", sql=True)
    sql = buf.getvalue()
    assert "CREATE TABLE workspaces" in sql and "TIMESTAMP WITH TIME ZONE" in sql


def test_redaction() -> None:
    event = {
        "password": "hunter2",
        "config": {"host": "db", "api_key": "abc"},
        "token_prefix": "aos_12",
        "message": "failed for postgresql://user:pw@host/db with aos_" + "a" * 30,
        "tokens_in": 12,
        "note": "key sk-ant-api03-abcdefghijk",
    }
    out = redact(event)
    s = json.dumps(out)
    assert "hunter2" not in s and "abc" not in s.replace("abcdefghijk", "") and "pw@" not in s
    assert "aos_aaaa" not in s and "sk-ant-api03" not in s
    assert out["tokens_in"] == 12 and out["token_prefix"] == "aos_12" and out["config"]["host"] == "db"


def test_structlog_output_is_redacted(capsys: Any) -> None:
    from analystos_api.logs import redaction_processor

    ev = redaction_processor(
        None, "info", {"event": "x", "secrets": {"password": "p"}, "authorization": "Bearer y"}
    )
    assert ev["secrets"] == "[REDACTED]" and ev["authorization"] == "[REDACTED]"
    logging.getLogger("x").info("ok")


def test_jobs_thread_mode_and_interrupted_recovery(tmp_path: Path) -> None:
    from analystos_api.main import create_app
    from analystos_api.models import Job
    from conftest import ApiClient, make_settings

    app = create_app(make_settings(tmp_path, AOS_JOB_EXECUTION="thread"))
    state = app.state.aos
    with ApiClient(app) as c:
        c.get("/api/v1/auth/session")
        ws = c.post("/api/v1/workspaces", json={"name": "J"}).json()["id"]
        job_id = state.jobs.submit(
            kind="unit",
            fn=lambda ctx: (ctx.progress(0.5, "half"), {"x": 1})[1],
            workspace_id=ws,
            user_id=None,
        )
        state.jobs.wait(job_id, 10)
        job = c.get(f"/api/v1/workspaces/{ws}/jobs/{job_id}").json()
        assert job["status"] == "succeeded" and job["result"] == {"x": 1}

        def boom(_ctx: Any) -> None:
            raise ValueError("bad input")

        failed = state.jobs.submit(kind="unit", fn=boom, workspace_id=ws, user_id=None)
        state.jobs.wait(failed, 10)
        job = c.get(f"/api/v1/workspaces/{ws}/jobs/{failed}").json()
        assert job["status"] == "failed" and "bad input" in job["error"]
        with state.session_factory() as db:
            db.add(Job(workspace_id=ws, kind="stuck", status="running"))
            db.commit()
        assert state.jobs.recover_interrupted() == 1


def test_upload_sniffing_units(tmp_path: Path) -> None:
    from analystos_api.services.uploads import safe_filename, sniff_matches

    p = tmp_path / "a.parquet"
    p.write_bytes(b"PAR1xxxxPAR1")
    assert sniff_matches(p, "parquet")[0]
    p.write_bytes(b"PAR1xxxx")
    assert not sniff_matches(p, "parquet")[0]
    assert safe_filename("../../x y.csv") == "x_y.csv" and safe_filename("..hidden") == "hidden"


def _plan_model() -> Any:
    from analystos_engine.semantic.models import SemanticModel

    def simple(mid: str, **kw: Any) -> dict[str, Any]:
        return {
            "id": mid,
            "name": mid.replace("_", " ").title(),
            "kind": "simple",
            "expr": mid,
            "entity": "s",
            "agg": "sum",
            **kw,
        }

    return SemanticModel.model_validate(
        {
            "entities": [{"name": "s", "table": "s", "primary_key": "id"}],
            "metrics": [
                simple("revenue", canonical=True),
                simple("cogs", higher_is_better=False),
                simple("freight", higher_is_better=False),
                {
                    "id": "gross_margin",
                    "name": "Gross Margin",
                    "kind": "derived",
                    "formula": "revenue - cogs",
                },
                {
                    "id": "profit",
                    "name": "Profit",
                    "kind": "derived",
                    "formula": "gross_margin - freight",
                    "tags": ["kpi"],
                },
                simple("plan_revenue", plan_for="revenue", tags=["plan"]),
                simple("fcst_revenue", plan_for="revenue", tags=["forecast"]),
                simple("revenue_target", plan_for="revenue", tags=["target", "targets"]),
                simple("untagged_revenue", plan_for="revenue"),
                simple("plan_cogs", plan_for="cogs", tags=["plan"]),
                simple("plan_freight", plan_for="freight", tags=["budget"]),
                {
                    "id": "plan_gross_margin",
                    "name": "Plan GM",
                    "kind": "derived",
                    "formula": "plan_revenue - plan_cogs",
                    "plan_for": "gross_margin",
                    "tags": ["plan"],
                },
                {
                    "id": "plan_profit",
                    "name": "Plan Profit",
                    "kind": "derived",
                    "formula": "plan_gross_margin - plan_freight",
                    "plan_for": "profit",
                    "tags": ["plan"],
                },
            ],
            "metric_trees": [
                {
                    "root_metric": "profit",
                    "nodes": [
                        {"parent": "profit", "child": "gross_margin", "relation": "additive"},
                        {"parent": "profit", "child": "freight", "relation": "subtractive"},
                        {"parent": "gross_margin", "child": "revenue", "relation": "additive"},
                        {"parent": "gross_margin", "child": "cogs", "relation": "subtractive"},
                    ],
                },
            ],
        }
    )


def test_plan_pairs_prefer_plan_over_target_and_forecast_and_read_roles() -> None:
    from analystos_api.routers.platform import plan_pairs

    pairs = plan_pairs(_plan_model())
    assert pairs["revenue"] == ("plan_revenue", "plan")
    assert pairs["freight"] == ("plan_freight", "budget")
    assert pairs["profit"] == ("plan_profit", "plan")


def test_plan_metric_order_groups_revenue_lead_parts_lines_heads() -> None:
    from analystos_api.routers.platform import plan_metric_order, plan_pairs

    model = _plan_model()
    groups = plan_metric_order(model, plan_pairs(model), ["profit"])
    assert groups == [["revenue"], ["profit"], ["gross_margin", "freight"], ["cogs"], []]


def test_order_plan_variances_ranks_the_lines_by_variance() -> None:
    from types import SimpleNamespace

    from analystos_api.routers.platform import order_plan_variances

    v = {
        m: SimpleNamespace(metric_id=m, variance=x)
        for m, x in {
            "rev": 1.0,
            "op": -9.0,
            "gm": -7.0,
            "ox": 2.0,
            "cogs": 5.0,
            "freight": 3.0,
            "ot": -4.0,
        }.items()
    }
    groups = [["rev"], ["op"], ["gm", "ox"], ["freight", "ot", "cogs", "missing"], []]
    assert [x.metric_id for x in order_plan_variances(groups, v)] == [
        "rev",
        "op",
        "gm",
        "ox",
        "cogs",
        "ot",
        "freight",
    ]


def test_plan_status_uses_tolerance_and_direction() -> None:
    from analystos_api.routers.platform import plan_status

    assert plan_status(0.003, 100.0, True) == "on_plan"
    assert plan_status(-0.47, -1.9e6, True) == "unfavorable"
    assert plan_status(0.12, 2.4e5, False) == "unfavorable"  # a cost over plan
    assert plan_status(-0.05, -1e4, False) == "favorable"
    assert plan_status(None, None, True) == "on_plan"


def test_kpi_exports_carry_the_plan_comparison() -> None:
    from analystos_api.services.exports import _kpi_html, blocks_to_md

    k = {
        "label": "Revenue",
        "value": 1003.0,
        "format": "currency",
        "pct_change": 0.1,
        "plan": 1000.0,
        "plan_variance_pct": 0.003,
        "plan_label": "Revenue Plan",
    }
    assert "+0.3% vs Revenue Plan ($1,000" in _kpi_html([k])
    md = blocks_to_md("Review", [{"id": "b1", "type": "kpi_overview", "title": "KPIs", "items": [k]}])
    assert "+0.3% vs Revenue Plan" in md
    no_plan = {**k, "plan": None}
    assert "vs Revenue Plan" not in _kpi_html([no_plan])


def test_operating_review_bridge_exports_as_a_table() -> None:
    from analystos_api.services.exports import blocks_to_html, blocks_to_md, bridge_rows

    bridge = {
        "metric_label": "Profit",
        "metric_format": "currency",
        "start_label": "Plan",
        "start": 200.0,
        "end_label": "Actual",
        "end": 100.0,
        "steps": [{"label": "Gross Margin", "amount": -75.0}, {"label": "Other lines, net", "amount": -25.0}],
    }
    assert bridge_rows(bridge) == [
        ["Profit, plan", "$200"],
        ["Gross Margin", "-$75"],
        ["Other lines, net", "-$25"],
        ["Profit, actual", "$100"],
    ]
    block = {
        "id": "r",
        "type": "narrative",
        "label": "Operating review",
        "markdown": "Memo",
        "bridge": bridge,
    }
    assert "Other lines, net" in blocks_to_md("R", [block]) and "Profit, plan" in blocks_to_html([block])
    assert bridge_rows(None) == []
    as_block = {"id": "b", "type": "bridge", "title": "Plan to actual", **bridge}
    assert "Plan to actual" in blocks_to_md("R", [as_block]) and "Profit, actual" in blocks_to_html(
        [as_block]
    )


def test_stakeholder_term_reads_the_governed_vocabulary() -> None:
    from analystos_api.routers.platform import stakeholder_term
    from analystos_engine.semantic.models import SemanticModel

    model = SemanticModel.model_validate(
        {
            "entities": [{"name": "s", "table": "s", "primary_key": "id"}],
            "metrics": [
                {
                    "id": "revenue",
                    "name": "Revenue",
                    "kind": "simple",
                    "expr": "a",
                    "entity": "s",
                    "agg": "sum",
                    "synonyms": ["net revenue"],
                },
                {
                    "id": "profit",
                    "name": "Operating Profit",
                    "kind": "simple",
                    "expr": "b",
                    "entity": "s",
                    "agg": "sum",
                    "synonyms": ["operating income"],
                },
                {
                    "id": "units",
                    "name": "Units",
                    "kind": "simple",
                    "expr": "c",
                    "entity": "s",
                    "agg": "sum",
                    "synonyms": ["volume"],
                },
                {
                    "id": "orders",
                    "name": "Orders",
                    "kind": "simple",
                    "expr": "d",
                    "entity": "s",
                    "agg": "sum",
                },
            ],
            "glossary": [
                {
                    "term": "Revenue",
                    "definition": "x",
                    "metric_id": "revenue",
                    "synonyms": ["sales", "top line"],
                },
                {"term": "Operating Profit", "definition": "x", "metric_id": "profit"},
                {"term": "Net Profit", "definition": "x", "metric_id": "profit"},
            ],
        }
    )
    assert stakeholder_term(model, "profit") == "net profit"
    assert stakeholder_term(model, "revenue") == "sales"
    assert stakeholder_term(model, "units") == "volume"
    assert stakeholder_term(model, "orders") == "orders"
