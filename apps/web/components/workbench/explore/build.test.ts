import { describe, expect, it } from "vitest";
import { buildMetricQuery, buildPivotRequest, buildTableRequest, exclusiveEnd, ROWS_FIELD } from "./build";
import { filterValues } from "./filter-editor";

describe("explore request builders", () => {
  const types = { amount: "DOUBLE", region: "VARCHAR" };

  it("builds a grouped table request with typed filters and calculated fields", () => {
    const body = buildTableRequest(
      {
        table: "orders",
        filters: [
          { field: "amount", op: "gt", text: "100" },
          { field: "region", op: "in", text: "Dallas, Houston" },
          { field: "region", op: "eq", text: "" },
        ],
        groupBy: ["region"],
        aggregates: [{ fn: "sum", column: "amount" }],
        calculated: [{ name: "margin", expr: "amount - cost" }, { name: "", expr: "" }],
        sort: { field: "sum_amount", desc: true },
        limit: 100,
      },
      types,
    );
    expect(body.filters).toEqual([
      { column: "amount", op: "gt", values: [100] },
      { column: "region", op: "in", values: ["Dallas", "Houston"] },
    ]);
    expect(body.aggregates).toEqual([{ column: "amount", fn: "sum", alias: "sum_amount" }]);
    expect(body.calculated).toEqual([{ name: "margin", expr: "amount - cost" }]);
    expect(body.order_by).toEqual([{ field: "sum_amount", desc: true }]);
  });

  it("defaults to a row count when grouping without aggregates", () => {
    const body = buildTableRequest(
      { table: "t", filters: [], groupBy: ["region"], aggregates: [], calculated: [], sort: null, limit: 10 },
      types,
    );
    expect(body.aggregates).toEqual([{ column: null, fn: "count", alias: "row_count" }]);
  });

  it("requests exact partial aggregates at the rows × columns grain for pivots", () => {
    const body = buildPivotRequest(
      "orders",
      [],
      [],
      {
        rows: ["region"],
        cols: ["channel"],
        values: [
          { field: "amount", agg: "avg" },
          { field: ROWS_FIELD, agg: "count" },
        ],
        subtotals: true,
        grandTotals: true,
        sortBy: "label",
        sortDir: "asc",
      },
      types,
    );
    expect(body.group_by).toEqual(["region", "channel"]);
    expect(body.aggregates).toEqual([
      { column: "amount", fn: "sum", alias: "__sum_amount" },
      { column: "amount", fn: "count", alias: "__count_amount" },
      { column: null, fn: "count", alias: `__count_${ROWS_FIELD}` },
    ]);
  });

  it("builds a metric query with time grains and a date window", () => {
    const q = buildMetricQuery({
      metrics: ["revenue"],
      dimensions: [{ name: "order_date", grain: "month" }, { name: "branch", grain: "" }],
      filters: [{ field: "region", op: "neq", text: "Austin" }],
      timeDimension: "",
      start: "2026-08-01",
      end: "2026-08-31",
      limit: 500,
    });
    expect(q.dimensions).toEqual(["order_date__month", "branch"]);
    expect(q.filters).toEqual([{ dimension: "region", op: "neq", values: ["Austin"] }]);
    // The picker's inclusive Aug 31 becomes the half-open end Sep 1, so Aug 31 is not dropped (R-25).
    expect(q.time).toEqual({ dimension: null, start: "2026-08-01", end: "2026-09-01" });
  });

  it("parses filter drafts by operator arity", () => {
    expect(filterValues({ field: "a", op: "between", text: "1", text2: "5" }, "INTEGER")).toEqual([1, 5]);
    expect(filterValues({ field: "a", op: "between", text: "1" })).toBeNull();
    expect(filterValues({ field: "a", op: "is_null", text: "" })).toEqual([]);
    expect(filterValues({ field: "", op: "eq", text: "x" })).toBeNull();
  });
});

describe("exclusiveEnd", () => {
  it("adds one day across month and year boundaries and leap days", () => {
    expect(exclusiveEnd("2026-08-31")).toBe("2026-09-01");
    expect(exclusiveEnd("2026-12-31")).toBe("2027-01-01");
    expect(exclusiveEnd("2028-02-28")).toBe("2028-02-29");
    expect(exclusiveEnd("2026-08-15")).toBe("2026-08-16");
  });
});
