from __future__ import annotations

from analystos_engine.store import WorkspaceStore
from analystos_investigator.llm import LLMUnavailable, ScriptedProvider, execute_with_repair


def test_valid_sql_runs_first_time(store: WorkspaceStore) -> None:
    out = execute_with_repair("SELECT count(*) AS n FROM orders", store, None)
    assert out.succeeded and len(out.attempts) == 1 and out.result.rows[0][0] > 0


def test_failure_without_llm_is_surfaced(store: WorkspaceStore) -> None:
    out = execute_with_repair("SELECT nope FROM orders", store, None)
    assert not out.succeeded and len(out.attempts) == 1
    assert out.final_error and "no LLM configured" in out.final_error


def test_repair_uses_schema_and_retries(store: WorkspaceStore) -> None:
    llm = ScriptedProvider(
        {
            "repair_sql": {
                "sql": "SELECT count(*) AS n FROM orders WHERE channel = 'Online'",
                "explanation": "column is channel, not sales_channel",
            }
        }
    )
    out = execute_with_repair("SELECT count(*) FROM orders WHERE sales_channel = 'Online'", store, llm)
    assert out.succeeded and len(out.attempts) == 2
    assert not out.attempts[0].ok and out.attempts[1].ok
    prompt = llm.calls[0]["messages"][0].content
    assert any("channel VARCHAR" in block for block in prompt)  # schema was re-inspected
    assert any("sales_channel" in block for block in prompt)


def test_unknown_table_lists_available_tables(store: WorkspaceStore) -> None:
    llm = ScriptedProvider(
        {"repair_sql": {"sql": "SELECT count(*) FROM orders", "explanation": "table is orders"}}
    )
    out = execute_with_repair("SELECT count(*) FROM sales_orders", store, llm)
    assert out.succeeded
    assert any("available_tables" in block for block in llm.calls[0]["messages"][0].content)


def test_unsafe_repair_is_rejected_and_loop_is_bounded(store: WorkspaceStore) -> None:
    llm = ScriptedProvider(
        {
            "repair_sql": [
                {"sql": "DELETE FROM orders", "explanation": "clean up"},
                {"sql": "SELECT still_wrong FROM orders", "explanation": "try again"},
            ]
        }
    )
    out = execute_with_repair("SELECT nope FROM orders", store, llm, max_attempts=3)
    assert not out.succeeded and len(out.attempts) == 3
    assert "UnsafeSQL" in (out.attempts[1].error or "")
    assert out.final_error == out.attempts[-1].error
    assert len(llm.calls) == 2
    assert store.row_count("orders") > 0


def test_model_giving_up_ends_the_loop(store: WorkspaceStore) -> None:
    llm = ScriptedProvider({"repair_sql": {"sql": "SELECT nope FROM orders", "explanation": "cannot fix"}})
    out = execute_with_repair("SELECT nope FROM orders", store, llm, max_attempts=5)
    assert not out.succeeded and len(out.attempts) == 1 and "cannot fix" in (out.final_error or "")


def test_provider_errors_end_the_loop(store: WorkspaceStore) -> None:
    llm = ScriptedProvider({"repair_sql": LLMUnavailable("quota")})
    out = execute_with_repair("SELECT nope FROM orders", store, llm)
    assert not out.succeeded and "quota" in (out.final_error or "")
