"""Shared fixtures for investigator tests: a planted-driver DuckDB store and semantic model."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from analystos_engine.semantic.models import SemanticModel
from analystos_engine.store import WorkspaceStore
from analystos_investigator import ExecutionConfig, InvestigationRun, build_value_index, investigate
from inv_fixture import TODAY, load_model, make_store


@pytest.fixture(scope="session")
def model() -> SemanticModel:
    return load_model()


@pytest.fixture(scope="session")
def store(tmp_path_factory: pytest.TempPathFactory) -> Iterator[WorkspaceStore]:
    s = make_store(tmp_path_factory.mktemp("inv_store"))
    yield s
    s.close()


@pytest.fixture(scope="session")
def value_index(store: WorkspaceStore, model: SemanticModel) -> dict[str, list[str]]:
    return build_value_index(store, model)


@pytest.fixture(scope="session")
def revenue_run(
    store: WorkspaceStore, model: SemanticModel, value_index: dict[str, list[str]]
) -> InvestigationRun:
    return investigate(
        "Why did revenue decline in August?", store, model, TODAY, value_index=value_index, auto_approve=True
    )


@pytest.fixture()
def fresh_store(tmp_path: Path) -> Iterator[WorkspaceStore]:
    s = make_store(tmp_path / "ws")
    yield s
    s.close()


@pytest.fixture()
def default_config() -> ExecutionConfig:
    return ExecutionConfig()
