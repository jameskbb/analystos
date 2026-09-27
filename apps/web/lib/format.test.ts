import { describe, expect, it } from "vitest";
import { formatCompact, formatPctChange, formatSignedChange, formatValue, formatWindow } from "./format";

describe("format", () => {
  it("formats business values", () => {
    expect(formatValue(4200000, "currency")).toBe("$4,200,000");
    expect(formatValue(0.118, "percent")).toBe("11.8%");
    expect(formatCompact(4200000, "currency")).toBe("$4.2M");
    expect(formatCompact(-420000, "currency")).toBe("-$420K");
  });
  it("formats signed changes with a true minus sign", () => {
    expect(formatPctChange(-0.118)).toBe("−11.8%");
    expect(formatPctChange(0.028)).toBe("+2.8%");
    expect(formatSignedChange(-420000, "currency")).toBe("−$420K");
    // Percentage metrics change by percentage points.
    expect(formatSignedChange(-0.0476, "percent")).toBe("−4.8 pp");
    expect(formatSignedChange(0.02, "percent")).toBe("+2.0 pp");
  });
  it("formats date windows compactly", () => {
    expect(formatWindow("2026-08-01", "2026-08-31")).toBe("Aug 1 – 31, 2026");
  });
});
