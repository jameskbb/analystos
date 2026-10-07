// Captures the README screenshots (docs/images/*.png) from a running AnalystOS with a fresh data dir.
//   bash tests/e2e/scripts/serve.sh &      # or any running stack
//   node tests/e2e/scripts/screenshots.mjs http://127.0.0.1:13765
// Loads the Summit Supply demo, runs the flagship CEO question from the home page's plan comparison
// (plan, bridge and tree, related signal, confirmed parts, operating review memo), then the August
// revenue and margin questions, and saves viewport PNGs.
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const base = process.argv[2] ?? "http://127.0.0.1:13765";
const out = resolve(dirname(fileURLToPath(import.meta.url)), "../../../docs/images");
mkdirSync(out, { recursive: true });

// Same rendering flags as playwright.config.ts (SwiftShader produces no frames on some hosts).
const browser = await chromium.launch({ args: ["--disable-gpu", "--disable-gpu-compositing", "--disable-software-rasterizer"] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
page.setDefaultTimeout(60_000);
const click = (loc) => loc.click();
const shot = async (name) => {
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${out}/${name}.png` });
  console.log("saved", name);
};
const tree = (re) => page.getByRole("tree", { name: "Investigation tree" }).getByRole("treeitem", { name: re }).first();
const api = async (path) => (await page.request.get(`${base}/api/v1${path}`)).json();

await page.goto(base);
const load = page.getByRole("button", { name: /Load Summit Supply demo/ }).first();
await load.or(page.getByRole("navigation", { name: "Primary" })).waitFor();
if (await load.isVisible()) {
  await click(load);
  await page.waitForURL(/\/w\/[^/]+/, { timeout: 240_000 });
}
const ws = page.url().match(/\/w\/([^/?#]+)/)[1];
await page.getByRole("note", { name: "Plan contradiction" }).waitFor({ timeout: 120_000 });
// Let the "demo loaded" toast go before the shot.
await page.waitForTimeout(6000);
await shot("home");

await page.goto(`${base}/w/${ws}/data`);
await page.getByRole("link", { name: "Orders", exact: true }).waitFor();
await shot("data");

// The flagship: "Investigate why" on the home page's plan comparison asks the CEO question.
await page.goto(`${base}/w/${ws}`);
await click(page.getByRole("button", { name: /Investigate why/ }));
await page.waitForURL(/\/investigate\/[^/]+/);
const runPlan = page.getByRole("button", { name: /^Run \d+ steps?/ });
await runPlan.waitFor();
await shot("ceo-plan");
await click(runPlan);
await page.getByTestId("premise-card").waitFor({ timeout: 240_000 });
const inv = page.url().match(/\/investigate\/([^/?#]+)/)[1];
// The first screen: the bridge above the tree, which is already open down to the operational causes.
// Select the supplier the unit-cost drill found and show how its effect was split.
await page.getByTestId("bridge-chart").first().waitFor();
// The supplier segment: a below-plan-grain split names its parent effect and segment ("... of unit-cost inflation in Lumber").
await click(tree(/of unit-cost inflation in /));
await click(page.getByRole("tab", { name: "Calculation" }));
await shot("ceo-investigation");

// The related signal, apart from the tree, with the SQL that measured it.
await click(page.getByTestId("related-signals").getByRole("button").first());
await page.getByRole("note", { name: "Related signal" }).waitFor();
await click(page.getByRole("tab", { name: "SQL" }).first());
await shot("ceo-signal");

// Confirm the root, the premise, the reconciled parts, their parts and the signal (the review workflow),
// then build the report: its operating review is written from the confirmed nodes only.
const doc = await api(`/workspaces/${ws}/investigations/${inv}`);
const nodes = doc.tree.nodes;
const recon = nodes.find((n) => n.reconciliation);
const parts = recon.reconciliation.identities[0].components.map((c) => c.node_id);
const ids = [
  doc.tree.root_id,
  nodes.find((n) => n.role === "premise").id,
  ...parts,
  ...nodes.filter((n) => parts.includes(n.parent_id) && n.root_effect != null && !n.role && n.kind !== "dimension").map((n) => n.id),
  doc.related_signals[0].node_id,
];
const toolbar = page.getByRole("toolbar", { name: "Node actions" });
for (const id of ids) {
  await click(page.locator(`[role=treeitem][data-node-id="${id}"], [data-testid=related-signals] [data-node-id="${id}"]`).first());
  await click(toolbar.getByRole("button", { name: "More node actions" }));
  await click(page.getByRole("menuitem", { name: "Confirm" }));
  await page.locator(`[data-node-id="${id}"]`).first().waitFor();
  await page.waitForTimeout(400);
}
const command = page.getByLabel("Investigation command");
await command.fill("build me a report");
await command.press("Enter");
await page.waitForURL(/\/reports\/[^/]+/);
await click(page.getByRole("radio", { name: "Read" }));
await page.getByTestId("block-operating-review").first().waitFor();
// Let the "Node confirmed" toasts go before the shot.
await page.locator("[data-sonner-toast]").first().waitFor({ state: "detached", timeout: 30_000 }).catch(() => {});
await page.waitForTimeout(4000);
await shot("ceo-review");

// The smaller example: why was August revenue down?
await page.goto(`${base}/w/${ws}/investigate?new=1`);
await page.getByLabel("Ask a business question").fill("Why was August revenue down?");
await click(page.getByRole("button", { name: /^Investigate/ }));
await page.waitForURL(/\/investigate\/[^/]+/);
const runAug = page.getByRole("button", { name: /^Run \d+ steps?/ });
await runAug.or(tree(/Revenue declined/)).waitFor();
if (await runAug.isVisible()) await click(runAug);
await tree(/Revenue declined 11\.8%/).waitFor({ timeout: 180_000 });
await click(tree(/Branch = Dallas: Revenue/));
await page.getByRole("img", { name: /^Chart: / }).first().waitFor();
await click(page.getByRole("tab", { name: "Calculation" }));
await page.getByRole("table", { name: "Segment effects" }).waitFor();
await shot("investigation");

// The literal margin question.
await page.goto(`${base}/w/${ws}/investigate?new=1`);
await page.getByLabel("Ask a business question").fill("Revenue was roughly flat. Why did margin decline?");
await click(page.getByRole("button", { name: /^Investigate/ }));
await page.waitForURL(/\/investigate\/[^/]+/);
await click(page.getByRole("group", { name: "Ambiguous terms" }).getByRole("button", { name: "Gross Margin %" }));
await page.getByRole("group", { name: "Premises" }).waitFor();
await click(page.getByRole("button", { name: /^Run \d+ steps?/ }));
await tree(/Unit cost changes lowered Gross Margin %/).waitFor({ timeout: 240_000 });
await click(tree(/Unit cost changes lowered Gross Margin %/));
await shot("margin-investigation");

await browser.close();
