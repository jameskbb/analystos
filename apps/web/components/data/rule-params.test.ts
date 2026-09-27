import { describe, expect, it } from "vitest";
import { buildRuleParams, defaultRuleName, describeRule, emptyDraft, parseAllowedValues, validateRule, type RuleDraft } from "./rule-params";

const draft = (patch: Partial<RuleDraft>): RuleDraft => ({ ...emptyDraft("orders"), column: "net_amount", ...patch });

describe("validateRule", () => {
  it("requires a table and, for column rules, a column", () => {
    expect(validateRule({ ...emptyDraft(""), kind: "not_null" })).toMatchObject({ table_name: expect.any(String), column: expect.any(String) });
    expect(validateRule(draft({ kind: "not_null" }))).toEqual({});
  });

  it("range needs at least one numeric bound and min ≤ max", () => {
    expect(validateRule(draft({ kind: "range" })).min).toMatch(/minimum, a maximum or both/);
    expect(validateRule(draft({ kind: "range", min: "0" }))).toEqual({});
    expect(validateRule(draft({ kind: "range", min: "abc" })).min).toMatch(/number/);
    expect(validateRule(draft({ kind: "range", min: "5", max: "1" })).max).toMatch(/≥ minimum/);
  });

  it("between needs both bounds", () => {
    expect(validateRule(draft({ kind: "between", min: "0" })).max).toBeDefined();
    expect(validateRule(draft({ kind: "between", min: "0", max: "1" }))).toEqual({});
  });

  it("fk_exists needs a referenced table and column that is not itself", () => {
    expect(validateRule(draft({ kind: "fk_exists" }))).toMatchObject({ ref_table: expect.any(String), ref_column: expect.any(String) });
    expect(validateRule(draft({ kind: "fk_exists", ref_table: "orders", ref_column: "net_amount" })).ref_column).toMatch(/itself/);
    expect(validateRule(draft({ kind: "fk_exists", column: "customer_id", ref_table: "customers", ref_column: "customer_id" }))).toEqual({});
  });

  it("validates allowed values, regex and custom SQL", () => {
    expect(validateRule(draft({ kind: "allowed_values", values: " , " })).values).toBeDefined();
    expect(validateRule(draft({ kind: "regex", pattern: "([" })).pattern).toMatch(/valid/);
    expect(validateRule(draft({ kind: "regex", pattern: "^[A-Z]+$" }))).toEqual({});
    expect(validateRule(draft({ kind: "custom_sql", column: "" , sql: "DELETE FROM orders" })).sql).toMatch(/read-only/);
    expect(validateRule(draft({ kind: "custom_sql", column: "", sql: "SELECT 1; SELECT 2" })).sql).toMatch(/single/);
    expect(validateRule(draft({ kind: "custom_sql", column: "", sql: "with x as (select 1) select * from x;" }))).toEqual({});
  });
});

describe("buildRuleParams", () => {
  it("coerces numbers and omits empty bounds", () => {
    expect(buildRuleParams(draft({ kind: "range", min: "0" }))).toEqual({ min: 0 });
    expect(buildRuleParams(draft({ kind: "between", min: "0", max: "1" }))).toEqual({ min: 0, max: 1 });
  });
  it("splits and de-duplicates allowed values", () => {
    expect(parseAllowedValues("Enterprise, Contractor\nRetail\nRetail\n")).toEqual(["Enterprise", "Contractor", "Retail"]);
    expect(buildRuleParams(draft({ kind: "allowed_values", values: "A,B" }))).toEqual({ values: ["A", "B"] });
  });
  it("strips a trailing semicolon from custom SQL", () => {
    expect(buildRuleParams(draft({ kind: "custom_sql", sql: "select 1;  " }))).toEqual({ sql: "select 1" });
  });
});

describe("rule naming and description", () => {
  it("generates readable default names", () => {
    expect(defaultRuleName(draft({ kind: "range", min: "0" }))).toBe("orders.net_amount ≥ 0");
    expect(defaultRuleName(draft({ kind: "fk_exists", column: "customer_id", ref_table: "customers", ref_column: "id" }))).toBe(
      "orders.customer_id exists in customers.id",
    );
    expect(defaultRuleName(draft({ kind: "not_null" }))).toBe("orders.net_amount: not null");
  });
  it("describes stored rules", () => {
    expect(describeRule("between", "discount_pct", { min: 0, max: 1 })).toBe("discount_pct between 0 and 1");
    expect(describeRule("allowed_values", "segment", { values: ["a", "b", "c", "d", "e", "f", "g"] })).toBe("segment in {a, b, c, d, e, +2}");
  });
});
