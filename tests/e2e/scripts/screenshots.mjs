// Captures the README screenshots (docs/images/*.png) from a running AnalystOS with a fresh data dir.
//   bash tests/e2e/scripts/serve.sh &      # or any running stack
//   node tests/e2e/scripts/screenshots.mjs http://127.0.0.1:13765
// Loads the Summit Supply demo, runs the two acceptance questions and an Analyze forecast, and
// saves viewport PNGs.
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

await page.goto(base);
const load = page.getByRole("button", { name: /Load Summit Supply demo/ }).first();
await load.or(page.getByRole("navigation", { name: "Primary" })).waitFor();
if (await load.isVisible()) {
  await click(load);
  await page.waitForURL(/\/w\/[^/]+/, { timeout: 240_000 });
}
const ws = page.url().match(/\/w\/([^/?#]+)/)[1];
await page.getByText("Summit Supply Co.").first().waitFor();
await shot("home");

await page.goto(`${base}/w/${ws}/data`);
await page.getByRole("link", { name: "Orders", exact: true }).waitFor();
await shot("data");

// Flow 1: why was August revenue down?
await page.goto(`${base}/w/${ws}/investigate?new=1`);
await page.getByLabel("Ask a business question").fill("Why was August revenue down?");
await click(page.getByRole("button", { name: /^Investigate/ }));
await page.waitForURL(/\/investigate\/[^/]+/);
const runPlan = page.getByRole("button", { name: /^Run \d+ steps?/ });
await runPlan.or(tree(/Revenue declined/)).waitFor();
await shot("plan");
if (await runPlan.isVisible()) await click(runPlan);
await tree(/Revenue declined 11\.8%/).waitFor({ timeout: 180_000 });
await click(tree(/Branch = Dallas: Revenue/));
await page.getByRole("img", { name: /^Chart: / }).first().waitFor();
await click(page.getByRole("tab", { name: "Calculation" }));
await page.getByRole("table", { name: "Segment effects" }).waitFor();
await shot("investigation");
await click(page.getByRole("tab", { name: "SQL" }).first());
await shot("investigation-sql");

// Flow 2: the literal margin question.
await page.goto(`${base}/w/${ws}/investigate?new=1`);
await page.getByLabel("Ask a business question").fill("Revenue was roughly flat. Why did margin decline?");
await click(page.getByRole("button", { name: /^Investigate/ }));
await page.waitForURL(/\/investigate\/[^/]+/);
await click(page.getByRole("group", { name: "Ambiguous terms" }).getByRole("button", { name: "Gross Margin %" }));
await page.getByRole("group", { name: "Premises" }).waitFor();
await shot("margin-plan");
await click(page.getByRole("button", { name: /^Run \d+ steps?/ }));
await tree(/Unit cost changes lowered Gross Margin %/).waitFor({ timeout: 240_000 });
await click(tree(/Unit cost changes lowered Gross Margin %/));
await shot("margin-investigation");

// Analyze: weekly revenue forecast with backtests.
await page.goto(`${base}/w/${ws}/analyze?tab=forecast&metric=revenue`);
await click(page.getByRole("button", { name: "Run forecast" }));
await page.getByRole("table", { name: "Backtest accuracy" }).waitFor({ timeout: 120_000 });
await shot("analyze-forecast");

await browser.close();
