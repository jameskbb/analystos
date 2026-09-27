import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { CommandResult } from "@/lib/api/types";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => "/w/ws1/investigate/inv1",
}));

const command = vi.fn<(...a: unknown[]) => Promise<CommandResult>>();
vi.mock("@/lib/api/endpoints", async (orig) => {
  const actual = await orig<typeof import("@/lib/api/endpoints")>();
  return {
    ...actual,
    workspaces: { ...actual.workspaces, get: vi.fn(async () => ({ id: "ws1", name: "WS", role: "owner" })) },
    investigations: { ...actual.investigations, command: (...a: unknown[]) => command(...a) },
  };
});

import { CommandBar, commandEffect } from "./command-bar";
import { WorkspaceProvider } from "@/components/providers/workspace";

const href = (p: string) => `/w/ws1${p}`;
const base: CommandResult = { command: { kind: "breakdown" }, message: "ok", action: "none" };

describe("commandEffect maps every API action", () => {
  it("navigates to a branched or newly created investigation", () => {
    expect(commandEffect({ ...base, action: "branched", investigation_id: "inv2" }, href, "inv1")).toEqual({
      kind: "navigate",
      href: "/w/ws1/investigate/inv2",
    });
    expect(commandEffect({ ...base, action: "created_investigation", investigation_id: "inv3" }, href, null)).toEqual({
      kind: "navigate",
      href: "/w/ws1/investigate/inv3",
    });
  });
  it("refreshes the tree and selects the top segment after a drill", () => {
    const res: CommandResult = { ...base, action: "drilled", investigation_id: "inv1", items: [{ node_id: "n7" }, { node_id: "n8" }] };
    expect(commandEffect(res, href, "inv1")).toEqual({ kind: "select", nodeId: "n7", refresh: true });
    expect(commandEffect({ ...base, action: "drilled", investigation_id: "inv1" }, href, "inv1")).toEqual({ kind: "refresh" });
  });
  it("refreshes after node updates and reruns", () => {
    expect(commandEffect({ ...base, action: "node_updated" }, href, "inv1")).toEqual({ kind: "refresh" });
    expect(commandEffect({ ...base, action: "rerun_started", job_id: "j1" }, href, "inv1")).toEqual({ kind: "refresh" });
  });
  it("links a saved finding", () => {
    expect(commandEffect({ ...base, action: "saved_finding", finding_id: "f1" }, href, "inv1")).toEqual({
      kind: "link",
      href: "/w/ws1/findings/f1",
      label: "Open finding",
      refresh: true,
    });
  });
  it("opens the SQL of the first artifact that resolves", () => {
    const res: CommandResult = {
      ...base,
      action: "show_sql",
      sql: [{ artifact_id: null, sql: "select 1" }, { artifact_id: "a2", sql: "select 2" }],
    };
    expect(commandEffect(res, href, "inv1")).toEqual({ kind: "sql", artifactId: "a2" });
  });
  it("navigates to built reports and dashboards", () => {
    expect(commandEffect({ ...base, action: "built_report", report_id: "r1" }, href, "inv1")).toEqual({
      kind: "navigate",
      href: "/w/ws1/reports/r1",
    });
    expect(commandEffect({ ...base, action: "built_dashboard", dashboard_id: "d1" }, href, "inv1")).toEqual({
      kind: "navigate",
      href: "/w/ws1/dashboards/d1",
    });
  });
  it("does nothing for clarify", () => {
    expect(commandEffect({ ...base, action: "clarify" }, href, "inv1")).toEqual({ kind: "none" });
  });
});

function renderBar(onSelectNode = vi.fn(), onShowSql = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <WorkspaceProvider id="ws1">
        <CommandBar investigationId="inv1" nodeId="root" onSelectNode={onSelectNode} onShowSql={onShowSql} />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
  return { invalidate, onSelectNode, onShowSql };
}

describe("CommandBar acts on the response", () => {
  beforeEach(() => {
    push.mockReset();
    command.mockReset();
  });
  it("routes to the branch for 'compare to last year'", async () => {
    command.mockResolvedValue({ ...base, message: "Branched", action: "branched", investigation_id: "inv9" });
    renderBar();
    await userEvent.type(screen.getByLabelText("Investigation command"), "compare to last year{enter}");
    await waitFor(() => expect(push).toHaveBeenCalledWith("/w/ws1/investigate/inv9"));
  });
  it("invalidates the investigation and selects the new node for 'break this down by branch'", async () => {
    command.mockResolvedValue({ ...base, message: "Broken down", action: "drilled", investigation_id: "inv1", items: [{ node_id: "n5" }] });
    const { invalidate, onSelectNode } = renderBar();
    await userEvent.type(screen.getByLabelText("Investigation command"), "break this down by branch{enter}");
    await waitFor(() => expect(onSelectNode).toHaveBeenCalledWith("n5"));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["ws", "ws1", "investigations", "inv1"] });
  });
  it("shows a link to the saved finding", async () => {
    command.mockResolvedValue({ ...base, message: "Saved as a finding: X", action: "saved_finding", finding_id: "f3" });
    renderBar();
    await userEvent.type(screen.getByLabelText("Investigation command"), "save this as a finding{enter}");
    const link = await screen.findByRole("link", { name: "Open finding" });
    expect(link).toHaveAttribute("href", "/w/ws1/findings/f3");
  });
  it("renders clarify replies as not-ok and does not navigate", async () => {
    command.mockResolvedValue({ ...base, message: "Which metric?", action: "clarify" });
    renderBar();
    await userEvent.type(screen.getByLabelText("Investigation command"), "use margin{enter}");
    expect(await screen.findByRole("status")).toHaveTextContent("Which metric?");
    expect(push).not.toHaveBeenCalled();
  });
});
