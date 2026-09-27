import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TokenReveal } from "./token-reveal";
import { tokenState } from "./tokens-tab";
import { describeResolution, describeWindow, fiscalYearLabel, windowDays } from "./calendar-format";
import { formatUsd } from "./ai-tab";

describe("TokenReveal", () => {
  it("shows the raw token once, copies it, and dismisses", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn(async () => {});
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const onDone = vi.fn();
    const { rerender } = render(<TokenReveal token="aos_secret_123" name="MCP" onDone={onDone} />);
    expect(screen.getByTestId("raw-token")).toHaveTextContent("aos_secret_123");
    expect(screen.getByRole("alert")).toHaveTextContent(/only time it is shown/);
    await user.click(screen.getByRole("button", { name: "Copy token" }));
    expect(writeText).toHaveBeenCalledWith("aos_secret_123");
    expect(screen.getByText("Copied")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(onDone).toHaveBeenCalled();
    rerender(<div />);
    expect(screen.queryByText("aos_secret_123")).not.toBeInTheDocument();
  });

  it("explains a clipboard failure", async () => {
    const user = userEvent.setup();
    Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn(async () => Promise.reject(new Error("denied"))) }, configurable: true });
    render(<TokenReveal token="aos_x" name="t" onDone={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Copy token" }));
    expect(await screen.findByText(/copy it manually/)).toBeInTheDocument();
  });
});

describe("tokenState", () => {
  const base = { id: "t", name: "n", prefix: "aos_ab", read_only: true, created_at: "2026-01-01T00:00:00Z" };
  it("classifies active, revoked and expired tokens", () => {
    const now = Date.parse("2026-09-18T00:00:00Z");
    expect(tokenState({ ...base, expires_at: null }, now)).toBe("active");
    expect(tokenState({ ...base, revoked_at: "2026-02-01T00:00:00Z" }, now)).toBe("revoked");
    expect(tokenState({ ...base, expires_at: "2026-09-01T00:00:00Z" }, now)).toBe("expired");
  });
});

describe("calendar preview formatting", () => {
  it("counts inclusive days", () => {
    expect(windowDays({ start: "2026-08-01", end: "2026-08-31" })).toBe(31);
    expect(windowDays({ start: "2026-08-20T00:00:00", end: "2026-09-18" })).toBe(30);
    expect(windowDays(null)).toBeNull();
  });

  it("describes a resolution with its comparison windows", () => {
    const d = describeResolution({
      input: "last month",
      window: { start: "2026-08-01", end: "2026-08-31" },
      previous_period: { start: "2026-07-01", end: "2026-07-31" },
      same_period_last_year: { start: "2025-08-01", end: "2025-08-31" },
    });
    expect(d.window).toBe("Aug 1 – 31, 2026 (31 days)");
    expect(d.previous).toBe("Jul 1 – 31, 2026 (31 days)");
    expect(d.yoy).toBe("Aug 1 – 31, 2025 (31 days)");
    expect(describeWindow({ start: "2026-09-18", end: "2026-09-18" })).toMatch(/\(1 day\)$/);
  });

  it("labels fiscal years", () => {
    expect(fiscalYearLabel(1)).toMatch(/calendar year/);
    expect(fiscalYearLabel(10)).toBe("Fiscal year runs October – September");
    expect(fiscalYearLabel(2)).toBe("Fiscal year runs February – January");
  });
});

describe("formatUsd", () => {
  it("formats small and large costs", () => {
    expect(formatUsd(0.004)).toBe("<$0.01");
    expect(formatUsd(1.234)).toBe("$1.23");
    expect(formatUsd(250)).toBe("$250");
    expect(formatUsd(null)).toBe("n/a");
  });
});
