import { expect, test, type Page } from "@playwright/test";

/**
 * Acceptance flow 1 (spec §96): start AnalystOS -> load the Summit Supply demo -> inspect schema,
 * relationships and metrics -> ask "Why was August revenue down?" -> review the plan -> run real
 * SQL -> investigation tree with drivers and charts -> expose the calculation and SQL -> rerun and
 * get an empty diff -> drill into Dallas -> find the major customer -> save a finding -> generate
 * a report.
 *
 * Every interaction is a real Playwright click (actionability checks included): no dispatched
 * events, no reloads. A click that cannot land is a product bug, not something to work around.
 */

const QUESTION = "Why was August revenue down?";
const MAJOR_CUSTOMER = "Trinity Ridge Construction";

function workspaceId(page: Page): string {
  const m = page.url().match(/\/w\/([^/?#]+)/);
  if (!m) throw new Error(`not inside a workspace: ${page.url()}`);
  return m[1];
}

function investigationId(page: Page): string {
  const m = page.url().match(/\/investigate\/([^/?#]+)/);
  if (!m) throw new Error(`not on an investigation: ${page.url()}`);
  return m[1];
}

function treeItem(page: Page, text: RegExp) {
  return page.getByRole("tree", { name: "Investigation tree" }).getByRole("treeitem", { name: text }).first();
}

test("acceptance flow 1: why was August revenue down", async ({ page }) => {
  test.setTimeout(900_000);

  await test.step("start AnalystOS and load the Summit Supply demo", async () => {
    await page.goto("/");
    const load = page.getByRole("button", { name: /Load Summit Supply demo/ }).first();
    await expect(load).toBeEnabled({ timeout: 60_000 });
    await load.click();
    await expect(page.getByText("Summit Supply demo loaded").first()).toBeVisible({ timeout: 240_000 });
    // One navigation, straight into the new workspace.
    await expect(page).toHaveURL(/\/w\/[^/]+/, { timeout: 30_000 });
    await expect(page.getByText("Summit Supply Co.").first()).toBeVisible({ timeout: 60_000 });
  });

  const ws = workspaceId(page);

  await test.step("inspect the discovered schema", async () => {
    await page.goto(`/w/${ws}/data`);
    await expect(page.getByRole("link", { name: "Order Lines", exact: true })).toBeVisible();
    await expect(page.getByRole("link", { name: "Customers", exact: true })).toBeVisible();
    await page.getByRole("link", { name: "Orders", exact: true }).click();
    await expect(page.getByText("order_date").first()).toBeVisible();
    await expect(page.getByText("customer_id").first()).toBeVisible();
  });

  await test.step("review suggested relationships", async () => {
    await page.goto(`/w/${ws}/data/relationships`);
    await expect(page.getByText(/order_lines/).first()).toBeVisible();
    await expect(page.getByText(/approved/i).first()).toBeVisible();
  });

  await test.step("inspect semantic metrics", async () => {
    await page.goto(`/w/${ws}/metrics`);
    await expect(page.getByText("Revenue", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Gross Margin %").first()).toBeVisible();
    await expect(page.getByText("Contribution Margin").first()).toBeVisible();
  });

  await test.step("ask the question and review the plan", async () => {
    await page.goto(`/w/${ws}/investigate?new=1`);
    await page.getByLabel("Ask a business question").fill(QUESTION);
    await page.getByRole("button", { name: /^Investigate/ }).click();
    await expect(page).toHaveURL(/\/investigate\/[^/]+/, { timeout: 60_000 });
    const runPlan = page.getByRole("button", { name: /^Run \d+ steps?/ });
    await expect(runPlan.or(treeItem(page, /Revenue declined/))).toBeVisible({ timeout: 60_000 });
    if (await runPlan.isVisible()) {
      await expect(page.getByRole("list", { name: "Plan steps" })).toBeVisible();
      await runPlan.click();
    }
  });

  await test.step("the tree shows the decline and its drivers", async () => {
    await expect(treeItem(page, /Revenue declined 11\.8%/)).toBeVisible({ timeout: 180_000 });
    await expect(treeItem(page, /Orders declined/)).toBeVisible();
    await expect(treeItem(page, /Branch = Dallas/)).toBeVisible();
  });

  await test.step("charts, the calculation and the SQL are exposed for a finding", async () => {
    await treeItem(page, /Branch = Dallas: Revenue/).click();
    await expect(page.getByRole("img", { name: /^Chart: / }).first()).toBeVisible({ timeout: 30_000 });
    await page.getByRole("tab", { name: "Calculation" }).click();
    const segments = page.getByRole("table", { name: "Segment effects" });
    await expect(segments).toBeVisible();
    await expect(segments.getByRole("row", { name: /Dallas/ })).toBeVisible();
    await page.getByRole("tab", { name: "SQL" }).first().click();
    await expect(page.getByText(/SELECT/).first()).toBeVisible();
    await expect(page.getByText(/cancelled/).first()).toBeVisible();
  });

  await test.step("rerun on unchanged data reproduces every node (empty diff)", async () => {
    const inv = investigationId(page);
    await page.getByRole("button", { name: "Re-run" }).click();
    const diff = page.getByTestId("investigation-diff");
    await expect(diff).toBeVisible({ timeout: 180_000 });
    await expect(diff.getByText(/No differences: the rerun reproduced every node exactly/)).toBeVisible();
    // The same through the API: no changed, added or removed nodes and no version changes.
    const res = await page.request.get(`/api/v1/workspaces/${ws}/investigations/${inv}/diff`);
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    const d = body.diff ?? body;
    expect(d.node_changes.filter((c: { change: string }) => c.change !== "unchanged")).toEqual([]);
    expect(d.dataset_version_changes).toEqual([]);
    expect(d.metric_version_changes).toEqual([]);
    await page.keyboard.press("Escape");
    await expect(diff).toBeHidden();
  });

  await test.step("drill into Dallas by customer and find the major customer", async () => {
    await treeItem(page, /Branch = Dallas: Revenue/).click();
    await page.getByRole("toolbar", { name: "Node actions" }).getByRole("button", { name: /Branch \/ drill/ }).click();
    const dialog = page.getByRole("dialog", { name: "Branch into a dimension" });
    await dialog.getByLabel("Dimension").selectOption({ label: "Customer" });
    await dialog.getByRole("button", { name: /^Branch$/ }).click();
    await expect(treeItem(page, new RegExp(`Customer = ${MAJOR_CUSTOMER}`))).toBeVisible({ timeout: 60_000 });
  });

  await test.step("save the Dallas node as a finding; the node shows it is saved", async () => {
    await treeItem(page, /Branch = Dallas: Revenue/).click();
    const toolbar = page.getByRole("toolbar", { name: "Node actions" });
    const saved = page.waitForResponse(
      (r) => /\/nodes\/[^/]+\/finding$/.test(r.url()) && r.request().method() === "POST",
    );
    await toolbar.getByRole("button", { name: /Save as finding/ }).click();
    expect((await saved).status()).toBe(201);
    await expect(toolbar.getByRole("button", { name: /Saved as finding/ })).toBeDisabled();
    await expect(page.getByRole("link", { name: /Saved finding/ }).first()).toBeVisible();
  });

  await test.step("generate a report from the investigation", async () => {
    const command = page.getByLabel("Investigation command");
    await command.fill("build me a report");
    await command.press("Enter");
    await expect(page).toHaveURL(/\/reports\/[^/]+/, { timeout: 60_000 });
    await expect(page.getByText(/Dallas/).first()).toBeVisible();
  });

  await test.step("the finding persists", async () => {
    await page.goto(`/w/${ws}/findings`);
    await expect(page.getByText(/Dallas/).first()).toBeVisible();
  });
});
