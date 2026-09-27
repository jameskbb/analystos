"""Uploads (limits, sniffing), ingest, profile, versions, preview and lineage."""

from __future__ import annotations

import io
from typing import Any

import pytest
from analystos_api.main import create_app
from conftest import ApiClient, make_settings

CSV = b"order_id,customer,amount,order_date\n1,Acme,100.5,2026-08-01\n2,Beta,20,2026-08-02\n3,Acme,-5,2026-08-03\n"


def upload(client: ApiClient, ws: str, name: str, content: bytes) -> Any:
    return client.post(
        f"/api/v1/workspaces/{ws}/datasets/uploads", files={"file": (name, io.BytesIO(content))}
    )


def ingest(client: ApiClient, ws: str, upload_id: str, **body: Any) -> dict[str, Any]:
    r = client.post(f"/api/v1/workspaces/{ws}/datasets/uploads/{upload_id}/ingest", json=body)
    assert r.status_code == 202, r.text
    job = client.get(r.json()["poll_url"]).json()
    assert job["status"] == "succeeded", job
    return job["result"]


def test_upload_inspect_ingest_profile_preview(local_client: ApiClient, workspace: dict[str, Any]) -> None:
    ws = workspace["id"]
    r = upload(local_client, ws, "orders.csv", CSV)
    assert r.status_code == 201, r.text
    up = r.json()
    assert up["file_kind"] == "csv" and up["inspection"]["tables"][0]["row_count"] == 3
    assert "/" not in up["inspection"]["path"]  # no server paths leak
    res = ingest(local_client, ws, up["id"], dataset_name="Orders")
    ds_id = res["dataset_id"]
    ds = local_client.get(f"/api/v1/workspaces/{ws}/datasets/{ds_id}").json()
    assert ds["table_name"] == "orders" and ds["row_count"] == 3 and ds["profile_status"] == "ready"
    prof = local_client.get(f"/api/v1/workspaces/{ws}/datasets/{ds_id}/profile").json()
    assert prof["row_count"] == 3 and {c["name"] for c in prof["columns"]} >= {"order_id", "amount"}
    prev = local_client.get(
        f"/api/v1/workspaces/{ws}/datasets/{ds_id}/preview",
        params={"order_by": "amount", "direction": "desc", "limit": 2},
    ).json()
    assert prev["row_count"] == 2 and prev["rows"][0][2] == 100.5
    versions = local_client.get(f"/api/v1/workspaces/{ws}/datasets/{ds_id}/versions").json()
    assert len(versions) == 1 and versions[0]["row_count"] == 3
    # Re-ingesting different content creates a new version; identical content does not.
    up2 = upload(local_client, ws, "orders.csv", CSV + b"4,Gamma,7,2026-08-04\n").json()
    ingest(local_client, ws, up2["id"], table_name="orders", if_exists="replace")
    versions = local_client.get(f"/api/v1/workspaces/{ws}/datasets/{ds_id}/versions").json()
    assert [v["version_no"] for v in versions] == [2, 1] and versions[0]["row_count"] == 4
    lineage = local_client.get(f"/api/v1/workspaces/{ws}/datasets/{ds_id}/lineage").json()
    assert any(n["kind"] == "source_file" for n in lineage["nodes"])
    assert all({"from", "to", "label"} <= set(e) for e in lineage["edges"])
    schema = local_client.get(f"/api/v1/workspaces/{ws}/queries/schema").json()
    assert any(t["table"] == "orders" for t in schema["tables"])


def test_duplicate_table_name_conflict(local_client: ApiClient, workspace: dict[str, Any]) -> None:
    ws = workspace["id"]
    up = upload(local_client, ws, "orders.csv", CSV).json()
    ingest(local_client, ws, up["id"])
    up2 = upload(local_client, ws, "orders.csv", CSV).json()
    r = local_client.post(f"/api/v1/workspaces/{ws}/datasets/uploads/{up2['id']}/ingest", json={})
    assert r.status_code == 409 and r.json()["code"] == "table_exists"


@pytest.mark.parametrize(
    ("name", "content", "code"),
    [
        ("evil.exe", b"MZ\x90\x00", "unsupported_file_type"),
        ("data.xls", b"\xd0\xcf\x11\xe0", "unsupported_file_type"),
        ("fake.parquet", b"not parquet at all", "content_mismatch"),
        ("fake.xlsx", b"PK\x03\x04garbage", "content_mismatch"),
        ("bin.csv", b"a,b\n\x00\x01\x02", "content_mismatch"),
        ("x.json", b"hello", "content_mismatch"),
        ("empty.csv", b"", "empty_file"),
    ],
)
def test_upload_rejects_bad_files(
    local_client: ApiClient, workspace: dict[str, Any], name: str, content: bytes, code: str
) -> None:
    r = upload(local_client, workspace["id"], name, content)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == code


