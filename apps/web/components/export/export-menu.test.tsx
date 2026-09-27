import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const download = vi.fn();
const request = vi.fn();
vi.mock("@/lib/api/client", async (orig) => {
  const actual = await orig<typeof import("@/lib/api/client")>();
  return { ...actual, downloadFromApi: (...a: unknown[]) => download(...a), request: (...a: unknown[]) => request(...a) };
});
const getInstanceByDom = vi.fn();
vi.mock("echarts/core", async (orig) => ({ ...(await orig<typeof import("echarts/core")>()), getInstanceByDom: (el: unknown) => getInstanceByDom(el) }));
vi.mock("@/lib/api/endpoints", async (orig) => {
  const actual = await orig<typeof import("@/lib/api/endpoints")>();
  return { ...actual, workspaces: { ...actual.workspaces, get: vi.fn(async () => ({ id: "ws1", name: "WS", role: "owner" })) } };
});

import { ApiError } from "@/lib/api/client";
import { ExportMenu } from "./export-menu";
import { captureCharts, dataUrlToBlob } from "@/components/charts/capture";
import { WorkspaceProvider } from "@/components/providers/workspace";

// Radix toggles `pointer-events` on <body> while menus open and close; the check adds nothing here.
const user = () => userEvent.setup({ pointerEventsCheck: 0 });

function renderMenu(beforeExport?: () => Promise<void>) {
  const qc = new QueryClient();
  render(
    <QueryClientProvider client={qc}>
      <WorkspaceProvider id="ws1">
        <ExportMenu target={{ kind: "report", id: "r1" }} formats={["pdf", "html", "md"]} filename="Report" beforeExport={beforeExport} />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
}

describe("ExportMenu", () => {
  beforeEach(() => {
    download.mockReset();
    request.mockReset();
    document.querySelectorAll("iframe").forEach((f) => f.remove());
    // Radix leaves `pointer-events: none` on <body> if a test ends while its menu animates closed.
    document.body.style.pointerEvents = "";
  });

  it("uploads chart images before HTML/PDF exports but not Markdown", async () => {
    const before = vi.fn(async () => {});
    download.mockResolvedValue(undefined);
    renderMenu(before);
    await user().click(screen.getByRole("button", { name: /Export options/ }));
    await user().click(await screen.findByRole("menuitem", { name: "HTML" }));
    await waitFor(() => expect(download).toHaveBeenCalledTimes(1));
    expect(before).toHaveBeenCalledTimes(1);
    await user().click(screen.getByRole("button", { name: /Export options/ }));
    await user().click(await screen.findByRole("menuitem", { name: "Markdown" }));
    await waitFor(() => expect(download).toHaveBeenCalledTimes(2));
    expect(before).toHaveBeenCalledTimes(1);
  });

  it("falls back to printing the HTML export when the server has no PDF engine", async () => {
    download.mockRejectedValue(new ApiError(409, "PDF export needs WeasyPrint", null, "pdf_unavailable", { fallback: "html" }));
    request.mockResolvedValue(new Response("<html><body><h1>Report</h1></body></html>", { headers: { "Content-Type": "text/html" } }));
    renderMenu();
    // Open with the keyboard (menus are keyboard-first; this also avoids jsdom pointer quirks).
    screen.getByRole("button", { name: /Export options/ }).focus();
    await user().keyboard("{Enter}");
    await user().click(await screen.findByRole("menuitem", { name: "PDF" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    expect(request.mock.calls[0][1]).toMatchObject({ method: "POST", body: { target: "report", id: "r1", format: "html" }, raw: true });
    await waitFor(() => expect(document.querySelector("iframe")?.getAttribute("srcdoc")).toContain("<h1>Report</h1>"));
  });
});

describe("chart capture", () => {
  it("decodes data URLs to PNG blobs", async () => {
    const blob = dataUrlToBlob("data:image/png;base64,iVBORw0KGgo=");
    expect(blob.type).toBe("image/png");
    expect(blob.size).toBe(8);
  });

  it("captures each marked chart once, keyed by block or tile id", () => {
    document.body.innerHTML = `
      <figure data-capture-key="b1" data-capture-title="Revenue by branch"><div _echarts_instance_="ec_1"></div></figure>
      <figure data-capture-key="b2"><p>table only</p></figure>
      <div data-capture-key="t9" _echarts_instance_="ec_2"></div>`;
    getInstanceByDom.mockImplementation(() => ({ getDataURL: () => "data:image/png;base64,AAAA" }));
    const out = captureCharts(document, "#fff");
    expect(out.map((c) => [c.key, c.title])).toEqual([
      ["b1", "Revenue by branch"],
      ["t9", "t9"],
    ]);
    document.body.innerHTML = "";
  });
});
