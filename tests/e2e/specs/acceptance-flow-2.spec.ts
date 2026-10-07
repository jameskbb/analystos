import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

/**
 * Acceptance flow 2 (spec §97), with the literal question: "Revenue was roughly flat. Why did
 * margin decline?" AnalystOS must ask which margin is meant, check the stated premise against
 * the data (and pick a period where it holds, since none is named), decompose the Gross Margin %
 * change into unit cost and discounting effects, investigate product/customer/region mix, and keep
 * hypotheses visibly separate from supported explanations.
 *
 * Expected magnitudes come from the demo's answer key (scenarios.json, written by `make demo`):
 * the Gross Margin % decline and its unit cost and discounting effects, Q2 2026 vs Q2 2025.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
// The repository's demo cache (`make demo`), else the copy the E2E API generated when it loaded the demo.
const SCENARIO_PATHS = [
  resolve(HERE, "../../../data/demo/summit-supply-seed42/scenarios.json"),
  resolve(HERE, "../.servers/data/demo/summit-supply-seed42/scenarios.json"),
];
type MarginKey = { headline: { gross_margin_pct_change_pp: number }; attribution_pp: { unit_cost_inflation: number; discounting: number } };
function marginKey(): MarginKey {
  const path = SCENARIO_PATHS.find((p) => existsSync(p));
  if (!path) throw new Error(`no demo answer key at ${SCENARIO_PATHS.join(" or ")}; run make demo`);
  return (JSON.parse(readFileSync(path, "utf8")) as { stories: { margin_compression_q2_2026: MarginKey } }).stories.margin_compression_q2_2026;
}
/**
 * A pp magnitude as the tree prints it (one decimal), as a RegExp alternation that accepts the answer
 * key's value within 0.1 pp: the engine's counterfactual and the answer key's are computed separately
 * and can land either side of a rounding boundary.
 */
const pp = (v: number) =>
  `(?:${[-0.1, 0, 0.1].map((d) => (Math.round(Math.abs(v) * 10) / 10 + d).toFixed(1).replace(".", "\\.")).join("|")})`;

const QUESTION = "Revenue was roughly flat. Why did margin decline?";

function treeItem(page: Page, text: RegExp) {
  return page.getByRole("tree", { name: "Investigation tree" }).getByRole("treeitem", { name: text }).first();
}

test("acceptance flow 2: revenue was roughly flat, why did margin decline", async ({ page }) => {
  test.setTimeout(600_000);

  await test.step("open the Summit Supply workspace (loading the demo if this runs alone)", async () => {
    await page.goto("/");
    const load = page.getByRole("button", { name: /Load Summit Supply demo/ }).first();
    // Either the welcome screen (fresh server) or the workspace shell (demo already loaded).
    await expect(load.or(page.getByRole("navigation", { name: "Primary" }))).toBeVisible({ timeout: 60_000 });
    if (await load.isVisible()) {
      await load.click();
      await expect(page.getByText("Summit Supply demo loaded").first()).toBeVisible({ timeout: 240_000 });
    }
    await expect(page).toHaveURL(/\/w\/[^/]+/, { timeout: 60_000 });
  });

  const ws = page.url().match(/\/w\/([^/?#]+)/)![1];

  await test.step("ask the literal question and choose which margin", async () => {
    await page.goto(`/w/${ws}/investigate?new=1`);
    await page.getByLabel("Ask a business question").fill(QUESTION);
    await page.getByRole("button", { name: /^Investigate/ }).click();
    await expect(page).toHaveURL(/\/investigate\/[^/]+/, { timeout: 60_000 });
    // "margin" matches several governed metrics: AnalystOS asks instead of choosing silently (§15).
    const ambiguous = page.getByRole("group", { name: "Ambiguous terms" });
    await expect(ambiguous).toContainText("“margin” matches several metrics");
    await ambiguous.getByRole("button", { name: "Gross Margin %" }).click();
  });

  await test.step("the premise is checked, not assumed, and the period is chosen so it holds", async () => {
    const premises = page.getByRole("group", { name: "Premises" });
    await expect(premises).toContainText("revenue was roughly flat", { timeout: 60_000 });
    await expect(premises).toContainText(/No period was named, so AnalystOS chose Q2 2026/);
    // August vs July was considered and rejected: revenue fell 11.8% there, so it is not "flat".
    await expect(premises).toContainText(/premise does not hold/);
    const run = page.getByRole("button", { name: /^Run \d+ steps?/ });
    await expect(page.getByRole("list", { name: "Plan steps" })).toContainText("isolate cost inflation");
    await run.click();
  });

  await test.step("the tree decomposes Gross Margin % into unit cost and discounting", async () => {
    const KEY = marginKey();
    await expect(treeItem(page, new RegExp(`Gross Margin % declined ${pp(KEY.headline.gross_margin_pct_change_pp)} pp`))).toBeVisible({ timeout: 240_000 });
    await expect(treeItem(page, /Premise check \('revenue was roughly flat'\).*premise holds/)).toBeVisible();
    await expect(treeItem(page, new RegExp(`Unit cost changes lowered Gross Margin % by ${pp(KEY.attribution_pp.unit_cost_inflation)} pp`))).toBeVisible();
    await expect(treeItem(page, new RegExp(`Discounting lowered Gross Margin % by ${pp(KEY.attribution_pp.discounting)} pp`))).toBeVisible();
    // No circular "Gross Margin explains Gross Margin %" driver.
    await expect(page.getByRole("treeitem", { name: /Gross Margin explains/ })).toHaveCount(0);
  });

  await test.step("product, customer and region mix are investigated", async () => {
    await expect(treeItem(page, /Gross Margin % change by Product Category/)).toBeVisible();
    await expect(treeItem(page, /Gross Margin % change by Customer Segment/)).toBeVisible();
    await expect(treeItem(page, /Gross Margin % change by Region/)).toBeVisible();
    await expect(treeItem(page, /Product Category = Lumber/)).toBeVisible();
  });

  await test.step("supported drivers show their evidence and calculation", async () => {
    await treeItem(page, /Unit cost changes lowered Gross Margin %/).click();
    await expect(page.getByText("Supported explanation").first()).toBeVisible();
    await page.getByRole("tab", { name: "SQL" }).first().click();
    await expect(page.getByText(/SELECT/).first()).toBeVisible();
  });

  await test.step("hypotheses are kept separate from confirmed explanations", async () => {
    await treeItem(page, /Hypothesis \(not tested\)/).click();
    await expect(page.getByText("This is a hypothesis. No executed test confirms it; do not report it as a fact.")).toBeVisible();
  });
});
