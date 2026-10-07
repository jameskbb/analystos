import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

/**
 * The flagship flow: the CEO of Summit Supply Co. asks "We made our sales number last quarter, but we
 * missed our target for net profit. What happened?" From the home page's plan comparison, AnalystOS must
 * start that investigation, read "net profit" as Operating Profit through the glossary and compare it
 * against plan for Q2 2026, verify
 * the revenue premise from the data, measure the profit gap, explain it with a tree that reconciles
 * exactly, keep the Gulf Coast lead-response signal apart as "not established", let the analyst confirm
 * a finding, reproduce the result on a re-run, and build the business review from what was accepted.
 *
 * No number is typed into this file. Expected values come from the demo's answer key (scenarios.json,
 * measured from the generated files with DuckDB) and from the investigation the API returns.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
// The repository's demo cache (`make demo`), else the copy the E2E API generated when it loaded the demo.
const SCENARIO_PATHS = [
  resolve(HERE, "../../../data/demo/summit-supply-seed42/scenarios.json"),
  resolve(HERE, "../.servers/data/demo/summit-supply-seed42/scenarios.json"),
];

type Json = Record<string, unknown>;
interface Story {
  questions: string[];
  headline: {
    revenue_actual: number;
    revenue_plan: number;
    revenue_variance: number;
    revenue_variance_pct: number;
    operating_profit_actual: number;
    operating_profit_plan: number;
    operating_profit_variance: number;
    operating_profit_variance_pct: number;
  };
  premise: { verdict: string };
  bridge: {
    gross_margin: { variance: number };
    operating_expenses: { variance: number; effect_on_operating_profit: number };
    reconciles: boolean;
  };
  related_signals: { id: string; metric: string; filters: Record<string, string>; relationship_to_gap: string }[];
  gross_margin_bridge: { unit_cost_effect: number; discount_effect: number; mix_effect: number };
  freight: { freight_cost: { variance: number }; fuel_surcharge: { variance: number }; fuel_surcharge_share_of_overrun: number };
  labor: { labor_cost: { variance: number }; by_branch: { branch: string; payroll_variance: number; overtime_variance: number }[] };
  operating_expense_groups: { other_lines_variance: number };
  lumber_unit_cost_by_supplier: { supplier: string; effect: number; share: number }[];
}

interface ApiBridge {
  start: number;
  end: number;
  gap: number;
  reconciled: boolean;
  steps: { node_id: string | null; label: string; amount: number; group_label: string }[];
}

/** The flagship story from the answer key (written by `make demo`, which the E2E stack reuses). */
function story(): Story {
  const path = SCENARIO_PATHS.find((p) => existsSync(p));
  if (!path) throw new Error(`no demo answer key at ${SCENARIO_PATHS.join(" or ")}; run make demo`);
  const all = JSON.parse(readFileSync(path, "utf8")) as { stories: Record<string, Json> };
  const s = all.stories.operating_profit_miss_q2_2026;
  if (!s) throw new Error(`no operating_profit_miss_q2_2026 story in ${path}; run make demo`);
  return s as unknown as Story;
}

/** The app's compact currency format (apps/web/lib/format.ts formatCompact + formatSignedChange). */
function money(n: number, signed = false): string {
  const abs = Math.abs(n);
  const trim = (x: number) => {
    const a = Math.abs(x);
    const f = a >= 100 ? x.toFixed(0) : a >= 10 ? x.toFixed(1) : x.toFixed(2);
    return f.includes(".") ? f.replace(/\.?0+$/, "") : f;
  };
  const body = abs >= 1e9 ? `${trim(abs / 1e9)}B` : abs >= 1e6 ? `${trim(abs / 1e6)}M` : abs >= 1e4 ? `${trim(abs / 1e3)}K` : Math.round(abs).toLocaleString("en-US");
  if (signed) return `${n > 0 ? "+" : n < 0 ? "−" : ""}$${body}`;
  return `${n < 0 ? "-" : ""}$${body}`;
}

