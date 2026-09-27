"""Summit Supply Co. demo bootstrap: data, semantic model, relationships, DQ rules, profiles."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..jobs import JobContext
from ..models import (
    Dataset,
    DimensionRecord,
    Entity,
    GlossaryTermRecord,
    MetricRecord,
    MetricTreeRecord,
    QualityRule,
    RelationshipRecord,
    SemanticSnapshot,
    Workspace,
)
from ..state import AppState
from .datasets import profile_dataset, refresh_relationship_suggestions, register_table
from .observability import timed_event
from .semantic import import_model
from .workspaces import merged_settings

_PARAM_MAP = {
    "min": "min_value",
    "max": "max_value",
    "values": "allowed_values",
    "min_value": "min_value",
    "max_value": "max_value",
    "allowed_values": "allowed_values",
    "pattern": "pattern",
    "ref_table": "ref_table",
    "ref_column": "ref_column",
    "sql": "sql",
    "inclusive": "inclusive",
}


def demo_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("analystos_demo") is not None


def wipe_workspace_content(db: Session, state: AppState, workspace_id: str) -> None:
    """Remove data and semantic definitions (used by demo reset). Investigations and findings are kept only if
    they do not depend on removed data; for a reset they are removed too."""
    from ..models import (
        ArtifactRecord,
        Dashboard,
        Finding,
        Investigation,
        Notebook,
        QueryRun,
        Report,
        SavedQuery,
        Upload,
    )

    for model in (
        Finding,
        ArtifactRecord,
        Investigation,
        Dashboard,
        Report,
        Notebook,
        SavedQuery,
        QueryRun,
        QualityRule,
        RelationshipRecord,
        MetricTreeRecord,
        GlossaryTermRecord,
        DimensionRecord,
        MetricRecord,
        Entity,
        SemanticSnapshot,
        Dataset,
        Upload,
    ):
        db.execute(delete(model).where(model.workspace_id == workspace_id))  # type: ignore[attr-defined]
    db.flush()
    with state.stores.writing(workspace_id) as store:
        for t in store.list_tables(include_row_counts=False):
            store.drop_table(t.name)


def load_job(
    state: AppState, workspace_id: str, user_id: str | None, seed: int = 42
) -> Callable[[JobContext], dict[str, Any]]:
    def run(ctx: JobContext) -> dict[str, Any]:
        analystos_demo = importlib.import_module("analystos_demo")

        cache_dir = state.settings.data_dir / "demo" / f"summit-supply-seed{seed}"
        ctx.progress(0.05, "Generating Summit Supply Co. data")
        with (
            timed_event(
                state.session_factory,
                category="import",
                name="demo_load",
                workspace_id=workspace_id,
                user_id=user_id,
                detail={"seed": seed},
            ) as ev,
            state.stores.writing(workspace_id) as store,
        ):
            result = analystos_demo.load_demo(store, cache_dir=cache_dir, seed=seed)
            ev.detail["tables"] = len(result.tables)
        ctx.progress(0.35, "Registering datasets")
        with state.session_factory() as db:
            ws = db.get(Workspace, workspace_id)
            assert ws is not None
            dataset_ids: list[str] = []
            failures: list[dict[str, str]] = []
            for t in result.tables:
                try:
                    ds, _ = register_table(
                        db,
                        state,
                        workspace_id,
                        t.table,
                        name=t.table.replace("_", " ").title(),
                        source_kind="demo",
                        source_ref={
                            "source_file": t.source_file,
                            "sheet": t.sheet,
                            "content_hash": result.content_hash,
                        },
                        user_id=user_id,
                        ingest_options={"demo_seed": seed},
                    )
                except Exception as exc:  # noqa: BLE001 - one table must not fail the whole load (review R-36)
                    db.rollback()
                    failures.append(
                        {"step": "register", "table": t.table, "error": f"{type(exc).__name__}: {exc}"}
                    )
                    continue
                dataset_ids.append(ds.id)
                db.commit()
            db.commit()
            ctx.progress(0.45, "Importing the semantic model")
            counts = import_model(
                db, ws, result.semantic_model, user_id=user_id, change_note="Summit Supply Co. demo model"
            )
            existing = {
                r.name: r
                for r in db.scalars(select(QualityRule).where(QualityRule.workspace_id == workspace_id))
            }
            tables = {
                d.table_name: d.id
                for d in db.scalars(select(Dataset).where(Dataset.workspace_id == workspace_id))
            }
            for spec in result.dq_rules:
                params = {_PARAM_MAP[k]: v for k, v in (spec.params or {}).items() if k in _PARAM_MAP}
                rule = existing.get(spec.name) or QualityRule(
                    workspace_id=workspace_id, name=spec.name, created_by=user_id
                )
                rule.dataset_id = tables.get(spec.table)
                rule.table_name = spec.table
                rule.kind = spec.kind
                rule.column = spec.column
                rule.params = params
                rule.severity = spec.severity
                rule.origin = "demo"
                rule.status = "active"
                rule.description = spec.description
                db.add(rule)
            settings = merged_settings(ws)
            end = _demo_end()
            settings["investigation"] = {**settings.get("investigation", {}), "reference_date": end}
            settings["demo"] = {
                "seed": seed,
                "content_hash": result.content_hash,
                "name": analystos_demo.DEMO_WORKSPACE_NAME,
            }
            ws.settings = settings
            db.commit()
        ctx.progress(0.55, "Profiling datasets")
        for i, ds_id in enumerate(dataset_ids):
            try:
                profile_dataset(state, workspace_id, ds_id, user_id)
            except Exception as exc:  # noqa: BLE001 - record profile_status=failed and keep loading (R-36)
                with state.session_factory() as db:
                    failed = db.get(Dataset, ds_id)
                    if failed is not None:
                        failed.profile_status = "failed"
                        failed.profile = {"error": f"{type(exc).__name__}: {exc}"}
                        db.commit()
                    failures.append(
                        {
                            "step": "profile",
                            "table": failed.table_name if failed else ds_id,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
            ctx.progress(0.55 + 0.3 * (i + 1) / len(dataset_ids), "Profiling datasets")
        ctx.progress(0.87, "Discovering relationships")
        try:
            suggestions = refresh_relationship_suggestions(state, workspace_id, user_id)
        except Exception as exc:  # noqa: BLE001
            suggestions = 0
            failures.append({"step": "relationships", "table": "", "error": f"{type(exc).__name__}: {exc}"})
        ctx.progress(0.9, "Measuring approved joins")
        analyzed, mismatched = _measure_approved_joins(state, workspace_id, failures)
        ctx.progress(0.92, "Running data-quality rules")
        from ..routers.quality import execute_rule

        failing = 0
        with state.session_factory() as db:
            for rule in db.scalars(
                select(QualityRule).where(
                    QualityRule.workspace_id == workspace_id, QualityRule.status == "active"
                )
            ).all():
                try:
                    run = execute_rule(db, state, rule, user_id)
                    db.commit()
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    failures.append(
                        {
                            "step": "quality",
                            "table": rule.table_name or "",
                            "error": f"{rule.name}: {type(exc).__name__}: {exc}",
                        }
                    )
                    continue
                failing += 0 if run.passed else 1
        if failures:
            ctx.progress(0.99, f"Loaded with {len(failures)} problem(s); see the job result")
        return {
            "workspace_id": workspace_id,
            "tables": len(dataset_ids),
            "semantic": counts,
            "relationship_suggestions": suggestions,
            "dq_rules_failing": failing,
            "joins_analyzed": analyzed,
            "join_cardinality_mismatches": mismatched,
            "content_hash": result.content_hash,
            "reference_date": end,
            "failures": failures,
        }

    return run


def _measure_approved_joins(
    state: AppState, workspace_id: str, failures: list[dict[str, str]]
) -> tuple[int, int]:
    """Measure every approved relationship on the loaded data so join_analysis is never empty and a declared
    many-to-one with duplicate "one"-side keys is flagged at load time (review R-07, R-48)."""
    from .datasets import measure_relationship

    analyzed = mismatched = 0
    with state.session_factory() as db:
        rels = db.scalars(
            select(RelationshipRecord).where(
                RelationshipRecord.workspace_id == workspace_id, RelationshipRecord.status == "approved"
            )
        ).all()
        for rel in rels:
            measure_relationship(state, workspace_id, rel)
            ja = rel.join_analysis or {}
            if ja.get("error"):
                failures.append({"step": "join_analysis", "table": rel.from_table, "error": str(ja["error"])})
            else:
                analyzed += 1
                mismatched += 1 if ja.get("cardinality_mismatch") else 0
        db.commit()
    return analyzed, mismatched


def _demo_end() -> str:
    try:
        stories = importlib.import_module("analystos_demo.stories")

        return str(stories.END)
    except (ImportError, AttributeError):
        return "2026-09-30"
