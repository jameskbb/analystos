"""Contract tests (review R-40): apps/api/API.md, which the web app codes against, must match the OpenAPI
document the server actually publishes, both at path level and for the response fields the web consumes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from conftest import ApiClient

API_MD = Path(__file__).resolve().parents[1] / "API.md"
PARAM = re.compile(r"\{[^}/]*\}")
METHODS = {"get", "post", "put", "patch", "delete"}


@pytest.fixture(scope="module")
def openapi(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from analystos_api.main import create_app
    from conftest import make_settings

    app = create_app(make_settings(tmp_path_factory.mktemp("contract")))
    with ApiClient(app) as c:
        return c.get("/api/openapi.json").json()


@pytest.fixture(scope="module")
def doc() -> str:
    return API_MD.read_text()


def _norm(path: str) -> str:
    path = path.split("?")[0].rstrip("/")
    for prefix in ("/api/v1",):
        if path.startswith(prefix):
            path = path[len(prefix) :]
    return PARAM.sub("{}", path)


def _doc_paths(doc: str) -> tuple[set[str], set[str]]:
    """(absolute paths, relative ``.../x`` suffixes) found in backticks in API.md tables."""
    absolute: set[str] = set()
    relative: set[str] = set()
    for line in doc.splitlines():
        if not line.startswith("|"):
            continue
        for token in re.findall(r"`([^`]+)`", line):
            token = token.split(" ")[0]
            if (
                token.startswith("/workspaces")
                or token.startswith("/auth")
                or token.startswith("/demo")
                or token.startswith("/data-sources")
                or token.startswith("/api/")
            ):
                absolute.add(_norm(token))
            elif token.startswith(".../") or (token.startswith("/") and len(token) > 1):
                relative.add(_norm(token.replace("...", "")))
    return absolute, relative


def _operations(openapi: dict[str, Any]) -> list[tuple[str, str]]:
    return [(m.upper(), _norm(p)) for p, item in openapi["paths"].items() for m in item if m in METHODS]


def test_every_operation_is_documented(openapi: dict[str, Any], doc: str) -> None:
    absolute, relative = _doc_paths(doc)
    missing = []
    for method, path in _operations(openapi):
        if path in absolute:
            continue
        # Relative rows (".../relationships/{id}/approve") document the tail of a workspace path.
        tail = path.split("/workspaces/{}", 1)[-1]
        if any(tail.endswith(r) for r in relative if r.count("/") >= 2):
            continue
        missing.append(f"{method} {path}")
    assert not missing, "operations missing from API.md:\n" + "\n".join(sorted(missing))


def test_every_documented_absolute_path_exists(openapi: dict[str, Any], doc: str) -> None:
    absolute, _ = _doc_paths(doc)
    real = {p for _, p in _operations(openapi)}
    unknown = sorted(p for p in absolute if p not in real and p != "/api/health")
    assert not unknown, f"API.md documents paths the server does not have: {unknown}"


# Response models the web renders: every field must be named in API.md, so a new or renamed field cannot
# silently drift from the contract (R-13, R-14 were exactly this kind of drift).
DOCUMENTED_SCHEMAS = [
    "AnalysisOut",
    "ArtifactOut",
    "CommandResult",
    "RunAllResult",
    "SessionInfo",
    "TreeNodeOut",
    "OrchestrationOut",
    "GenerateSqlOut",
    "RepairAttemptOut",
    "AISettingsOut",
    "RunOut",
    "QualitySummary",
    "TileData",
    "SignupRequest",
    "UpdateMeRequest",
    "AssumptionOut",
    "InviteOut",
    "InviteCreated",
    "InvitePreview",
    "InviteAccept",
    "InviteCreate",
]


@pytest.mark.parametrize("schema", DOCUMENTED_SCHEMAS)
def test_response_fields_are_documented(openapi: dict[str, Any], doc: str, schema: str) -> None:
    schemas = openapi["components"]["schemas"]
    assert schema in schemas, f"{schema} is not in the OpenAPI document"
    props = schemas[schema].get("properties", {})
    undocumented = [f for f in props if not re.search(rf"(?<![A-Za-z_]){re.escape(f)}(?![A-Za-z_])", doc)]
    assert not undocumented, f"{schema} fields missing from API.md: {undocumented}"


def test_shapes_the_web_relies_on(openapi: dict[str, Any]) -> None:
    s = openapi["components"]["schemas"]
    assert {"notebook", "executed", "stopped_at"} <= set(s["RunAllResult"]["properties"])
    assert s["CommandResult"]["properties"]["action"].get("type") == "string"
    assert {"investigation_id", "finding_id", "report_id", "dashboard_id"} <= set(
        s["CommandResult"]["properties"]
    )
    assert "data" in s["ArtifactOut"]["properties"]
    assert "finding_id" in s["TreeNodeOut"]["properties"]
    ops = {
        (m.upper(), _norm(p)): item[m] for p, item in openapi["paths"].items() for m in item if m in METHODS
    }
    promote = ops[("POST", "/workspaces/{}/investigations/{}/nodes/{}/finding")]
    assert {"200", "201"} <= set(promote["responses"])
    assert ("GET", "/workspaces/{}/dashboards/{}/tiles/{}/lineage") in ops
    for kind in ("forecast", "anomalies", "segments", "stats-test", "correlation", "regression"):
        op = ops[("POST", f"/workspaces/{{}}/analysis/{kind}")]
        assert op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/AnalysisOut"
        )
        assert op.get("x-read-only-post") is True
