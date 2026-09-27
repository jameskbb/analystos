"""Acceptance flows 1 and 2 (spec §96, §97) end to end through the REST API.

The app runs in-process (FastAPI TestClient) on a temporary SQLite metadata DB and data
directory; jobs execute inline. The demo dataset is the cached, hash-verified seed-42 copy.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

API = "/api/v1"


def _route_exists(app: Any, path: str) -> bool:
    return path in app.openapi().get("paths", {})


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory, demo: Any) -> Iterator[Any]:
    import fastapi.testclient as fastapi_testclient
    from analystos_api.config import Settings
    from analystos_api.main import create_app

    tmp = tmp_path_factory.mktemp("acceptance")
    data_dir = tmp / "data"
    # Reuse the verified demo cache instead of regenerating it (bootstrap_paths re-verifies hashes).
    (data_dir / "demo").mkdir(parents=True)
    (data_dir / "demo" / "summit-supply-seed42").symlink_to(
        Path(demo.data_dir).resolve(), target_is_directory=True
    )
    settings = Settings(
        DATABASE_URL=f"sqlite:///{tmp}/meta.db",
        DATA_DIR=data_dir,
        AOS_SECRET_KEY="acceptance-secret-" + "x" * 40,
        AOS_ENV="development",  # local (password-less) mode exists only in development
        AOS_LOG_JSON=False,
        AOS_LOG_LEVEL="WARNING",
        AOS_JOB_EXECUTION="inline",
        AUTH_MODE="local",
    )
    app = create_app(settings)
    assert _route_exists(app, f"{API}/demo/load"), "the API must expose /demo/load"

    class Client(fastapi_testclient.TestClient):
        def request(self, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
                headers = dict(kwargs.pop("headers", None) or {})
                if self.cookies.get("aos_csrf"):
                    headers.setdefault("X-CSRF-Token", self.cookies.get("aos_csrf"))
                kwargs["headers"] = headers
            return super().request(method, url, *args, **kwargs)

    # A local analyst on this machine: loopback peer and localhost Host (local mode refuses others).
    with Client(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        r = client.get(f"{API}/auth/session")
        assert r.status_code == 200 and r.json()["authenticated"], r.text
        yield client


def ok(r: Any, *codes: int) -> Any:
    assert r.status_code in (codes or (200,)), (
        f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text[:500]}"
    )
    return r.json()


def wait_job(client: Any, ws: str, accepted: dict[str, Any], timeout_s: float = 120) -> dict[str, Any]:
    job = accepted["job"]
    deadline = time.monotonic() + timeout_s
    while job["status"] not in ("succeeded", "failed"):
        assert time.monotonic() < deadline, f"job {job['id']} did not finish"
        time.sleep(0.2)
        job = ok(client.get(f"{API}/workspaces/{ws}/jobs/{job['id']}"))
    assert job["status"] == "succeeded", job.get("error")
    return job


@pytest.fixture(scope="module")
def ws(api: Any) -> str:
    accepted = ok(api.post(f"{API}/demo/load", json={}), 202)
    ws_id = accepted["workspace_id"]
    wait_job(api, ws_id, accepted, timeout_s=300)
    return ws_id


def run_question(api: Any, ws: str, question: str, choices: dict[str, str] | None = None) -> dict[str, Any]:
    inv = ok(api.post(f"{API}/workspaces/{ws}/investigations", json={"question": question}), 201)
    if inv["status"] == "needs_disambiguation":
        assert choices, f"unexpected ambiguity: {inv['interpretation'].get('ambiguous')}"
        inv = ok(
            api.post(
                f"{API}/workspaces/{ws}/investigations/{inv['id']}/disambiguate", json={"choices": choices}
            )
        )
    if inv["status"] in ("awaiting_approval", "ready"):
        accepted = ok(api.post(f"{API}/workspaces/{ws}/investigations/{inv['id']}/run", json={}), 202)
        wait_job(api, ws, accepted)
        inv = ok(api.get(f"{API}/workspaces/{ws}/investigations/{inv['id']}"))
    assert inv["status"] == "completed", inv.get("failures") or inv.get("error")
    return inv


def nodes(inv: dict[str, Any]) -> list[dict[str, Any]]:
    return inv["tree"]["nodes"]


def find_segment(inv: dict[str, Any], dimension: str, value: str) -> dict[str, Any] | None:
    for n in nodes(inv):
        seg = n.get("segment") or {}
        if n["kind"] == "segment" and seg.get("dimension") == dimension and seg.get("value") == value:
            return n
    return None


def test_flow1_data_schema_relationships_metrics(api: Any, ws: str, scenarios: dict[str, Any]) -> None:
    """Load demo -> open workspace -> inspect schema -> relationships -> semantic metrics."""
    workspaces = ok(api.get(f"{API}/workspaces"))
    assert any(w["id"] == ws and w["name"] == "Summit Supply Co." for w in workspaces)
    datasets = ok(api.get(f"{API}/workspaces/{ws}/datasets"))
    by_table = {d["table_name"]: d for d in datasets}
    for t in ("orders", "order_lines", "customers", "products", "branches", "budgets_branch", "leads"):
        assert t in by_table, f"dataset {t} missing"
    assert by_table["orders"]["row_count"] > 100_000
    assert {c["name"] for c in by_table["orders"]["columns"]} >= {
        "order_id",
        "order_date",
        "status",
        "branch_id",
    }
    rels = ok(api.get(f"{API}/workspaces/{ws}/relationships"))
    listed = {(r["from_table"], r["from_col"], r["to_table"]) for r in rels}
    assert ("order_lines", "order_id", "orders") in listed, "relationships are shown for review"
    assert ("orders", "customer_id", "customers") in listed
    current = ok(api.get(f"{API}/workspaces/{ws}/semantic-models/current"))
    model_rels = {
        (r["from_entity"], r["to_entity"]) for r in current["model"]["relationships"] if r["approved"]
    }
    assert ("order_lines", "orders") in model_rels and ("orders", "customers") in model_rels
    metric_ids = {m["id"] for m in current["model"]["metrics"]}
    assert {
        "revenue",
        "orders",
        "aov",
        "gross_margin_pct",
        "contribution_margin",
        "operating_margin",
    } <= metric_ids
    assert not [i for i in current["issues"] if i["severity"] == "error"]


def test_flow1_august_investigation_drill_finding_report(
    api: Any, ws: str, scenarios: dict[str, Any]
) -> None:
    """Ask -> plan -> real SQL -> tree -> drivers -> SQL exposed -> drill Dallas -> finding -> report -> rerun."""
    key = scenarios["stories"]["revenue_decline_aug_2026"]
    inv = run_question(api, ws, "Why was August revenue down?")
    assert inv["plan"] and inv["plan"]["steps"], "an analysis plan is shown"
    root = next(n for n in nodes(inv) if n["id"] == inv["tree"]["root_id"])
    assert root["metric_id"] == "revenue"
    assert root["pct_change"] == pytest.approx(key["headline"]["pct_change"], abs=0.003)
    assert root["current"] == pytest.approx(key["headline"]["current"], abs=1.0)
    assert inv["brief_answer"] and "11.8%" in inv["brief_answer"]

    dallas = find_segment(inv, "branch", "Dallas")
    assert dallas is not None and dallas["contribution_to_parent"]["share"] >= 0.35

    artifacts = ok(api.get(f"{API}/workspaces/{ws}/investigations/{inv['id']}/artifacts"))
    with_sql = [a for a in artifacts if a.get("sql")]
    assert with_sql, "every calculation exposes its SQL"
    assert any("cancelled" in a["sql"] for a in with_sql)
    assert any(a.get("chart_spec") for a in artifacts), "findings come with charts"

    # Drill into Dallas by customer: the major customer's cut must surface.
    revenue_dallas = next(
        n
        for n in nodes(inv)
        if n["kind"] == "segment"
        and n["metric_id"] == "revenue"
        and (n.get("segment") or {}).get("value") == "Dallas"
    )
    drilled = ok(
        api.post(
            f"{API}/workspaces/{ws}/investigations/{inv['id']}/nodes/{revenue_dallas['id']}/drill",
            json={"dimension": "customer"},
        )
    )
    major = find_segment(drilled, "customer", key["major_customer"]["customer_name"])
    assert major is not None and major["abs_change"] < 0

    finding = ok(
        api.post(
            f"{API}/workspaces/{ws}/investigations/{inv['id']}/nodes/{revenue_dallas['id']}/finding", json={}
        ),
        201,
    )
    assert finding["artifact_ids"] and finding["evidence_strength"] != "hypothesis_only"
    ok(api.post(f"{API}/workspaces/{ws}/findings/{finding['id']}/status", json={"status": "confirmed"}))
    lineage = ok(api.get(f"{API}/workspaces/{ws}/findings/{finding['id']}/lineage"))
    kinds = {n["kind"] for n in lineage["nodes"]}
    assert {"finding", "query", "metric"} <= kinds

    report = ok(
        api.post(f"{API}/workspaces/{ws}/reports/from-investigation", json={"investigation_id": inv["id"]}),
        200,
        201,
    )
    assert report["blocks"], "report generated from the investigation"

    # Reproducible: rerun on unchanged data yields no node changes.
    accepted = ok(api.post(f"{API}/workspaces/{ws}/investigations/{inv['id']}/rerun", json={}), 202)
    wait_job(api, ws, accepted)
    diff = ok(api.get(f"{API}/workspaces/{ws}/investigations/{inv['id']}/diff"))
    assert diff["to_run_no"] == diff["from_run_no"] + 1
    assert not diff["diff"]["node_changes"]
    assert not diff["diff"]["dataset_version_changes"]
    assert not diff["diff"]["metric_version_changes"]


def test_flow2_margin_with_flat_revenue(api: Any, ws: str, scenarios: dict[str, Any]) -> None:
    """Spec §97, literal: "Revenue was roughly flat. Why did margin decline?"

    'margin' is ambiguous; with Gross Margin % chosen, the period is picked where revenue is
    flat (the question names none), the premise is checked, and the decline is split into
    unit cost, discounting and mix, each against the answer key. Facts and hypotheses stay
    distinct and no circular "Gross Margin explains GM%" driver is offered.
    """
    key = scenarios["stories"]["margin_compression_q2_2026"]
    q = "Revenue was roughly flat. Why did margin decline?"
    inv = ok(api.post(f"{API}/workspaces/{ws}/investigations", json={"question": q}), 201)
    assert inv["status"] == "needs_disambiguation"
    terms = {
        a["term"].lower(): {c["metric_id"] for c in a["candidates"]}
        for a in inv["interpretation"]["ambiguous"]
    }
    assert {"gross_margin", "gross_margin_pct", "contribution_margin", "operating_margin"} <= terms["margin"]
    inv = run_question(api, ws, q, choices={"margin": "gross_margin_pct"})
    interp = inv["interpretation"]
    assert interp["window"]["start"] == key["current_period"]["start"]
    assert interp["baseline"]["start"] == key["baseline_period"]["start"]
    tree = nodes(inv)
    root = next(n for n in tree if n["id"] == inv["tree"]["root_id"])
    assert root["metric_id"] == "gross_margin_pct" and root["current"] < root["baseline"]
    premise = next(n for n in tree if (n.get("step_id") or "").startswith("premise:"))
    assert "premise holds" in premise["notes"], premise["statement"]
    first_level = [n for n in tree if n["parent_id"] == root["id"]]
    effects = {
        n["metric_label"]: n["contribution_to_parent"]["effect"] * 100
        for n in first_level
        if (n.get("step_id") or "").startswith("margin_bridge")
    }
    want = key["attribution_pp"]
    assert abs(effects["Unit cost"] - want["unit_cost_inflation"]) <= 0.3, effects
    assert abs(effects["Discounting"] - want["discounting"]) <= 0.3, effects
    assert not [n for n in first_level if n["kind"] == "driver" and n["metric_id"] == "gross_margin"]
    lumber = [
        n
        for n in tree
        if (n.get("segment") or {}).get("value") == "Lumber" and n["metric_id"] == "cogs" and n["rank"] == 1
    ]
    assert lumber, "unit-cost inflation is located in Lumber"
    types = {n["statement_type"] for n in tree}
    assert "hypothesis" in types and "supported_explanation" in types, (
        "facts and hypotheses are distinguished"
    )


def test_rerun_keeps_analyst_decisions(api: Any, ws: str) -> None:
    """R-12: reject + annotate a node, rerun, and the decision and note are still there."""
    inv = run_question(api, ws, "Which customers contributed most to the August revenue decline?")
    target = next(n for n in nodes(inv) if n["kind"] == "segment")
    ok(
        api.post(
            f"{API}/workspaces/{ws}/investigations/{inv['id']}/nodes/{target['id']}/actions",
            json={"action": "reject", "note": "not convinced"},
        )
    )
    accepted = ok(api.post(f"{API}/workspaces/{ws}/investigations/{inv['id']}/rerun", json={}), 202)
    wait_job(api, ws, accepted)
    after = ok(api.get(f"{API}/workspaces/{ws}/investigations/{inv['id']}"))
    node = next(n for n in nodes(after) if n["id"] == target["id"])
    assert node["status"] == "rejected"
    assert any(
        "not convinced" in (a if isinstance(a, str) else a.get("text", "")) for a in node["annotations"]
    )
