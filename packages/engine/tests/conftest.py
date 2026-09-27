"""Shared fixtures: a tiny star schema designed so that naive joins double count.

customers (3) 1-N orders (5) 1-N order_lines (8) N-1 products (3)

Orders carry an order-level ``shipping_fee``; summing it after joining order_lines
repeats it once per line, which is exactly the bug the compiler must avoid.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from analystos_engine.semantic.models import (
    Dimension,
    DriverEdge,
    Entity,
    Filter,
    GlossaryTerm,
    Metric,
    MetricTree,
    Relationship,
    SemanticModel,
)
from analystos_engine.store import WorkspaceStore

D = dt.date


def star_frames() -> dict[str, pd.DataFrame]:
    customers = pd.DataFrame(
        {
            "customer_id": [1, 2, 3],
            "customer_name": ["Acme Builders", "Bayou Homes", "Capitol Retail"],
            "region": ["Dallas", "Houston", "Austin"],
            "segment": ["Enterprise", "Retail", "Retail"],
        }
    )
    orders = pd.DataFrame(
        {
            "order_id": [101, 102, 103, 104, 105],
            "customer_id": [1, 1, 2, 3, 2],
            "order_date": [D(2026, 7, 5), D(2026, 8, 3), D(2026, 8, 10), D(2026, 8, 15), D(2026, 7, 20)],
            "status": ["complete", "complete", "cancelled", "complete", "complete"],
            "shipping_fee": [10.0, 20.0, 5.0, 0.0, 7.0],
        }
    )
    order_lines = pd.DataFrame(
        {
            "line_id": [1, 2, 3, 4, 5, 6, 7, 8],
            "order_id": [101, 101, 102, 102, 102, 103, 104, 105],
            "product_id": ["P1", "P2", "P1", "P2", "P3", "P3", "P1", "P2"],
            "qty": [2, 1, 5, 1, 3, 1, 1, 2],
            "net_amount": [100.0, 50.0, 250.0, 40.0, 90.0, 30.0, 50.0, 100.0],
        }
    )
    products = pd.DataFrame(
        {
            "product_id": ["P1", "P2", "P3"],
            "category": ["Lumber", "Roofing", "Tools"],
            "tier": ["standard", "premium", "entry"],
        }
    )
    return {"customers": customers, "orders": orders, "order_lines": order_lines, "products": products}


def star_model() -> SemanticModel:
    return SemanticModel(
        name="star",
        entities=[
            Entity(
                name="customers",
                table="customers",
                primary_key="customer_id",
                grain_description="one row per customer",
            ),
            Entity(
                name="orders",
                table="orders",
                primary_key="order_id",
                grain_description="one row per order",
                default_time_dimension="order_date",
            ),
            Entity(
                name="order_lines",
                table="order_lines",
                primary_key="line_id",
                grain_description="one row per order line",
                default_time_dimension="order_date",
            ),
            Entity(
                name="products",
                table="products",
                primary_key="product_id",
                grain_description="one row per product",
            ),
        ],
        dimensions=[
            Dimension(name="region", entity="customers", expr="region"),
            Dimension(name="segment", entity="customers", expr="segment"),
            Dimension(name="customer_name", entity="customers", expr="customer_name"),
            Dimension(name="order_date", entity="orders", expr="order_date", type="time"),
            Dimension(name="status", entity="orders", expr="status"),
            Dimension(name="category", entity="products", expr="category"),
            Dimension(name="tier", entity="products", expr="tier"),
            Dimension(name="product_id", entity="order_lines", expr="product_id"),
        ],
        metrics=[
            Metric(
                id="revenue",
                name="Revenue",
                entity="order_lines",
                expr="net_amount",
                agg="sum",
                format="currency",
            ),
            Metric(id="units", name="Units", entity="order_lines", expr="qty", agg="sum", format="integer"),
            Metric(
                id="orders",
                name="Orders",
                entity="orders",
                expr="order_id",
                agg="count_distinct",
                format="integer",
            ),
            Metric(
                id="shipping",
                name="Shipping Fees",
                entity="orders",
                expr="shipping_fee",
                agg="sum",
                format="currency",
            ),
            Metric(id="customers", name="Customers", entity="customers", agg="count", format="integer"),
            Metric(
                id="active_customers",
                name="Active Customers",
                entity="orders",
                expr="customer_id",
                agg="count_distinct",
            ),
            Metric(
                id="aov",
                name="Average Order Value",
                kind="ratio",
                numerator="revenue",
                denominator="orders",
                format="currency",
            ),
            Metric(
                id="asp",
                name="Average Selling Price",
                kind="derived",
                formula="revenue / units",
                format="currency",
            ),
            Metric(
                id="completed_revenue",
                name="Completed Revenue",
                entity="order_lines",
                expr="net_amount",
                agg="sum",
                filters=[Filter(dimension="status", op="eq", values=["complete"])],
            ),
            Metric(
                id="avg_line", name="Average Line Amount", entity="order_lines", expr="net_amount", agg="avg"
            ),
            Metric(
                id="revenue_per_customer",
                name="Revenue per Customer",
                kind="ratio",
                numerator="revenue",
                denominator="active_customers",
            ),
        ],
        relationships=[
            Relationship(
                from_entity="orders",
                from_col="customer_id",
                to_entity="customers",
                to_col="customer_id",
                cardinality="many_to_one",
                approved=True,
            ),
            Relationship(
                from_entity="order_lines",
                from_col="order_id",
                to_entity="orders",
                to_col="order_id",
                cardinality="many_to_one",
                approved=True,
            ),
            Relationship(
                from_entity="order_lines",
                from_col="product_id",
                to_entity="products",
                to_col="product_id",
                cardinality="many_to_one",
                approved=True,
            ),
        ],
        metric_trees=[
            MetricTree(
                root_metric="revenue",
                nodes=[
                    DriverEdge(parent="revenue", child="orders", relation="multiplicative"),
                    DriverEdge(parent="revenue", child="aov", relation="multiplicative"),
                ],
            )
        ],
        glossary=[
            GlossaryTerm(term="Sales", definition="Net revenue", metric_id="revenue", synonyms=["turnover"])
        ],
    )


@pytest.fixture()
def star_store() -> WorkspaceStore:
    store = WorkspaceStore.in_memory()
    for name, df in star_frames().items():
        store.write_table(name, df)
    yield store
    store.close()


@pytest.fixture()
def model() -> SemanticModel:
    return star_model()
