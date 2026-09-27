import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CommandPalette, buildDefaultActions, hitPath, scoreCommand } from "./command-palette";
import type { SearchHit } from "@/lib/api/types";

function setup(search?: (q: string) => Promise<SearchHit[]>) {
  const navigate = vi.fn();
  const onAsk = vi.fn();
  const toggleTheme = vi.fn();
  const actions = buildDefaultActions({ navigate, toggleTheme, openShortcuts: vi.fn() });
  render(
    <CommandPalette
      open
      onOpenChange={() => {}}
      actions={actions}
      onNavigate={navigate}
      onAsk={onAsk}
      search={search ? (q) => search(q) : undefined}
    />,
  );
  return { navigate, onAsk, toggleTheme };
}

describe("command palette", () => {
  it("lists create and navigation commands", () => {
    setup();
    expect(screen.getByText("New investigation")).toBeInTheDocument();
    expect(screen.getByText("Go to Findings")).toBeInTheDocument();
  });

  it("filters commands by prefix and keywords", async () => {
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText("Search or type a command"), "pivot");
    expect(screen.getByText("Explore a table")).toBeInTheDocument();
    expect(screen.queryByText("Go to Findings")).not.toBeInTheDocument();
  });

  it("runs a command on Enter", async () => {
    const user = userEvent.setup();
    const { navigate } = setup();
    await user.type(screen.getByLabelText("Search or type a command"), "go to find");
    await user.keyboard("{Enter}");
    expect(navigate).toHaveBeenCalledWith("/findings");
  });

  it("shows workspace search hits and navigates to them", async () => {
    const user = userEvent.setup();
    const search = vi.fn(async () => [
      { id: "m1", kind: "metric", title: "Revenue", subtitle: "SUM(net_amount)" },
      { id: "f1", kind: "finding", title: "Dallas accounted for 54% of the decline" },
    ]);
    const { navigate } = setup(search);
    await user.type(screen.getByLabelText("Search or type a command"), "rev");
    expect(await screen.findByText("Revenue")).toBeInTheDocument();
    await user.click(screen.getByText("Dallas accounted for 54% of the decline"));
    expect(navigate).toHaveBeenCalledWith("/findings/f1");
    await waitFor(() => expect(search).toHaveBeenCalledWith("rev"));
  });

  it("offers to investigate a free-text question", async () => {
    const user = userEvent.setup();
    const { onAsk } = setup();
    await user.type(screen.getByLabelText("Search or type a command"), "Why was August revenue down?");
    await user.click(screen.getByText(/Investigate:/));
    expect(onAsk).toHaveBeenCalledWith("Why was August revenue down?");
  });
});

describe("palette helpers", () => {
  it("scores prefix above substring", () => {
    expect(scoreCommand("dash", "Go to Dashboards")).toBe(2);
    expect(scoreCommand("go", "Go to Dashboards")).toBe(3);
    expect(scoreCommand("zzz", "Go to Dashboards")).toBe(0);
  });

  it("maps hits to workspace routes", () => {
    expect(hitPath({ id: "x", kind: "dataset", title: "orders" })).toBe("/data/x");
    expect(hitPath({ id: "x", kind: "column", title: "net_amount", parent_id: "d1" })).toContain("/data/d1?tab=schema");
    expect(hitPath({ id: "q", kind: "saved_query", title: "Top SKUs" })).toBe("/sql?saved=q");
  });
});
