/* Listing chat browser test (handbook CHAT-01..05, T-11, T-16). Run against an empty, disposable instance.
   Optional: set AXE_PATH to axe-core's axe.min.js to run an automated accessibility scan. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const { chromium } = require("playwright");

(async () => {
  const origin = process.env.WATCHER_TEST_ORIGIN || "http://127.0.0.1:8000";
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, args: ["--no-sandbox"] });
  const axe = process.env.AXE_PATH ? fs.readFileSync(process.env.AXE_PATH, "utf8") : null;
  const violations = [];
  async function scan(page, label) {
    if (!axe) return;
    await page.addScriptTag({ content: axe });
    const result = await page.evaluate(async () => (await axe.run(document, { resultTypes: ["violations"] })).violations
      .filter((v) => v.impact === "critical" || v.impact === "serious").map((v) => `${v.id}: ${v.nodes.length}`));
    violations.push(...result.map((v) => `${label} ${v}`));
  }
  try {
    const context = await browser.newContext({ baseURL: origin, viewport: { width: 390, height: 844 }, bypassCSP: !!axe,
      permissions: ["clipboard-read", "clipboard-write"] });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const imported = await page.request.post("/api/ingestion/manual", { data: {
      url: "https://example.com/listing/probox-browser", title: "Mediasonic ProBox 8-bay with 8x4TB drives",
      description: "USB 3.0 and eSATA enclosure.", price: "600", currency: "CAD" } });
    const listingId = (await imported.json()).listing_ids[0];
    await page.goto(`/?listing=${listingId}`);
    await page.getByRole("heading", { name: "Mediasonic ProBox 8-bay with 8x4TB drives" }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390);

    await page.getByText("Add a seller update or note").click();
    await page.getByLabel("Text", { exact: true }).fill("Seller says the drives have approximately 80,000 hours.");
    await page.getByRole("button", { name: "Add update" }).click();
    await page.getByText("Update saved.").waitFor();

    await page.getByRole("tab", { name: "Chat" }).click();
    assert.equal(await page.locator("#pane-details").isVisible(), false);
    const box = page.getByLabel("Message about this listing");
    await box.fill("Draft kept across tabs");
    await page.getByRole("tab", { name: "Details" }).click();
    await page.getByRole("tab", { name: "Chat" }).click();
    assert.equal(await box.inputValue(), "Draft kept across tabs");

    await box.fill("How about this one?");
    await page.getByRole("button", { name: "Send" }).click();
    await page.getByText("Assistant reply complete.").waitFor({ timeout: 15000 });
    const reply = page.locator(".bubble.assistant").last();
    const text = await reply.innerText();
    assert.match(text, /600 CAD/);
    assert.match(text, /80,000 power-on hours/);
    assert.match(text, /not an AI model/);
    await reply.getByRole("button", { name: "Copy" }).click();
    assert.match(await page.evaluate(() => navigator.clipboard.readText()), /600 CAD/);

    await page.getByText("Context sent with each message").click();
    await page.getByLabel(/^Seller claim/).uncheck();
    await page.getByText("Context updated for the next message.").waitFor();
    await scan(page, "mobile chat");

    await page.goto(`/?listing=${listingId}&pane=chat`);
    await page.locator(".bubble.assistant").first().waitFor();
    assert.equal(await page.locator(".bubble").count(), 2, "turns persist after reload");
    await page.getByText("Context sent with each message").click();
    assert.equal(await page.getByLabel(/^Seller claim/).isChecked(), false, "exclusion persists");

    await page.setViewportSize({ width: 1280, height: 900 });
    assert.equal(await page.locator("#pane-details").isVisible(), true);
    assert.equal(await page.locator("#pane-chat").isVisible(), true);
    assert.equal(await page.getByRole("tab", { name: "Chat" }).isVisible(), false);
    await scan(page, "desktop split");

    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement?.tagName);
    assert.ok(focused && focused !== "BODY", "keyboard focus moves");
    assert.deepEqual(errors, []);
    if (axe) assert.deepEqual(violations, [], "serious or critical accessibility violations");
    console.log(`PASS Chromium chat: mobile tabs, draft retention, seller update, streamed grounded reply, copy, context exclusion, reload persistence, desktop split${axe ? ", axe scan" : ""}`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
