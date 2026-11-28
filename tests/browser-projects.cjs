/* Projects, inventory, memory and project-aware chat (handbook PROJ, INV, section 15, T-16).
   Run against an empty, disposable instance. Optional AXE_PATH enables an accessibility scan. */
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
    page.on("dialog", (dialog) => dialog.accept());
    await page.goto("/");
    await page.getByRole("heading", { name: "Deal overview" }).waitFor();

    await page.getByRole("button", { name: "Inventory", exact: true }).click();
    await page.getByLabel("Name", { exact: true }).fill("WD Red 4TB");
    await page.getByLabel("Category").fill("hard drive");
    await page.getByLabel("Exact model").fill("WD40EFRX");
    await page.getByLabel("Quantity").fill("2");
    await page.getByLabel("Serial number (private, optional)").fill("PRIVATE-SERIAL-1");
    await page.getByLabel("Specifications (one key=value per line)").fill("interface=SATA\ncapacity_tb=4");
    await page.getByRole("button", { name: "Add item" }).click();
    await page.getByText("Inventory saved.").waitFor();
    await page.getByText("interface: SATA · capacity_tb: 4").waitFor();
    assert.equal(await page.getByText("PRIVATE-SERIAL-1").count(), 0);
    await scan(page, "inventory");

    await page.getByRole("button", { name: "Projects", exact: true }).click();
    await page.getByLabel("Name", { exact: true }).fill("Home Media Server");
    await page.getByLabel("Budget (optional)").fill("1000");
    await page.getByRole("button", { name: "Create project" }).click();
    await page.getByText("Project created.").waitFor();
    await page.getByRole("button", { name: "Add requirement" }).click();
    await page.getByLabel("Label").fill("Usable capacity");
    await page.getByLabel("Attribute").fill("usable_capacity");
    await page.getByLabel("Value", { exact: true }).fill("10");
    await page.getByLabel("Unit").fill("TB");
    await page.getByRole("button", { name: "Save requirements" }).click();
    await page.getByText("Requirements saved.").waitFor();
    await page.getByLabel("Quantity").fill("3");
    await page.getByRole("button", { name: "Allocate" }).click();
    await page.getByText(/Only 2 of 2 are unallocated/).waitFor();
    await page.getByLabel("Quantity").fill("2");
    await page.getByRole("button", { name: "Allocate" }).click();
    await page.getByText("Allocated.").waitFor();
    await page.getByText("2 × WD Red 4TB").waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390);
    await scan(page, "project detail");

    await page.getByRole("button", { name: "Memory", exact: true }).click();
    await page.getByText("Add a fact").click();
    await page.getByLabel("Applies to").selectOption({ label: "Project · Home Media Server" });
    await page.getByLabel("Fact", { exact: true }).fill("max_drive_hours");
    await page.getByLabel("Value", { exact: true }).fill("50000");
    await page.getByLabel("Unit").fill("hours");
    await page.getByRole("button", { name: "Save fact" }).click();
    await page.getByText("Fact saved and approved.").waitFor();
    await scan(page, "memory");

    const imported = await page.request.post("/api/ingestion/manual", { data: {
      url: "https://example.com/listing/probox-projects", title: "ProBox 8-bay enclosure", price: "600", currency: "CAD" } });
    const listingId = (await imported.json()).listing_ids[0];
    await page.goto(`/?listing=${listingId}`);
    await page.getByRole("button", { name: "Link to project" }).click();
    await page.getByText("Linked to project.").waitFor();
    await page.getByText("Home Media Server · candidate").waitFor();
    await page.getByRole("tab", { name: "Chat" }).click();
    await page.getByLabel("Project context").selectOption({ label: "Home Media Server" });
    await page.getByLabel("Message about this listing").fill("Does this fit my project?");
    await page.getByRole("button", { name: "Send" }).click();
    await page.getByText("Assistant reply complete.").waitFor({ timeout: 15000 });
    const reply = await page.locator(".bubble.assistant").last().innerText();
    assert.match(reply, /Project context: Home Media Server/);
    assert.match(reply, /60% of the remaining budget/);
    assert.match(reply, /2 × WD Red 4TB/);
    assert.match(reply, /max_drive_hours: 50000 hours/);
    assert.doesNotMatch(reply, /PRIVATE-SERIAL-1/);
    assert.deepEqual(errors, []);
    if (axe) assert.deepEqual(violations, [], "serious or critical accessibility violations");
    console.log(`PASS Chromium projects: inventory privacy, project budget/requirements, allocation limit, memory, listing link, project-aware chat${axe ? ", axe scans" : ""}`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
