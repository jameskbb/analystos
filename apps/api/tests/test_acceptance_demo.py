"""Acceptance flow 1 (spec section 96) end-to-end over HTTP, plus the features that hang off it.

load demo -> schema -> relationships -> metrics -> ask "Why was August revenue down?" -> plan -> run ->
tree includes Dallas and the major customer -> drill -> save finding -> report -> rerun reproduces
identical numbers.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from analystos_api.main import create_app
from conftest import ApiClient, make_settings

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    tmp = tmp_path_factory.mktemp("demo")
    app = create_app(make_settings(Path(tmp)))
    with ApiClient(app) as c:
        assert c.get("/api/v1/auth/session").json()["authenticated"]
        r = c.post("/api/v1/demo/load", json={})
        assert r.status_code == 202, r.text
        ws = r.json()["workspace_id"]
        job = c.get(r.json()["poll_url"]).json()
        assert job["status"] == "succeeded", job
        yield {"client": c, "ws": ws, "base": f"/api/v1/workspaces/{ws}", "app": app, "load": job["result"]}


@pytest.fixture(scope="module")
def investigation(demo: dict[str, Any]) -> dict[str, Any]:
    c, base = demo["client"], demo["base"]
    r = c.post(f"{base}/investigations", json={"question": "Why was August revenue down?"})
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["status"] == "awaiting_approval"  # expensive plan: shown before execution (spec section 53)
    assert inv["plan"]["steps"] and inv["interpretation"]["metric_ids"] == ["revenue"]
    r = c.post(f"{base}/investigations/{inv['id']}/run")
    assert r.status_code == 202
    job = c.get(r.json()["poll_url"]).json()
    assert job["status"] == "succeeded", job
    return c.get(f"{base}/investigations/{inv['id']}").json()


def _nodes(inv: dict[str, Any]) -> list[dict[str, Any]]:
    return inv["tree"]["nodes"]


def _find(inv: dict[str, Any], dimension: str, value: str, parent_dim: str | None = None) -> dict[str, Any]:
    by_id = {n["id"]: n for n in _nodes(inv)}
    for n in _nodes(inv):
        seg = n.get("segment") or {}
        if (
            n["kind"] == "segment"
            and seg.get("dimension") == dimension
            and seg.get("value") == value
            and (parent_dim is None or any(s["dimension"] == parent_dim for s in n["segment_path"][:-1]))
        ):
            return n
    raise AssertionError(f"no node {dimension}={value}; have {[n['statement'] for n in by_id.values()][:20]}")


def test_demo_bootstrap(demo: dict[str, Any]) -> None:
    c, base = demo["client"], demo["base"]
    load = demo["load"]
    assert load["tables"] >= 15 and load["semantic"]["metrics_created"] >= 20
    schema = c.get(f"{base}/queries/schema").json()
    tables = {t["table"] for t in schema["tables"]}
    assert {"orders", "order_lines", "customers", "products", "branches"} <= tables
    datasets = c.get(f"{base}/datasets").json()
    assert all(d["profile_status"] == "ready" for d in datasets)
    assert any(d["issue_count"] > 0 for d in datasets)  # messy demo data is detected
    rels = c.get(f"{base}/relationships").json()
    assert any(r["status"] == "approved" for r in rels) and any(r["status"] == "suggested" for r in rels)
    # R-48: every approved join is measured at load, and mismatches with the declared cardinality are flagged.
    approved = [r for r in rels if r["status"] == "approved"]
    assert all(r["join_analysis"] and r["join_analysis"].get("observed_cardinality") for r in approved)
    assert load["joins_analyzed"] == len(approved) and load["failures"] == []
    for r in approved:
        ja = r["join_analysis"]
        assert ja["cardinality_mismatch"] == (ja["observed_cardinality"] != ja["declared_cardinality"])
    metrics = {m["id"]: m for m in c.get(f"{base}/metrics").json()}
    assert {"revenue", "orders", "aov"} <= set(metrics)
    assert metrics["revenue"]["version_no"] == 1 and metrics["revenue"]["kind"] == "simple"
    q = c.get(f"{base}/quality/summary").json()
    assert q["active"] > 0 and q["failing"] > 0  # some demo rules fail on purpose
    settings = c.get(f"{base}/settings").json()
    assert settings["investigation"]["reference_date"] == "2026-09-30"


def test_demo_load_is_idempotent(demo: dict[str, Any]) -> None:
    c = demo["client"]
    r = c.post("/api/v1/demo/load", json={})
    assert r.json()["workspace_id"] == demo["ws"]
    job = c.get(r.json()["poll_url"]).json()
    assert job["status"] == "succeeded"
    assert (
        job["result"]["semantic"]["metrics_created"] == 0
        and job["result"]["semantic"]["metrics_versioned"] == 0
    )
    ds = c.get(f"{demo['base']}/datasets").json()
    orders = next(d for d in ds if d["table_name"] == "orders")
    versions = c.get(f"{demo['base']}/datasets/{orders['id']}/versions").json()
    assert len(versions) == 1  # identical content: no new dataset version
    status = c.get("/api/v1/demo/status").json()
    assert status["available"] and [w["id"] for w in status["workspaces"]] == [demo["ws"]]


def test_acceptance_flow_tree(investigation: dict[str, Any]) -> None:
    inv = investigation
    assert inv["status"] == "completed"
    root = next(n for n in _nodes(inv) if n["id"] == inv["tree"]["root_id"])
    assert root["metric_id"] == "revenue"
    assert root["pct_change"] == pytest.approx(-0.118, abs=0.003)
    dallas = _find(inv, "branch", "Dallas")
    assert dallas["abs_change"] < 0 and dallas["evidence_strength"] in {"strong", "moderate"}
    customers = [
        n
        for n in _nodes(inv)
        if n["kind"] == "segment"
        and (n.get("segment") or {}).get("dimension") == "customer"
        and n["abs_change"] is not None
    ]
    top = min(customers, key=lambda n: n["abs_change"])
    assert top["segment"]["value"] == "Trinity Ridge Construction"
    assert "11.8%" in inv["brief_answer"]
    assert inv["metric_version_ids"]["revenue"]["version_no"] == 1
    assert inv["dataset_versions"]["order_lines"]["version_no"] == 1
    assert inv["semantic_snapshot_id"]


def test_contribution_calculation_and_engine_ids(demo: dict[str, Any], investigation: dict[str, Any]) -> None:
    """R-11: the calculation behind a contribution is returned; R-29: tree artifact ids resolve."""
    c, base = demo["client"], demo["base"]
    dallas = _find(investigation, "branch", "Dallas")
    parent = next(n for n in _nodes(investigation) if n["id"] == dallas["parent_id"])
    ids = list(dict.fromkeys([*dallas["artifact_ids"], *parent["artifact_ids"]]))
    arts = [c.get(f"{base}/artifacts/{eid}") for eid in ids]
    assert all(a.status_code == 200 for a in arts), [a.text for a in arts]
    calc = next(a.json() for a in arts if a.json()["data"] and a.json()["data"].get("rows"))
    data = calc["data"]
    assert data["method"] and "additive_valid" in data and data["total_change"] is not None
    dallas_row = next(r for r in data["rows"] if str(r.get("segment")) == "Dallas")
    assert dallas_row["effect"] == pytest.approx(dallas["contribution_to_parent"]["effect"], rel=1e-6)
    assert calc["engine_id"] in ids
    lineage = c.get(f"{base}/artifacts/{calc['engine_id']}/lineage")
    assert lineage.status_code == 200
    mid, version = next(iter(calc["metric_versions"].items()))
    metric_nodes = [n for n in lineage.json()["nodes"] if n["kind"] == "metric" and n["ref_id"] == mid]
    assert metric_nodes and metric_nodes[0]["meta"]["version_no"] == 1
    assert metric_nodes[0]["meta"]["engine_version_id"] == version


def test_every_node_is_backed_by_executed_sql(demo: dict[str, Any], investigation: dict[str, Any]) -> None:
    c, base = demo["client"], demo["base"]
    arts = c.get(f"{base}/investigations/{investigation['id']}/artifacts").json()
    by_engine = {a["engine_id"]: a for a in arts}
    assert any(a["sql"] for a in arts)
    for n in _nodes(investigation):
        if n["status"] == "failed" or n["kind"] == "hypothesis":
            continue
        assert n["artifact_ids"], n["statement"]
        assert all(a in by_engine for a in n["artifact_ids"])
    art = next(a for a in arts if a["sql"] and a["filter_context"])
    assert any(fc["kind"] == "time" for fc in art["filter_context"])
    rerun = c.post(f"{base}/artifacts/{art['id']}/rerun").json()
    assert rerun["identical"] is True
    lineage = c.get(f"{base}/artifacts/{art['id']}/lineage").json()
    kinds = {n["kind"] for n in lineage["nodes"]}
    assert {"metric", "entity", "dataset"} <= kinds


def test_drill_finding_report_rerun(demo: dict[str, Any], investigation: dict[str, Any]) -> None:
    c, base, iid = demo["client"], demo["base"], investigation["id"]
    dallas = _find(investigation, "branch", "Dallas")
    r = c.post(f"{base}/investigations/{iid}/nodes/{dallas['id']}/drill", json={"dimension": "category"})
    assert r.status_code == 200, r.text
    drilled = r.json()
    kids = [n for n in _nodes(drilled) if n["parent_id"] == dallas["id"] and n.get("dimension") == "category"]
    assert kids, "drill must add a product-category breakdown under Dallas"
    # Confirm the Dallas node, then promote it to a finding.
    r = c.post(
        f"{base}/investigations/{iid}/nodes/{dallas['id']}/actions",
        json={"action": "confirm", "note": "Dallas volume collapse verified"},
    )
    assert r.status_code == 200
    r = c.post(f"{base}/investigations/{iid}/nodes/{dallas['id']}/finding", json={})
    assert r.status_code == 201, r.text
    finding = r.json()
    assert finding["status"] == "confirmed" and finding["statement_type"] in {
        "supported_explanation",
        "observation",
    }
    assert finding["artifact_ids"] and finding["metric_version_ids"]["revenue"]["version_no"] == 1
    assert any(fc["kind"] == "segment" and fc["value"] == "Dallas" for fc in finding["filter_context"])
    lineage = c.get(f"{base}/findings/{finding['id']}/lineage").json()
    kinds = {n["kind"] for n in lineage["nodes"]}
    assert {"finding", "metric", "entity", "dataset"} <= kinds
    inv = c.get(f"{base}/investigations/{iid}").json()
    assert inv["node_findings"][dallas["id"]] == finding["id"]
    # R-37: the tree node carries its finding; saving it again returns the same finding with 200.
    assert next(n for n in _nodes(inv) if n["id"] == dallas["id"])["finding_id"] == finding["id"]
    again = c.post(f"{base}/investigations/{iid}/nodes/{dallas['id']}/finding", json={})
    assert again.status_code == 200 and again.json()["id"] == finding["id"]
    # R-31: the revenue definition's filter is a visible chip on the finding and its artifacts.
    assert any(fc["kind"] == "metric" and "status" in fc["value"] for fc in finding["filter_context"])
    summary = c.get(f"{base}/investigations/{iid}/summary").json()["summary"]
    assert any("Dallas" in i["text"] for i in summary["supported_explanations"] + summary["observations"])
    # Report from the investigation.
    rep = c.post(f"{base}/reports/from-investigation", json={"investigation_id": iid}).json()
    types = [b["type"] for b in rep["blocks"]]
    charts = [b for b in rep["blocks"] if b["type"] == "chart"]
    assert len({b["artifact_id"] for b in charts}) == len(charts)  # R-47: no duplicate chart blocks
    assert all(b["provenance"]["sql"] for b in charts)  # chart provenance resolves to SQL
    brief = next(b for b in rep["blocks"] if b["type"] == "narrative")
    assert brief["label"] == "Unconfirmed analysis" and brief["markdown"].startswith(
        "**Unconfirmed analysis.**"
    )
    assert {"narrative", "summary", "kpi", "finding", "methodology", "sources"} <= set(types)
    md = c.post(f"{base}/exports", json={"target": "report", "id": rep["id"], "format": "md"})
    assert md.status_code == 200 and "Dallas" in md.text and "attachment" in md.headers["content-disposition"]
    html = c.post(f"{base}/exports", json={"target": "report", "id": rep["id"], "format": "html"})
    assert html.status_code == 200 and "<table>" in html.text and "@page" in html.text
    # Rerun reproduces identical numbers.
    before = {n["id"]: n for n in _nodes(c.get(f"{base}/investigations/{iid}").json())}
    r = c.post(f"{base}/investigations/{iid}/rerun", json={})
    assert r.status_code == 202
    job = c.get(r.json()["poll_url"]).json()
    assert job["status"] == "succeeded" and job["result"]["has_changes"] is False, job
    after = {n["id"]: n for n in _nodes(c.get(f"{base}/investigations/{iid}").json())}
    assert set(before) == set(after)
    for nid, n in before.items():
        assert after[nid]["current"] == n["current"] and after[nid]["abs_change"] == n["abs_change"]
    diff = c.get(f"{base}/investigations/{iid}/diff").json()
    assert diff["to_run_no"] == 2 and diff["diff"]["node_changes"] == []
    assert diff["diff"]["dataset_version_changes"] == [] and diff["diff"]["metric_version_changes"] == []
    runs = c.get(f"{base}/investigations/{iid}/runs").json()
    assert [r["run_no"] for r in runs] == [2, 1] and runs[0]["kind"] == "rerun"


def test_metric_edit_is_versioned_and_detected_by_rerun(demo: dict[str, Any]) -> None:
    c, base = demo["client"], demo["base"]
    r = c.post(f"{base}/investigations", json={"question": "Why was August units down?", "auto_run": True})
    inv = r.json()
    if inv["status"] == "awaiting_approval":
        c.post(f"{base}/investigations/{inv['id']}/run")
    inv = c.get(f"{base}/investigations/{inv['id']}").json()
    assert inv["status"] == "completed", inv.get("failures")
    old = c.get(f"{base}/metrics/units").json()
    r = c.patch(
        f"{base}/metrics/units", json={"description": "Units shipped (clarified)", "change_note": "docs"}
    )
    assert r.status_code == 200 and r.json()["version_no"] == old["version_no"] + 1
    versions = c.get(f"{base}/metrics/units/versions").json()
    assert [v["version_no"] for v in versions][:2] == [old["version_no"] + 1, old["version_no"]]
    assert versions[1]["definition"]["description"] == old["description"]  # old version is immutable
    job = c.get(c.post(f"{base}/investigations/{inv['id']}/rerun", json={}).json()["poll_url"]).json()
    assert job["status"] == "succeeded"
    diff = c.get(f"{base}/investigations/{inv['id']}/diff").json()["diff"]
    # description is not part of the definition hash: numbers and definitions are unchanged
    assert diff["node_changes"] == []
    used = c.get(f"{base}/metrics/units/investigations").json()
    assert any(u["id"] == inv["id"] for u in used)


def test_ambiguous_margin_requires_a_choice(demo: dict[str, Any]) -> None:
    c, base = demo["client"], demo["base"]
    inv = c.post(f"{base}/investigations", json={"question": "Why did margin decline in Q2 2026?"}).json()
    assert inv["status"] == "needs_disambiguation"
    amb = inv["interpretation"]["ambiguous"][0]
    candidates = [cand["metric_id"] for cand in amb["candidates"]]
    assert len(candidates) >= 2
    r = c.post(f"{base}/investigations/{inv['id']}/run")
    assert r.status_code == 409 and r.json()["code"] == "needs_disambiguation"
    r = c.post(
        f"{base}/investigations/{inv['id']}/disambiguate", json={"choices": {amb["term"]: "gross_margin_pct"}}
    )
    assert r.status_code == 200, r.text
    assert r.json()["interpretation"]["metric_ids"][0] == "gross_margin_pct"
    assert r.json()["status"] in {"awaiting_approval", "completed"}


def test_plan_editing(demo: dict[str, Any]) -> None:
    c, base = demo["client"], demo["base"]
    inv = c.post(
        f"{base}/investigations", json={"question": "Why was August revenue down?", "auto_run": False}
    ).json()
    assert inv["status"] == "awaiting_approval"
    plan = inv["plan"]
    disabled = plan["steps"][-1]["id"]
    plan["steps"][-1]["enabled"] = False
    r = c.put(f"{base}/investigations/{inv['id']}/plan", json=plan)
    assert r.status_code == 200, r.text
    step = next(s for s in r.json()["plan"]["steps"] if s["id"] == disabled)
    assert step["enabled"] is False


def test_commands(demo: dict[str, Any], investigation: dict[str, Any]) -> None:
    c, base, iid = demo["client"], demo["base"], investigation["id"]
    r = c.post(f"{base}/investigations/{iid}/command", json={"text": "Show the SQL"})
    assert r.status_code == 200 and r.json()["action"] == "show_sql" and r.json()["sql"]
    r = c.post(f"{base}/investigations/{iid}/command", json={"text": "Break this down by channel"})
    body = r.json()
    assert body["action"] in {"drilled", "clarify"}, body
    r = c.post(f"{base}/investigations/{iid}/command", json={"text": "Turn this into a dashboard"})
    assert r.json()["action"] == "built_dashboard"
    dash_id = r.json()["dashboard_id"]
    data = c.post(f"{base}/dashboards/{dash_id}/data", json={}).json()
    kpi = next(t for t in data if t["kind"] == "kpi")
    assert kpi["error"] is None and kpi["kpi"]["value"] == pytest.approx(
        investigation["tree"]["nodes"][0]["current"]
    )
    assert kpi["kpi"]["pct_change"] == pytest.approx(-0.118, abs=0.003)
    assert kpi["provenance"]["metric_versions"]["revenue"]["version_no"] >= 1
    r = c.post(f"{base}/investigations/{iid}/command", json={"text": "gibberish flurble"})
    assert r.json()["action"] == "clarify"
    r = c.post(f"{base}/investigations/command", json={"text": "Why did orders fall in August?"})
    assert r.json()["action"] == "created_investigation"


def test_business_review_and_exec_summary(demo: dict[str, Any]) -> None:
    c, base = demo["client"], demo["base"]
    r = c.post(f"{base}/reports/business-review", json={"period": "2026-08"})
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["kind"] == "business_review" and rep["status"] == "draft"
    kpis = next(b for b in rep["blocks"] if b["type"] == "kpi_overview")["items"]
    revenue = next(k for k in kpis if k["metric_id"] == "revenue")
    assert revenue["pct_change"] == pytest.approx(-0.118, abs=0.003)
    types = {b["type"] for b in rep["blocks"]}
    assert {
        "kpi_overview",
        "table",
        "anomalies",
        "summary",
        "open_questions",
        "methodology",
        "sources",
    } <= types
    # Materiality order (R-26): revenue leads the narrative and the KPI list, not a ratio's relative swing.
    assert kpis[0]["metric_id"] == "revenue"
    narrative = next(b for b in rep["blocks"] if b["type"] == "narrative")["markdown"]
    assert narrative.split("\n- ")[1].startswith("Revenue")
    drivers = [
        b
        for b in rep["blocks"]
        if b["type"] == "table" and b.get("title", "").startswith("Drivers of Revenue by")
    ]
    assert len(drivers) >= 2 and len({b["dimension"] for b in drivers}) == len(
        drivers
    )  # one table per dimension
    assert all(b["sql"] and b["result"]["sql"] for b in drivers)
    changes = next(b for b in rep["blocks"] if b.get("title") == "Largest changes")
    assert changes["sql"] and changes["queries"]
    # Publishing needs review (R-15): draft -> 409, unreviewed blocks -> 409 with ids, reviewed -> 200.
    r = c.post(f"{base}/reports/{rep['id']}/publish")
    assert r.status_code == 409 and r.json()["code"] == "not_in_review"
    assert c.post(f"{base}/reports/{rep['id']}/submit").json()["status"] == "in_review"
    r = c.post(f"{base}/reports/{rep['id']}/publish")
    assert r.status_code == 409 and r.json()["code"] == "unreviewed_blocks"
    assert set(r.json()["errors"]["block_ids"]) == {b["id"] for b in rep["blocks"]}
    blocks = [{**b, "reviewed": True} for b in rep["blocks"]]
    excluded = next(b for b in blocks if b["type"] == "methodology")
    excluded.update(reviewed=False, excluded=True, markdown="EXCLUDED-BLOCK-TEXT")
    blocks[1] = {**blocks[1], "reviewed": False}
    c.patch(f"{base}/reports/{rep['id']}", json={"blocks": blocks})
    r = c.post(f"{base}/reports/{rep['id']}/publish")
    assert r.status_code == 409 and r.json()["errors"]["block_ids"] == [blocks[1]["id"]]
    blocks[1]["reviewed"] = True
    c.patch(f"{base}/reports/{rep['id']}", json={"blocks": blocks})
    assert c.post(f"{base}/reports/{rep['id']}/publish").json()["status"] == "published"
    assert c.patch(f"{base}/reports/{rep['id']}", json={"title": "x"}).status_code == 409
    # Excluded blocks never reach an export (R-32).
    for fmt in ("md", "html"):
        out = c.post(f"{base}/exports", json={"target": "report", "id": rep["id"], "format": fmt})
        assert out.status_code == 200 and "EXCLUDED-BLOCK-TEXT" not in out.text
    es = c.post(f"{base}/reports/executive-summary", json={}).json()
    assert any(b["type"] == "summary" for b in es["blocks"])
    pdf = c.post(f"{base}/exports", json={"target": "report", "id": rep["id"], "format": "pdf"})
    assert pdf.status_code in {200, 409}
    if pdf.status_code == 409:
        assert pdf.json()["code"] == "pdf_unavailable" and pdf.json()["errors"]["fallback"] == "html"
    x = c.post(f"{base}/exports", json={"target": "report", "id": rep["id"], "format": "xlsx"})
    assert x.status_code == 200 and x.content[:2] == b"PK"


def test_home_search_and_explore(demo: dict[str, Any]) -> None:
    c, base = demo["client"], demo["base"]
    home = c.get(f"{base}/home").json()
    assert home["counts"]["datasets"] >= 15 and home["quality_failures"]
    assert any(ch["metric_id"] == "revenue" for ch in home["changes"])
    hits = c.get(f"{base}/search", params={"q": "revenue"}).json()
    assert hits[0]["kind"] == "metric" and hits[0]["ref_id"] == "revenue"
    hits = c.get(f"{base}/search", params={"q": "customer_id", "kinds": "column"}).json()
    assert hits and all(h["kind"] == "column" for h in hits)
    res = c.post(
        f"{base}/explore/metrics",
        json={
            "metrics": ["revenue", "aov"],
            "dimensions": ["branch"],
            "time": {"start": "2026-08-01", "end": "2026-09-01"},
            "order_by": ["-revenue"],
            "limit": 5,
        },
    ).json()
    assert (
        res["result"]["row_count"] == 5 and res["result"]["rows"][0][0] == "Houston" or res["result"]["rows"]
    )
    assert res["metric_versions"]["revenue"]["version_no"] >= 1 and res["charts"]
    assert any(fc["kind"] == "time" for fc in res["filter_context"])
    pivot = c.post(
        f"{base}/explore/pivot",
        json={
            "metric_query": {"time": {"start": "2026-07-01", "end": "2026-09-01"}},
            "rows": ["segment"],
            "columns": ["order_date__month"],
            "values": [{"field": "revenue"}, {"field": "aov"}],
        },
    ).json()
    total = next(r for r in pivot["rows"] if r["is_total"])
    # grand total AOV is re-queried at its own grain, not summed from segment cells
    aov_total = total["cells"][-1]
    seg_aovs = [r["cells"][-1] for r in pivot["rows"] if not r["is_total"]]
    assert aov_total < sum(seg_aovs)
    values = c.get(f"{base}/dimensions/branch/values", params={"q": "dal"}).json()
    assert values["values"] == ["Dallas"]
    ex = c.get(f"{base}/metrics/revenue/examples", params={"grain": "month", "periods": 3}).json()
    assert len(ex["points"]) == 3 and ex["compiled"]["sql"]
    lineage = c.get(f"{base}/metrics/aov/lineage").json()
    assert {"metric:aov", "metric:revenue", "metric:orders"} <= {n["id"] for n in lineage["nodes"]}


def test_notebook_from_investigation(demo: dict[str, Any], investigation: dict[str, Any]) -> None:
    c, base = demo["client"], demo["base"]
    nb = c.post(f"{base}/investigations/{investigation['id']}/notebook").json()
    assert any(cell["kind"] == "sql" for cell in nb["cells"])
    run = c.post(f"{base}/notebooks/{nb['id']}/run").json()
    assert run["stopped_at"] is None, [
        c_["output"] for c_ in run["notebook"]["cells"] if c_["status"] == "error"
    ]
    ipynb = c.get(f"{base}/notebooks/{nb['id']}/ipynb")
    doc = json.loads(ipynb.content)
    assert doc["nbformat"] == 4 and any(cell["cell_type"] == "code" for cell in doc["cells"])


def test_demo_load_survives_one_table_failing(demo: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """R-36: a profiling failure in one table is recorded on that dataset and in the job result; the rest of the
    load (other profiles, relationships, joins, quality rules) still completes."""
    from analystos_api.services import demo as demo_svc

    real = demo_svc.profile_dataset

    def flaky(state: Any, workspace_id: str, dataset_id: str, user_id: str | None) -> dict[str, Any]:
        with state.session_factory() as db:
            from analystos_api.models import Dataset

            table = db.get(Dataset, dataset_id).table_name
        if table == "inventory_snapshots":
            raise TimeoutError("simulated profiling timeout")
        return real(state, workspace_id, dataset_id, user_id)

    monkeypatch.setattr(demo_svc, "profile_dataset", flaky)
    c = demo["client"]
    r = c.post("/api/v1/demo/load", json={})
    job = c.get(r.json()["poll_url"]).json()
    assert job["status"] == "succeeded", job
    failures = job["result"]["failures"]
    assert [(f["step"], f["table"]) for f in failures] == [("profile", "inventory_snapshots")]
    assert job["result"]["joins_analyzed"] > 0 and job["result"]["dq_rules_failing"] >= 0
    datasets = {d["table_name"]: d for d in c.get(f"{demo['base']}/datasets").json()}
    assert datasets["inventory_snapshots"]["profile_status"] == "failed"
    assert all(d["profile_status"] == "ready" for t, d in datasets.items() if t != "inventory_snapshots")


def test_flow2_plan_then_approve_picks_the_flat_revenue_period(demo: dict[str, Any]) -> None:
    """Spec §97 literal question through the plan-first path (auto_run=false): the period search for the stated
    premise ("revenue was roughly flat") runs before planning, as it does on the immediate-run path."""
    from analystos_demo import bootstrap_paths

    c, base = demo["client"], demo["base"]
    cache = demo["app"].state.aos.settings.data_dir / "demo" / "summit-supply-seed42"
    key = json.loads(Path(bootstrap_paths(cache_dir=cache).scenarios_path).read_text())["stories"][
        "margin_compression_q2_2026"
    ]
    q = "Revenue was roughly flat. Why did margin decline?"
    for route in ("create", "disambiguate"):
        body: dict[str, Any] = {"question": q, "auto_run": False}
        if route == "create":
            body["choices"] = {"margin": "gross_margin_pct"}
        inv = c.post(f"{base}/investigations", json=body).json()
        if route == "disambiguate":
            assert inv["status"] == "needs_disambiguation"
            inv = c.post(
                f"{base}/investigations/{inv['id']}/disambiguate",
                json={"choices": {"margin": "gross_margin_pct"}},
            ).json()
        assert inv["status"] == "awaiting_approval", (route, inv.get("failures"))
        interp = inv["interpretation"]
        assert interp["window"]["start"] == key["current_period"]["start"], route
        assert interp["baseline"]["start"] == key["baseline_period"]["start"], route
        assert inv["plan"]["context"]["window"]["start"] == key["current_period"]["start"]
    job = c.get(c.post(f"{base}/investigations/{inv['id']}/run", json={}).json()["poll_url"]).json()
    assert job["status"] == "succeeded", job
    done = c.get(f"{base}/investigations/{inv['id']}").json()
    premise = next(n for n in _nodes(done) if (n.get("step_id") or "").startswith("premise:"))
    assert "premise holds" in premise["notes"], premise["statement"]