function treeItem(page: Page, text: RegExp) {
  return page.getByRole("tree", { name: "Investigation tree" }).getByRole("treeitem", { name: text }).first();
}

function investigationId(page: Page): string {
  const m = page.url().match(/\/investigate\/([^/?#]+)/);
  if (!m) throw new Error(`not on an investigation: ${page.url()}`);
  return m[1];
}

interface ApiNode {
  id: string;
  kind?: string | null;
  parent_id: string | null;
  statement: string;
  title?: string | null;
  role?: string | null;
  metric_id?: string | null;
  current?: number | null;
  baseline?: number | null;
  abs_change?: number | null;
  root_effect?: number | null;
  offset_node_id?: string | null;
  status: string;
  reconciliation?: { reconciled: boolean; gap: number } | null;
}

test("flagship: we made our sales number but missed our target for net profit; what happened", async ({ page }) => {
  test.setTimeout(900_000);

  await test.step("open the Summit Supply workspace (loading the demo if this runs alone)", async () => {
    await page.goto("/");
    const load = page.getByRole("button", { name: /Load Summit Supply demo/ }).first();
    await expect(load.or(page.getByRole("navigation", { name: "Primary" }))).toBeVisible({ timeout: 60_000 });
    if (await load.isVisible()) {
      await load.click();
      await expect(page.getByText("Summit Supply demo loaded").first()).toBeVisible({ timeout: 240_000 });
    }
    await expect(page).toHaveURL(/\/w\/[^/]+/, { timeout: 60_000 });
  });

  const ws = page.url().match(/\/w\/([^/?#]+)/)![1];
  // Read after the demo is loaded: on a fresh machine the API wrote the answer key while loading it.
  const s = story();

  await test.step("home shows revenue on plan and operating profit below plan, from executed queries", async () => {
    await page.goto(`/w/${ws}`);
    const note = page.getByRole("note", { name: "Plan contradiction" });
    await expect(note).toBeVisible({ timeout: 60_000 });
    await expect(note).toContainText(/Revenue finished on plan in Q2 2026/);
    await expect(note).toContainText(`Operating Profit missed plan`);
    await expect(note).toContainText(money(s.headline.operating_profit_variance, true));
    const tiles = page.getByTestId("plan-tile");
    await expect(tiles.filter({ hasText: "Revenue" }).first()).toHaveAttribute("data-status", "on_plan");
    await expect(tiles.filter({ hasText: "Operating Profit" }).first()).toHaveAttribute("data-status", "unfavorable");
  });

  await test.step("Investigate why starts the investigation of the CEO question", async () => {
    // The home page composes the question from the plan comparison; it is the answer key's CEO question.
    await expect(page.getByRole("button", { name: /Investigate why/ })).toHaveAttribute("title", s.questions[0]);
    await page.getByRole("button", { name: /Investigate why/ }).click();
    await expect(page).toHaveURL(/\/investigate\/[^/]+/, { timeout: 60_000 });
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(s.questions[0]);
  });

  await test.step("the interpretation is governed and the premise is checked, not assumed", async () => {
    const run = page.getByRole("button", { name: /^Run \d+ steps?/ });
    await expect(run).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("How AnalystOS read your question")).toBeVisible();
    await expect(page.getByText("Operating Profit").first()).toBeVisible();
    const premises = page.getByRole("group", { name: "Premises" });
    await expect(premises).toContainText(/sales number/i);
    // "net profit" is not a metric name: it resolves to Operating Profit through a glossary term, shown with its definition.
    const resolved = page.getByRole("group", { name: "Resolved terms" });
    await expect(resolved).toContainText("“net profit”");
    await expect(resolved).toContainText("Operating Profit");
    await expect(resolved).toContainText("glossary term “Net Profit”");
    await expect(premises).toContainText(/checked against the data, not taken as given/);
    // The plan shows the reconciliation step before anything runs.
    await expect(page.getByRole("list", { name: "Plan steps" })).toContainText(/Reconcile/i);
    await run.click();
  });

  let nodes: ApiNode[] = [];
  let bridge: ApiBridge | null = null;
  const inv = await test.step("the premise is supported and the headline gap is the answer key's", async () => {
    const premise = page.getByTestId("premise-card");
    await expect(premise).toBeVisible({ timeout: 240_000 });
    expect(s.premise.verdict).toBe("supported");
    await expect(premise).toContainText("Supported by the data");
    await expect(premise).toContainText(money(s.headline.revenue_actual));
    await expect(premise).toContainText(money(s.headline.revenue_plan));
    await expect(page.getByTestId("bridge-gap")).toContainText(money(s.headline.operating_profit_variance, true));

    const id = investigationId(page);
    const res = await page.request.get(`/api/v1/workspaces/${ws}/investigations/${id}`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    nodes = body.tree.nodes as ApiNode[];
    bridge = body.bridge as ApiBridge;
    const root = nodes.find((n) => n.id === body.tree.root_id)!;
    expect(root.metric_id).toBe("operating_profit");
    // The tree's numbers are the answer key's, to the cent.
    expect(root.current).toBeCloseTo(s.headline.operating_profit_actual, 2);
    expect(root.baseline).toBeCloseTo(s.headline.operating_profit_plan, 2);
    return id;
  });

  await test.step("the bridge walks from plan to actual; its steps sum to the gap and are the answer key's", async () => {
    const b = bridge!;
    expect(b.reconciled).toBe(true);
    expect(b.start).toBeCloseTo(s.headline.operating_profit_plan, 2);
    expect(b.end).toBeCloseTo(s.headline.operating_profit_actual, 2);
    expect(b.gap).toBeCloseTo(s.headline.operating_profit_variance, 2);
    expect(b.steps.reduce((t, x) => t + x.amount, 0)).toBeCloseTo(b.gap, 2);
    // Each step is a measured effect in the answer key (signed on Operating Profit).
    const expected = [
      s.gross_margin_bridge.unit_cost_effect,
      s.gross_margin_bridge.discount_effect,
      s.gross_margin_bridge.mix_effect,
      -s.freight.freight_cost.variance,
      -s.labor.labor_cost.variance,
      -s.operating_expense_groups.other_lines_variance,
    ];
    const amounts = b.steps.map((x) => x.amount);
    for (const e of expected) expect(amounts.some((a) => Math.abs(a - e) < 0.01)).toBe(true);
    // The chart draws exactly these steps between the plan and actual bars.
    const chart = page.getByTestId("bridge-chart").first();
    await expect(chart).toBeVisible();
    const drawn = await chart.locator("[data-bar=step]").evaluateAll((els) => els.map((e) => Number(e.getAttribute("data-amount"))));
    expect(drawn).toEqual(amounts);
    await expect(chart.locator("[data-bar=total]")).toHaveCount(2);
  });

  await test.step("the first screen follows the causes down to supplier, fuel surcharge and branch", async () => {
    // No clicks: the default view opens the path to the operational causes.
    const top = s.lumber_unit_cost_by_supplier[0];
    await expect(treeItem(page, new RegExp(`^Supported explanation: ${top.supplier}: costs`))).toBeVisible();
    await expect(treeItem(page, /Fuel Surcharge/)).toBeVisible();
    const worst = [...s.labor.by_branch].sort((a, b) => b.overtime_variance - a.overtime_variance)[0];
    const branch = treeItem(page, new RegExp(`^Supported explanation: ${worst.branch}: Overtime`));
    await expect(branch).toBeVisible();
    // The same-branch payroll saving is named on the overtime node and one click away.
    await expect(branch).toContainText("partly offset in the same branch");
    const withOffset = nodes.find((n) => n.offset_node_id && n.statement.includes(worst.branch))!;
    expect(withOffset).toBeTruthy();
    await branch.click();
    await page.getByTestId("offset-link").click();
    await expect(page.locator(`[role=treeitem][data-node-id="${withOffset.offset_node_id}"]`)).toHaveAttribute("aria-selected", "true");
    // Immaterial lines are one compact node.
    await expect(page.locator("[role=treeitem][data-compact=true]")).toHaveCount(1);
  });

  await test.step("the gap reconciles: gross margin and operating expenses add back to it", async () => {
    expect(s.bridge.reconciles).toBe(true);
    const card = page.getByTestId("reconciliation-card");
    await expect(card).toContainText("Adds back to the gap");
    await expect(card).toContainText(money(s.bridge.gross_margin.variance, true));
    await expect(card).toContainText(money(s.bridge.operating_expenses.effect_on_operating_profit, true));
    await card.click();
    const table = page.getByRole("table", { name: "Reconciliation identities" });
    await expect(table).toBeVisible();
    const recon = nodes.find((n) => n.reconciliation)!;
    expect(recon.reconciliation!.reconciled).toBe(true);
    expect(recon.reconciliation!.gap).toBeCloseTo(s.headline.operating_profit_variance, 2);
  });

  await test.step("a major explanation node opens its evidence, calculation and SQL", async () => {
    const gm = nodes.find((n) => n.metric_id === "gross_margin" && n.root_effect !== null && n.root_effect !== undefined)!;
    await page.locator(`[role=treeitem][data-node-id="${gm.id}"]`).click();
    await expect(page.getByTestId("reconciliation-status")).toContainText("Part of the reconciled explanation");
    await expect(page.getByText(`Effect on Operating Profit`)).toBeVisible();
    await page.getByRole("tab", { name: "SQL" }).first().click();
    await expect(page.getByText(/SELECT/).first()).toBeVisible();
  });

  await test.step("the related signal is shown apart and not claimed as a cause", async () => {
    const section = page.getByTestId("related-signals");
    await expect(section).toBeVisible();
    const expected = s.related_signals[0];
    expect(expected.relationship_to_gap).toBe("not_established");
    const signal = section.getByRole("button", { name: new RegExp(`Related signal: .*${expected.filters.region}`) }).first();
    await expect(signal).toContainText("Relationship to the Operating Profit miss: not established");
    await signal.click();
    await expect(page.getByRole("note", { name: "Related signal" })).toContainText("Relationship to the Operating Profit miss: not established");
    // It is never a node of the explanation tree.
    const signalIds = nodes.filter((n) => n.role === "related_signal").map((n) => n.id);
    expect(signalIds.length).toBeGreaterThan(0);
    for (const id of signalIds) await expect(page.locator(`[role=tree] [data-node-id="${id}"]`)).toHaveCount(0);
  });

  await test.step("confirm the explanation's parts and the signal through the review workflow", async () => {
    const recon = nodes.find((n) => n.reconciliation)!;
    const parts = (recon.reconciliation as unknown as { identities: { components: { node_id: string }[] }[] }).identities[0].components.map((c) => c.node_id);
    const signal = nodes.find((n) => n.role === "related_signal" && !nodes.some((p) => p.id === n.parent_id && p.role === "related_signal"))!;
    const root = nodes.find((n) => n.parent_id === null)!;
    const premise = nodes.find((n) => n.role === "premise")!;
    // The parts' own parts: the margin bridge (unit cost, discounting, volume and mix) and the expense lines.
    const subParts = nodes.filter((n) => parts.includes(n.parent_id ?? "") && n.root_effect !== null && n.root_effect !== undefined && !n.role && n.kind !== "dimension");
    const toolbar = page.getByRole("toolbar", { name: "Node actions" });
    for (const id of [root.id, premise.id, ...parts, ...subParts.map((n) => n.id), signal.id]) {
      await page.locator(`[role=treeitem][data-node-id="${id}"], [data-testid=related-signals] [data-node-id="${id}"]`).first().click();
      await toolbar.getByRole("button", { name: "More node actions" }).click();
      await page.getByRole("menuitem", { name: "Confirm" }).click();
      await expect.poll(async () => {
        const inv = await (await page.request.get(`/api/v1/workspaces/${ws}/investigations/${investigationId(page)}`)).json();
        return (inv.tree.nodes as ApiNode[]).find((n) => n.id === id)?.status;
      }).toBe("confirmed");
    }
    // The gross-margin part also becomes a saved finding, the review workflow's unit of record.
    await page.locator(`[role=treeitem][data-node-id="${parts[0]}"]`).click();
    const saved = page.waitForResponse((r) => /\/nodes\/[^/]+\/finding$/.test(r.url()) && r.request().method() === "POST");
    await toolbar.getByRole("button", { name: /Save as finding/ }).click();
    expect((await saved).status()).toBe(201);
  });

  await test.step("a re-run reproduces every node and keeps the decision", async () => {
    await page.getByRole("button", { name: "Re-run" }).click();
    const diff = page.getByTestId("investigation-diff");
    await expect(diff).toBeVisible({ timeout: 240_000 });
    await expect(diff.getByText(/No differences: the rerun reproduced every node exactly/)).toBeVisible();
    const res = await page.request.get(`/api/v1/workspaces/${ws}/investigations/${inv}/diff`);
    const body = await res.json();
    const d = body.diff ?? body;
    expect(d.node_changes.filter((c: { change: string }) => c.change !== "unchanged")).toEqual([]);
    expect(d.dataset_version_changes).toEqual([]);
    expect(d.metric_version_changes).toEqual([]);
    await page.keyboard.press("Escape");
    const after = await (await page.request.get(`/api/v1/workspaces/${ws}/investigations/${inv}`)).json();
    const gm = (after.tree.nodes as ApiNode[]).find((n) => n.metric_id === "gross_margin" && n.root_effect !== null && n.root_effect !== undefined)!;
    expect(gm.status).toBe("confirmed");
  });

  await test.step("the report from the investigation carries the reconciled operating review", async () => {
    // Built from the accepted parts only: headline, premise, the reconciled explanation and the signal, not established.
    const command = page.getByLabel("Investigation command");
    await command.fill("build me a report");
    await command.press("Enter");
    await expect(page).toHaveURL(/\/reports\/[^/]+/, { timeout: 60_000 });
    const review = page.getByTestId("block-operating-review").first();
    await expect(review).toBeVisible();
    await expect(review).toContainText("Reconciles exactly");
    await expect(review).toContainText(money(Math.abs(s.headline.operating_profit_variance)).replace("-", ""));
    await expect(review).toContainText(/not established/);
    await expect(review).toContainText("The premise holds");
    await expect(review.locator("[data-memo=explains]")).toContainText(s.lumber_unit_cost_by_supplier[0].supplier);
    // The bridge block follows the memo, and a complete review drops the unconfirmed draft.
    const bridgeBlock = page.getByTestId("block-bridge").first();
    await expect(bridgeBlock.locator("[data-bar=step]")).toHaveCount(bridge!.steps.length);
    await expect(page.getByText("Unconfirmed analysis.")).toHaveCount(0);
  });

  await test.step("the quarterly business review shows plan columns and the same operating review", async () => {
    await page.goto(`/w/${ws}/reports/review`);
    await page.getByLabel("Review a").selectOption("quarter");
    await page.getByLabel("Quarter").selectOption("Q2 2026");
    await page.getByRole("button", { name: /Generate Q2 2026 review/ }).click();
    await expect(page).toHaveURL(/\/reports\/[^/]+/, { timeout: 180_000 });
    const overview = page.getByTestId("block-kpi-overview").first();
    await expect(overview).toContainText("vs plan");
    await expect(overview.getByRole("row", { name: /Revenue/ }).first()).toContainText(`+${(s.headline.revenue_variance_pct * 100).toFixed(1)}%`);
    const review = page.getByTestId("block-operating-review").first();
    await expect(review).toContainText("Reconciles exactly");
    await expect(review).toContainText(/not established/);
  });
});