def test_upload_size_limit(tmp_path: Any) -> None:
    app = create_app(make_settings(tmp_path, AOS_MAX_UPLOAD_MB=1))
    with ApiClient(app) as c:
        c.get("/api/v1/auth/session")
        ws = c.post("/api/v1/workspaces", json={"name": "W"}).json()["id"]
        big = b"a,b\n" + b"1,2\n" * (300 * 1024)
        r = upload(c, ws, "big.csv", big)
        assert r.status_code == 413 and r.json()["code"] == "payload_too_large"
        # Nothing is left behind on disk.
        uploads = list((tmp_path / "data" / "workspaces" / ws / "uploads").rglob("*.csv"))
        assert uploads == []


def test_upload_filename_is_sanitised(local_client: ApiClient, workspace: dict[str, Any], app: Any) -> None:
    r = upload(local_client, workspace["id"], "../../etc/passwd.csv", CSV)
    assert r.status_code == 201
    from analystos_api.models import Upload

    with app.state.aos.session_factory() as db:
        up = db.get(Upload, r.json()["id"])
        assert up is not None
        assert "/etc/" not in up.stored_path and up.stored_path.endswith("passwd.csv")
        assert f"/workspaces/{workspace['id']}/uploads/" in up.stored_path


def test_excel_upload_with_title_rows(
    local_client: ApiClient, workspace: dict[str, Any], tmp_path: Any
) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    sh = wb.active
    sh.title = "Budget"
    sh.append(["Summit Supply budget FY26"])
    sh.append([])
    sh.append(["Branch", "Month", "Budget"])
    sh.append(["Dallas", "2026-08", 1000])
    sh.append(["Austin", "2026-08", 800])
    path = tmp_path / "budget.xlsx"
    wb.save(path)
    ws = workspace["id"]
    up = upload(local_client, ws, "budget.xlsx", path.read_bytes()).json()
    table = up["inspection"]["tables"][0]
    assert table["header_row"] == 3 and table["row_count"] == 2
    r = local_client.post(
        f"/api/v1/workspaces/{ws}/datasets/uploads/{up['id']}/inspect", json={"options": {"header_row": 3}}
    )
    assert r.status_code == 200 and r.json()["preview"]["tables"][0]["header_row"] == 3
    res = ingest(local_client, ws, up["id"], table_name="budget")
    ds = local_client.get(f"/api/v1/workspaces/{ws}/datasets/{res['dataset_id']}").json()
    assert ds["row_count"] == 2 and [c["name"] for c in ds["columns"]] == ["branch", "month", "budget"]


def test_inspect_overrides_match_ingest(local_client: ApiClient, workspace: dict[str, Any]) -> None:
    """R-35: header-row and delimiter overrides reach the preview, `limit` caps it, and the preview equals ingest."""
    import openpyxl

    ws = workspace["id"]
    wb = openpyxl.Workbook()
    sh = wb.active
    sh.title = "Data"
    sh.append(["Quarterly budget"])
    sh.append(["prepared by finance"])
    sh.append([])
    sh.append(["note: draft"])
    sh.append(["branch", "month", "budget"])
    for i in range(12):
        sh.append([f"B{i % 3}", f"2026-{i % 12 + 1:02d}", 1000 + i])
    buf = io.BytesIO()
    wb.save(buf)
    up = upload(local_client, ws, "budget.xlsx", buf.getvalue()).json()
    r = local_client.post(
        f"/api/v1/workspaces/{ws}/datasets/uploads/{up['id']}/inspect",
        json={"options": {"sheet": "Data", "header_row": 5}, "limit": 3},
    )
    assert r.status_code == 200, r.text
    table = r.json()["preview"]["tables"][0]
    assert table["header_row"] == 5 and [c["name"] for c in table["columns"]] == ["branch", "month", "budget"]
    assert len(table["preview_rows"]) == 3
    ingest(local_client, ws, up["id"], table_name="budget", options={"sheet": "Data", "header_row": 5})
    ds = next(
        d for d in local_client.get(f"/api/v1/workspaces/{ws}/datasets").json() if d["table_name"] == "budget"
    )
    assert [c["name"] for c in ds["columns"]] == ["branch", "month", "budget"] and ds["row_count"] == 12

    semi = b"a;b;c\n1;x;2.5\n2;y;3.5\n"
    up = upload(local_client, ws, "semi.csv", semi).json()
    r = local_client.post(
        f"/api/v1/workspaces/{ws}/datasets/uploads/{up['id']}/inspect",
        json={"options": {"delimiter": ";"}, "limit": 1},
    )
    table = r.json()["preview"]["tables"][0]
    assert [c["name"] for c in table["columns"]] == ["a", "b", "c"] and len(table["preview_rows"]) == 1
