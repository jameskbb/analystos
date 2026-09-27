"""Shared fixtures for the analytical evals: the demo dataset, a loaded workspace store,
the semantic model and an independent ground-truth DuckDB connection over the raw files."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TODAY = "2026-09-18"


def demo_cache_dir() -> Path:
    env = os.environ.get("AOS_DEMO_CACHE")
    return Path(env) if env else REPO_ROOT / "data" / "demo" / "summit-supply-seed42"


@pytest.fixture(scope="session")
def demo() -> Any:
    from analystos_demo import bootstrap_paths

    return bootstrap_paths(demo_cache_dir(), seed=42)


@pytest.fixture(scope="session")
def scenarios(demo: Any) -> dict[str, Any]:
    return json.loads(demo.scenarios_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def loaded(demo: Any) -> Any:
    from analystos_demo import load_demo
    from analystos_engine.store import WorkspaceStore

    store = WorkspaceStore.in_memory()
    result = load_demo(store, cache_dir=demo.data_dir)
    yield store, result
    store.close()


@pytest.fixture(scope="session")
def store(loaded: Any) -> Any:
    return loaded[0]


@pytest.fixture(scope="session")
def model(loaded: Any) -> Any:
    return loaded[1].semantic_model


@pytest.fixture(scope="session")
def gt(demo: Any) -> Any:
    """Ground truth: plain DuckDB over the raw files (never through the semantic compiler)."""
    from analystos_demo.measure import connect

    con = connect(demo.data_dir)
    con.execute(
        "CREATE VIEW customers_dedup AS SELECT * FROM customers "
        "QUALIFY row_number() OVER (PARTITION BY customer_id ORDER BY customer_name) = 1"
    )
    yield con
    con.close()
