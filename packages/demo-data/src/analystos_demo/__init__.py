"""Summit Supply Co. demo data for AnalystOS: generator, answer key, semantic model and loader."""

import datetime as _dt

DEMO_WORKSPACE_NAME = "Summit Supply Co."
REFERENCE_DATE = _dt.date(2026, 9, 30)
"""The demo's as-of date: the last day with data. No row is dated after it, and relative
periods ("last month") should be resolved against it, not against the wall clock."""

from .generator import GENERATOR_VERSION, Manifest, ManifestFile, generate  # noqa: E402
from .loader import (  # noqa: E402
    TABLES,
    DemoLoadResult,
    DemoPaths,
    DQRuleSpec,
    TableLoad,
    bootstrap_paths,
    default_cache_dir,
    dq_rules,
    load_demo,
    read_table_arrow,
    semantic_model,
    semantic_model_yaml,
)
from .measure import connect as connect_ground_truth  # noqa: E402

__all__ = [
    "DEMO_WORKSPACE_NAME",
    "REFERENCE_DATE",
    "GENERATOR_VERSION",
    "TABLES",
    "DQRuleSpec",
    "DemoLoadResult",
    "DemoPaths",
    "Manifest",
    "ManifestFile",
    "TableLoad",
    "bootstrap_paths",
    "connect_ground_truth",
    "default_cache_dir",
    "dq_rules",
    "generate",
    "load_demo",
    "read_table_arrow",
    "semantic_model",
    "semantic_model_yaml",
]
