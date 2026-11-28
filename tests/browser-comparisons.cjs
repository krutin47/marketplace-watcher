/* Comparisons and calculator (handbook CMP-01..04, T-07, T-16, T-18). Run against an empty, disposable instance. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require("playwright");

(async () => {
  const origin = process.env.WATCHER_TEST_ORIGIN || "http://127.0.0.1:8000";
  const axe = process.env.AXE_PATH ? fs.readFileSync(process.env.AXE_PATH, "utf8") : null;
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, args: ["--no-sandbox"] });
  const violations = [];
  async function scan(page, label) {
    if (!axe) return;
    await page.addScriptTag({ content: axe });
    const found = await page.evaluate(async () => (await axe.run(document, { resultTypes: ["violations"] })).violations
      .filter((v) => v.impact === "critical" || v.impact === "serious").map((v) => `${v.id}: ${v.nodes.length}`));
    violations.push(...found.map((v) => `${label} ${v}`));
  }
  try {
    const context = await browser.newContext({ baseURL: origin, viewport: { width: 390, height: 844 }, bypassCSP: !!axe });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const post = async (path, data) => { const r = await page.request.post(path, { data }); assert.ok(r.ok(), await r.text()); return r.json(); };
    const project = await post("/api/projects", { name: "Home Media Server", budget: "1000", currency: "CAD" });
    await page.request.put(`/api/projects/${project.id}/requirements`, { data: { version: project.version, requirements: [
      { label: "Usable capacity", attribute: "usable_capacity", operator: ">=", value: "10", unit: "TB", hardness: "hard" }] } });
    await post("/api/ingestion/manual", { url: "https://example.com/l/probox-cmp", title: "ProBox 8-bay with 8x4TB drives", price: "600", currency: "CAD" });
    const b = (await post("/api/ingestion/manual", { url: "https://example.com/l/five-bay-cmp", title: "Five bay enclosure", price: "60", currency: "CAD" })).listing_ids[0];

    await page.goto("/");
    await page.getByRole("button", { name: "Comparisons", exact: true }).click();
    await page.getByLabel("Project").selectOption({ label: "Home Media Server" });
    await page.getByLabel(/ProBox 8-bay/).check();
    await page.getByLabel(/Five bay enclosure/).check();
    await page.getByRole("button", { name: "Compare", exact: true }).click();
    const table = page.getByRole("region", { name: "Comparison table" });
    await table.waitFor();
    const text = await table.innerText();
    assert.match(text, /raid6: 24\.00 TB/);
    assert.match(text, /assume none are included/);
    assert.match(text, /Usable capacity \(hard\): meets/);
    assert.match(text, /Usable capacity \(hard\): unknown/);
    assert.match(text, /60% of remaining/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390, "table scrolls inside its region");
    await scan(page, "comparison");

    await post(`/api/listings/${b}/evidence`, { kind: "seller_claim", content: "Includes 5x2TB drives" });
    await page.getByRole("button", { name: "Back to comparisons" }).click();
    await page.getByRole("button", { name: "Open comparison" }).click();
    await page.getByRole("alert").filter({ hasText: "New seller updates or notes were added" }).waitFor();
    await page.getByRole("button", { name: "Recompute now" }).click();
    await page.getByText("New snapshot saved.").waitFor();
    assert.equal(await page.getByRole("alert").count(), 0);
    assert.match(await table.innerText(), /5 × 2 TB/);
    assert.match(await table.innerText(), /Usable capacity \(hard\): fails/);
    await page.getByRole("button", { name: "View" }).last().click();
    await page.getByText("Viewing an earlier snapshot").waitFor();
    assert.doesNotMatch(await table.innerText(), /5 × 2 TB/);

    await page.getByRole("button", { name: "Back to comparisons" }).click();
    await page.getByLabel("Drive sizes in TB (comma-separated)").fill("4, 4, 4, 4");
    await page.getByLabel("Layout").selectOption("raid5");
    await page.getByRole("button", { name: "Calculate storage" }).click();
    await page.getByText("usable_tb: 12.00").waitFor();
    await page.getByLabel("Average watts").fill("50");
    await page.getByLabel("Rate per kWh (optional)").fill("0.15");
    await page.getByRole("button", { name: "Calculate energy" }).click();
    await page.getByText("annual_cost: 65.70").waitFor();
    await scan(page, "calculator");
    assert.deepEqual(errors, []);
    if (axe) assert.deepEqual(violations, [], "serious or critical accessibility violations");
    console.log(`PASS Chromium comparisons: project comparison, bundle capacity, stale snapshot and recompute, history, calculators${axe ? ", axe scans" : ""}`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
