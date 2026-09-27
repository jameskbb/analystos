"""Migrations, logging redaction, jobs, upload sniffing units."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from analystos_api.db import Base
from analystos_api.logs import redact
from analystos_api.migrate import upgrade_to_head
from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_head_on_sqlite_matches_models(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path}/m.db")
    upgrade_to_head(engine)
    upgrade_to_head(engine)  # idempotent
    tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= tables and "alembic_version" in tables
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff


def test_migration_renders_for_postgres() -> None:
    """Offline SQL generation for PostgreSQL (no server needed) must succeed."""
    import io

    from alembic import command
    from analystos_api.migrate import alembic_config

    buf = io.StringIO()
    cfg = alembic_config("postgresql://u:p@localhost/db")
    cfg.output_buffer = buf
    command.upgrade(cfg, "head", sql=True)
    sql = buf.getvalue()
    assert "CREATE TABLE workspaces" in sql and "TIMESTAMP WITH TIME ZONE" in sql


def test_redaction() -> None:
    event = {
        "password": "hunter2",
        "config": {"host": "db", "api_key": "abc"},
        "token_prefix": "aos_12",
        "message": "failed for postgresql://user:pw@host/db with aos_" + "a" * 30,
        "tokens_in": 12,
        "note": "key sk-ant-api03-abcdefghijk",
    }
    out = redact(event)
    s = json.dumps(out)
    assert "hunter2" not in s and "abc" not in s.replace("abcdefghijk", "") and "pw@" not in s
    assert "aos_aaaa" not in s and "sk-ant-api03" not in s
    assert out["tokens_in"] == 12 and out["token_prefix"] == "aos_12" and out["config"]["host"] == "db"


def test_structlog_output_is_redacted(capsys: Any) -> None:
    from analystos_api.logs import redaction_processor

    ev = redaction_processor(
        None, "info", {"event": "x", "secrets": {"password": "p"}, "authorization": "Bearer y"}
    )
    assert ev["secrets"] == "[REDACTED]" and ev["authorization"] == "[REDACTED]"
    logging.getLogger("x").info("ok")


def test_jobs_thread_mode_and_interrupted_recovery(tmp_path: Path) -> None:
    from analystos_api.main import create_app
    from analystos_api.models import Job
    from conftest import ApiClient, make_settings

    app = create_app(make_settings(tmp_path, AOS_JOB_EXECUTION="thread"))
    state = app.state.aos
    with ApiClient(app) as c:
        c.get("/api/v1/auth/session")
        ws = c.post("/api/v1/workspaces", json={"name": "J"}).json()["id"]
        job_id = state.jobs.submit(
            kind="unit",
            fn=lambda ctx: (ctx.progress(0.5, "half"), {"x": 1})[1],
            workspace_id=ws,
            user_id=None,
        )
        state.jobs.wait(job_id, 10)
        job = c.get(f"/api/v1/workspaces/{ws}/jobs/{job_id}").json()
        assert job["status"] == "succeeded" and job["result"] == {"x": 1}

        def boom(_ctx: Any) -> None:
            raise ValueError("bad input")

        failed = state.jobs.submit(kind="unit", fn=boom, workspace_id=ws, user_id=None)
        state.jobs.wait(failed, 10)
        job = c.get(f"/api/v1/workspaces/{ws}/jobs/{failed}").json()
        assert job["status"] == "failed" and "bad input" in job["error"]
        with state.session_factory() as db:
            db.add(Job(workspace_id=ws, kind="stuck", status="running"))
            db.commit()
        assert state.jobs.recover_interrupted() == 1


def test_upload_sniffing_units(tmp_path: Path) -> None:
    from analystos_api.services.uploads import safe_filename, sniff_matches

    p = tmp_path / "a.parquet"
    p.write_bytes(b"PAR1xxxxPAR1")
    assert sniff_matches(p, "parquet")[0]
    p.write_bytes(b"PAR1xxxx")
    assert not sniff_matches(p, "parquet")[0]
    assert safe_filename("../../x y.csv") == "x_y.csv" and safe_filename("..hidden") == "hidden"
