from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pytest
from analystos_demo import DemoPaths, bootstrap_paths

REPO_ROOT = Path(__file__).resolve().parents[3]


def demo_cache_dir() -> Path:
    env = os.environ.get("AOS_DEMO_CACHE")
    return Path(env) if env else REPO_ROOT / "data" / "demo" / "summit-supply-seed42"


@pytest.fixture(scope="session")
def demo() -> DemoPaths:
    """The seed-42 dataset, generated once and reused while the generator code is unchanged."""
    return bootstrap_paths(demo_cache_dir(), seed=42)


@pytest.fixture(scope="session")
def scenarios(demo: DemoPaths) -> dict:
    return json.loads(demo.scenarios_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def raw(demo: DemoPaths) -> duckdb.DuckDBPyConnection:
    """Plain DuckDB over the raw files, with SQL written independently of analystos_demo.measure."""
    d = demo.data_dir
    con = duckdb.connect()
    con.execute(f"CREATE VIEW orders AS SELECT * FROM read_csv('{d}/orders.csv')")
    con.execute(f"CREATE VIEW order_lines AS SELECT * FROM read_parquet('{d}/order_lines.parquet')")
    con.execute(f"CREATE VIEW branches AS SELECT * FROM read_csv('{d}/branches.csv')")
    con.execute(
        f"CREATE VIEW customers AS SELECT * FROM read_csv('{d}/customers.csv', types={{'account_opened': 'VARCHAR'}})"
    )
    con.execute(f"CREATE VIEW products AS SELECT * FROM read_csv('{d}/products.csv')")
    con.execute(f"CREATE VIEW leads AS SELECT * FROM read_csv('{d}/leads.csv')")
    con.execute(f"CREATE VIEW inventory AS SELECT * FROM read_parquet('{d}/inventory_snapshots.parquet')")
    con.execute(f"CREATE VIEW forecast AS SELECT * FROM read_csv('{d}/revenue_forecast.csv')")
    con.execute(
        f"CREATE VIEW returns AS SELECT * FROM read_csv('{d}/returns.csv', types={{'order_id': 'VARCHAR'}})"
    )
    con.execute(
        """
        CREATE VIEW sales AS
        SELECT l.*, o.order_date, o.branch_id, o.customer_id, o.channel, b.branch_name, b.region,
               p.category, p.tier
        FROM order_lines l
        JOIN orders o USING (order_id)
        JOIN branches b USING (branch_id)
        JOIN products p USING (product_id)
        WHERE o.status <> 'cancelled'
        """
    )
    return con
