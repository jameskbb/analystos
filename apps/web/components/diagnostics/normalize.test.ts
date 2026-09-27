import { describe, expect, it } from "vitest";
import { errorRate, infoSections, summaryRows } from "./normalize";

describe("diagnostics normalizers", () => {
  it("splits system info into sections", () => {
    const s = infoSections({ version: "0.1.0", ai_configured: false, database: { dialect: "sqlite", migration_head: "abc" } });
    expect(s[0]).toEqual({ title: "Server", rows: [{ key: "Version", value: "0.1.0" }, { key: "Ai Configured", value: "no" }] });
    expect(s[1].title).toBe("Database");
    expect(s[1].rows).toContainEqual({ key: "Dialect", value: "sqlite" });
  });

  it("accepts several summary shapes", () => {
    expect(summaryRows({ categories: [{ category: "sql", count: 3 }] })).toHaveLength(1);
    const rows = summaryRows({ by_category: { sql: { count: 10, errors: 1 }, ai: { count: 0 } } });
    expect(rows.map((r) => r.category)).toEqual(["sql", "ai"]);
    expect(errorRate(rows[0])).toBeCloseTo(0.1);
    expect(errorRate(rows[1])).toBeNull();
    expect(summaryRows(null)).toEqual([]);
  });
});
