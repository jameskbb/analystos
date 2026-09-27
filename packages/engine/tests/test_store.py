from __future__ import annotations

import threading

import pandas as pd
import pyarrow as pa
import pytest
from analystos_engine.sqlsafety import UnsafeSQLError
from analystos_engine.store import QueryTimeoutError, StoreError, WorkspaceStore, quote_ident


def test_file_store_persists(tmp_path):
    ws = tmp_path / "ws1"
    with WorkspaceStore(ws) as store:
        store.write_table("sales", pd.DataFrame({"id": [1, 2], "amount": [10.5, 20.0]}))
    assert (ws / "warehouse.duckdb").exists()
    with WorkspaceStore(ws) as store:
        assert [t.name for t in store.list_tables()] == ["sales"]
        info = store.describe("sales")
        assert info.row_count == 2
        assert [c.name for c in info.columns] == ["id", "amount"]
        assert info.columns[1].type == "DOUBLE"


def test_two_handles_same_file_share_database(tmp_path):
    a = WorkspaceStore(tmp_path / "ws")
    b = WorkspaceStore(tmp_path / "ws")
    a.write_table("t", pd.DataFrame({"x": [1]}))
    assert b.has_table("t")
    a.close()
    assert b.row_count("t") == 1
    b.close()
    with pytest.raises(StoreError):
        b.list_tables()


def test_execute_read_row_cap_and_types(star_store):
    res = star_store.execute_read("SELECT * FROM order_lines ORDER BY line_id", limit=3)
    assert res.row_count == 3 and res.truncated
    assert res.column_names == ["line_id", "order_id", "product_id", "qty", "net_amount"]
    full = star_store.execute_read("SELECT * FROM order_lines", limit=None)
    assert full.row_count == 8 and not full.truncated
    exact = star_store.execute_read("SELECT * FROM order_lines", limit=8)
    assert not exact.truncated
    assert res.elapsed_ms >= 0
    assert res.sql.startswith("SELECT")


def test_execute_read_params(star_store):
    res = star_store.execute_read("SELECT count(*) FROM orders WHERE status = ?", ["complete"])
    assert res.scalar() == 4
    res = star_store.execute_read("SELECT count(*) AS n FROM orders WHERE status = $s", {"s": "cancelled"})
    assert res.scalar("n") == 1


def test_execute_read_rejects_writes(star_store):
    for sql in [
        "DROP TABLE orders",
        "DELETE FROM orders",
        "SELECT 1; DROP TABLE orders",
        "COPY orders TO 'x.csv'",
    ]:
        with pytest.raises(UnsafeSQLError):
            star_store.execute_read(sql)
    assert star_store.row_count("orders") == 5


def test_query_error_is_store_error(star_store):
    with pytest.raises(StoreError) as err:
        star_store.execute_read("SELECT nope FROM orders")
    assert "nope" in str(err.value)


def test_timeout_interrupts(star_store):
    with pytest.raises(QueryTimeoutError):
        star_store.execute_read(
            "SELECT sum(a.range * b.range) FROM range(200000) a, range(200000) b", timeout_s=0.3
        )
    # the store is still usable afterwards
    assert star_store.row_count("orders") == 5


def test_concurrent_reads(star_store):
    errors: list[Exception] = []

    def work() -> None:
        try:
            for _ in range(20):
                assert star_store.execute_read("SELECT sum(net_amount) FROM order_lines").scalar() == 710.0
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_table_version_stable_and_sensitive(star_store):
    v1 = star_store.table_version("orders")
    v2 = star_store.table_version("orders")
    assert v1.content_hash == v2.content_hash and v1.row_count == 5
    df = star_store.execute_read("SELECT * FROM orders ORDER BY order_id DESC").to_pandas()
    star_store.write_table("orders", df, if_exists="replace")
    assert star_store.table_version("orders").content_hash == v1.content_hash  # order independent
    df.loc[0, "shipping_fee"] = 99.0
    star_store.write_table("orders", df, if_exists="replace")
    assert star_store.table_version("orders").content_hash != v1.content_hash


def test_write_modes(star_store):
    star_store.write_table("t", pd.DataFrame({"a": [1]}))
    with pytest.raises(StoreError):
        star_store.write_table("t", pd.DataFrame({"a": [2]}))
    star_store.write_table("t", pd.DataFrame({"a": [2]}), if_exists="append")
    assert star_store.row_count("t") == 2
    star_store.write_table("t", pa.table({"a": [5]}), if_exists="replace")
    assert star_store.row_count("t") == 1
    star_store.write_table("t2", [{"a": 1, "b": "x"}])
    star_store.write_table("t3", {"a": [1, 2]})
    with pytest.raises(StoreError):
        star_store.write_table("t4", 42)
    with pytest.raises(StoreError):
        star_store.write_table("t", pd.DataFrame({"a": [1]}), if_exists="merge")


def test_polars_write(star_store):
    import polars as pl

    star_store.write_table("pl", pl.DataFrame({"x": [1, 2, 3]}))
    assert star_store.row_count("pl") == 3


@pytest.mark.parametrize("bad", ["1abc", "a-b", "a b", "", "x" * 64, "_aos_internal", 'a"b'])
def test_invalid_table_names(star_store, bad):
    with pytest.raises(StoreError):
        star_store.write_table(bad, pd.DataFrame({"a": [1]}))


def test_rename_drop_preview(star_store):
    star_store.write_table("tmp", pd.DataFrame({"a": [1, 2, 3]}))
    star_store.rename_table("tmp", "tmp2")
    assert not star_store.has_table("tmp")
    assert star_store.preview("tmp2", limit=2).row_count == 2
    with pytest.raises(StoreError):
        star_store.rename_table("tmp2", "orders")
    with pytest.raises(StoreError):
        star_store.rename_table("missing", "x")
    star_store.drop_table("tmp2")
    assert not star_store.has_table("tmp2")
    with pytest.raises(StoreError):
        star_store.preview("tmp2")
    with pytest.raises(StoreError):
        star_store.describe("tmp2")
    with pytest.raises(StoreError):
        star_store.row_count("tmp2")


def test_export_arrow(star_store):
    t = star_store.export_arrow("SELECT * FROM orders", limit=2)
    assert t.num_rows == 2
    with pytest.raises(UnsafeSQLError):
        star_store.export_arrow("DROP TABLE orders")


def test_quote_ident():
    assert quote_ident('a"b') == '"a""b"'


def test_negative_limit(star_store):
    with pytest.raises(StoreError):
        star_store.execute_read("SELECT 1", limit=-1)


def test_values_normalized(star_store):
    res = star_store.execute_read(
        "SELECT 1.5::DECIMAL(10,2) AS d, 'abc'::BLOB AS b, uuid() AS u, INTERVAL 1 DAY AS i"
    )
    d, b, u, i = res.rows[0]
    assert d == 1.5 and isinstance(b, str) and isinstance(u, str) and i == 86400.0
