"""Single place where the API calls into ``analystos_engine`` for ingest, profiling,
relationships and quality. Keeping these calls together isolates the API from engine
signature details: when an engine signature changes, this module is the only API code that
has to follow (package boundaries: docs/architecture.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel


def dump(obj: Any) -> Any:
    """JSON-safe dump of engine objects (Pydantic models, lists, dicts)."""
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, list | tuple):
        return [dump(o) for o in obj]
    if isinstance(obj, dict):
        return {k: dump(v) for k, v in obj.items()}
    return obj


# ------------------------------------------------------------------ ingest


def _public_inspection(insp: Any) -> dict[str, Any]:
    data = dump(insp)
    data["path"] = data.get("file_name", "")  # never expose server paths
    return data


def inspect_file(path: Path, max_bytes: int | None = None) -> dict[str, Any]:
    from analystos_engine import ingest

    kwargs: dict[str, Any] = {"max_bytes": max_bytes} if max_bytes else {}
    return _public_inspection(ingest.inspect_file(path, **kwargs))


def _ingest_options(options: dict[str, Any]) -> Any:
    from analystos_engine.ingest import IngestOptions

    return IngestOptions.model_validate(options)


def ingest_file(store: Any, path: Path, options: dict[str, Any]) -> Any:
    from analystos_engine import ingest

    return ingest.ingest_file(store, path, _ingest_options(options))


def preview_file(
    path: Path, options: dict[str, Any], limit: int, max_bytes: int | None = None
) -> dict[str, Any]:
    """Re-inspect a file with explicit options (sheet, header row, delimiter, encoding, types) exactly as ingest
    will read it, returning at most ``limit`` preview rows per table (review R-35)."""
    from analystos_engine import ingest

    kwargs: dict[str, Any] = {"max_bytes": max_bytes} if max_bytes else {}
    return _public_inspection(
        ingest.inspect_file(path, options=_ingest_options(options), preview_rows=limit, **kwargs)
    )


def ingest_options_schema() -> dict[str, Any]:
    from analystos_engine import ingest

    model = getattr(ingest, "IngestOptions", None)
    if model is not None and isinstance(model, type) and issubclass(model, BaseModel):
        return model.model_json_schema()
    return {}


# ------------------------------------------------------------------ profiling / relationships / quality


def profile_table(store: Any, table: str) -> dict[str, Any]:
    from analystos_engine.profiling import profile_table as _profile

    return dump(_profile(store, table))


def discover_relationships(store: Any, tables: list[str]) -> list[dict[str, Any]]:
    from analystos_engine import relationships

    return dump(relationships.discover(store, tables))


def analyze_join(
    store: Any, *, from_table: str, from_col: str, to_table: str, to_col: str, cardinality: str
) -> dict[str, Any]:
    """Measure a table-level relationship on the actual data (observed cardinality, fan-out, orphans)."""
    from analystos_engine.semantic import joins
    from analystos_engine.semantic.models import Relationship

    rel = Relationship.model_validate(
        {
            "from_entity": from_table,
            "from_col": from_col,
            "to_entity": to_table,
            "to_col": to_col,
            "cardinality": cardinality,
        }
    )
    return dump(joins.analyze_join(store, rel))
