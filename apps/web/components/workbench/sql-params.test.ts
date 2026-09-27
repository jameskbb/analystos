import { describe, expect, it } from "vitest";
import {
  coerceParamValue,
  detectParams,
  guessParamType,
  normalizeParams,
  syncParamDefs,
  validateParamValues,
} from "./sql-params";

describe("detectParams", () => {
  it("finds $name and {{name}} placeholders in order, once each", () => {
    const sql = "select * from orders where order_date >= $start_date and branch = {{ branch }} and order_date < $end_date and x = $start_date";
    expect(detectParams(sql)).toEqual(["start_date", "branch", "end_date"]);
  });

  it("ignores placeholders in strings, quoted identifiers and comments", () => {
    const sql = `select '$not_a_param', "$col" from t -- where a = $commented\n/* $block */ where b = $real and c = 'it''s $x'`;
    expect(detectParams(sql)).toEqual(["real"]);
  });

  it("does not treat DuckDB casts or positional params as named params", () => {
    expect(detectParams("select amount::double, $1 from t")).toEqual([]);
  });
});

describe("normalizeParams", () => {
  it("rewrites {{name}} to $name outside strings", () => {
    expect(normalizeParams("select '{{keep}}' where a = {{ region }}")).toBe("select '{{keep}}' where a = $region");
  });
});

describe("param typing and values", () => {
  it("guesses types from names", () => {
    expect(guessParamType("start_date")).toBe("date");
    expect(guessParamType("limit")).toBe("integer");
    expect(guessParamType("min_amount")).toBe("number");
    expect(guessParamType("regions")).toBe("string_list");
    expect(guessParamType("branch")).toBe("string");
  });

  it("keeps existing definitions and adds new ones", () => {
    const defs = syncParamDefs(["a", "b"], [{ name: "b", type: "number" }, { name: "gone", type: "string" }]);
    expect(defs).toEqual([{ name: "a", type: "string", required: true }, { name: "b", type: "number" }]);
  });

  it("coerces values to typed params", () => {
    expect(coerceParamValue("42", "integer")).toBe(42);
    expect(coerceParamValue("1.5", "number")).toBe(1.5);
    expect(coerceParamValue("Dallas, Houston", "string_list")).toEqual(["Dallas", "Houston"]);
    expect(coerceParamValue("true", "boolean")).toBe(true);
    expect(coerceParamValue("  ", "string")).toBeNull();
  });

  it("validates required and typed values", () => {
    const errs = validateParamValues(
      [
        { name: "n", type: "integer" },
        { name: "d", type: "date" },
        { name: "opt", type: "string", required: false },
        { name: "withDefault", type: "string", default: "x" },
      ],
      { n: "1.5", d: "Aug 1" },
    );
    expect(errs).toEqual({ n: "Must be a whole number", d: "Use YYYY-MM-DD" });
    expect(validateParamValues([{ name: "r", type: "string" }], {})).toEqual({ r: "Required" });
  });
});
