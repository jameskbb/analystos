"""Bootstrap helpers the API's `/demo/load` (and tests) call.

`bootstrap_paths()` makes sure a verified copy of the dataset exists on disk.
`load_demo(store)` loads every table into a workspace DuckDB store and returns the
semantic model, relationships, data-quality rules and answer key to persist.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

import duckdb
import yaml
from pydantic import BaseModel, ConfigDict, Field

from .generator import GENERATOR_VERSION, Manifest, code_hash, generate
from .measure import load_budget_sheets

DATA_PACKAGE_DIR = Path(__file__).parent / "data"
SEMANTIC_MODEL_PATH = DATA_PACKAGE_DIR / "semantic_model.yaml"
DQ_RULES_PATH = DATA_PACKAGE_DIR / "dq_rules.yaml"

# Load order: dimensions first, facts after, so a partially failed load is still coherent.
TABLES: tuple[str, ...] = (
    "regions",
    "customer_segments",
    "branches",
    "sales_reps",
    "customers",
    "products",
    "product_costs",
    "orders",
    "order_lines",
    "returns",
    "inventory_snapshots",
    "leads",
    "opportunities",
    "revenue_forecast",
    "budgets_branch",
    "budgets_category",
    "targets",
    "operating_expenses",
)

# Columns that must stay text even when most values look like another type (messy on purpose).
_FORCE_VARCHAR = {"customers": ["account_opened"], "returns": ["order_id"]}


class DemoPaths(BaseModel):
    data_dir: Path
    manifest: Manifest
    semantic_model_path: Path
    dq_rules_path: Path
    scenarios_path: Path


class DQRuleSpec(BaseModel):
    """Engine-neutral rule definition (kinds match analystos_engine.quality)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    table: str
    column: str | None = None
    kind: Literal[
        "not_null",
        "unique",
        "range",
        "between",
        "fk_exists",
        "not_future",
        "allowed_values",
        "regex",
        "custom_sql",
    ]
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["error", "warning"] = "error"
    description: str = ""


class TableLoad(BaseModel):
    table: str
    source_file: str
    rows: int
    sheet: str | None = None


class DemoLoadResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    workspace_name: str
    data_dir: Path
    content_hash: str
    tables: list[TableLoad]
    semantic_model: Any  # analystos_engine.semantic.models.SemanticModel
    semantic_model_yaml: str
    relationships: list[Any]  # analystos_engine.semantic.models.Relationship (approved and proposed)
    dq_rules: list[DQRuleSpec]
    scenarios: dict[str, Any]


def default_cache_dir(seed: int = 42) -> Path:
    base = Path(os.environ.get("DATA_DIR", "./data"))
    return (base / "demo" / f"summit-supply-seed{seed}").resolve()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _valid_cache(data_dir: Path, seed: int) -> Manifest | None:
    mpath = data_dir / "manifest.json"
    if not mpath.exists():
        return None
    try:
        manifest = Manifest.model_validate_json(mpath.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if (
        manifest.seed != seed
        or manifest.generator_version != GENERATOR_VERSION
        or manifest.code_hash != code_hash()
    ):
        return None
    for f in manifest.files:
        p = data_dir / f.path
        if not p.exists() or _sha256(p) != f.sha256:
            return None
    if not (data_dir / "scenarios.json").exists():
        return None
    return manifest


def bootstrap_paths(cache_dir: str | Path | None = None, seed: int = 42) -> DemoPaths:
    """Return paths to a verified dataset, generating it into `cache_dir` if missing or stale."""
    data_dir = Path(cache_dir).resolve() if cache_dir else default_cache_dir(seed)
    manifest = _valid_cache(data_dir, seed)
    if manifest is None:
        manifest = generate(data_dir, seed=seed)
    return DemoPaths(
        data_dir=data_dir,
        manifest=manifest,
        semantic_model_path=SEMANTIC_MODEL_PATH,
        dq_rules_path=DQ_RULES_PATH,
        scenarios_path=data_dir / "scenarios.json",
    )


def semantic_model_yaml() -> str:
    return SEMANTIC_MODEL_PATH.read_text(encoding="utf-8")


def semantic_model() -> Any:
    """The demo semantic model as an `analystos_engine` SemanticModel (validated)."""
    from analystos_engine.semantic.models import SemanticModel

    model = SemanticModel.from_yaml(semantic_model_yaml())
    model.raise_for_errors()
    return model


def dq_rules() -> list[DQRuleSpec]:
    data = yaml.safe_load(DQ_RULES_PATH.read_text(encoding="utf-8"))
    return [DQRuleSpec.model_validate(r) for r in data["rules"]]


def read_table_arrow(data_dir: Path, manifest: Manifest, table: str) -> Any:
    """Read one demo table from disk as a pyarrow Table, exactly as the raw file has it."""
    entry = manifest.file(table)
    path = data_dir / entry.path
    if entry.format == "xlsx":
        import pyarrow as pa

        branch, category = load_budget_sheets(path)
        df = branch if table == "budgets_branch" else category
        return pa.Table.from_pandas(df, preserve_index=False)
    con = duckdb.connect()
    try:
        p = str(path).replace("'", "''")
        if entry.format == "csv":
            forced = _FORCE_VARCHAR.get(table)
            types = ""
            if forced:
                types = ", types={" + ", ".join(f"'{c}': 'VARCHAR'" for c in forced) + "}"
            sql = f"SELECT * FROM read_csv('{p}', header=true, auto_detect=true, sample_size=-1{types})"
        elif entry.format == "parquet":
            sql = f"SELECT * FROM read_parquet('{p}')"
        elif entry.format == "json":
            sql = f"SELECT * FROM read_json_auto('{p}')"
        else:  # pragma: no cover - manifest formats are fixed by the generator
            raise ValueError(f"unsupported format {entry.format}")
        rel = con.execute(sql)
        fetch = getattr(rel, "to_arrow_table", None) or rel.fetch_arrow_table
        return fetch()
    finally:
        con.close()


def load_demo(
    store: Any, cache_dir: str | Path | None = None, seed: int = 42, *, replace: bool = True
) -> DemoLoadResult:
    """Load Summit Supply Co. into `store` (an analystos_engine WorkspaceStore).

    Tables keep the raw files' mess (duplicates, padded IDs, casing, negative quantities):
    AnalystOS is supposed to find those problems, not have them silently cleaned away.
    """
    from . import DEMO_WORKSPACE_NAME

    paths = bootstrap_paths(cache_dir, seed)
    loads: list[TableLoad] = []
    for table in TABLES:
        arrow = read_table_arrow(paths.data_dir, paths.manifest, table)
        store.write_table(table, arrow, if_exists="replace" if replace else "fail")
        entry = paths.manifest.file(table)
        loads.append(TableLoad(table=table, source_file=entry.path, rows=arrow.num_rows, sheet=entry.sheet))
    model = semantic_model()
    return DemoLoadResult(
        workspace_name=DEMO_WORKSPACE_NAME,
        data_dir=paths.data_dir,
        content_hash=paths.manifest.content_hash,
        tables=loads,
        semantic_model=model,
        semantic_model_yaml=semantic_model_yaml(),
        relationships=list(model.relationships),
        dq_rules=dq_rules(),
        scenarios=json.loads(paths.scenarios_path.read_text(encoding="utf-8")),
    )
