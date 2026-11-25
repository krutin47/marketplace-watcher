/* Run with Node and Playwright against an empty, disposable local instance. */
const assert = require("node:assert/strict");
const { chromium } = require("playwright");

(async () => {
  const origin = process.env.WATCHER_TEST_ORIGIN || "http://127.0.0.1:8000";
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || undefined,
    args: ["--no-sandbox"],
  });
  try {
    const context = await browser.newContext({
      viewport: { width: 390, height: 844 },
      permissions: ["clipboard-read", "clipboard-write"],
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(origin);
    await page.getByRole("heading", { name: "Deal overview" }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390);
    await page.getByRole("button", { name: "Watchlists", exact: true }).click();
    await page.getByLabel("Name", { exact: true }).fill("Browser smoke");
    await page.getByLabel("Search phrase").fill("8TB HDD");
    await page.getByLabel("Maximum price", { exact: true }).fill("50");
    await page.getByLabel("Location (optional)").fill("");
    await page.getByLabel("Simulated fixture", { exact: true }).uncheck();
    await page.getByLabel("Manual import", { exact: true }).check();
    await page.getByRole("button", { name: "Create watchlist" }).click();
    await page.getByRole("heading", { name: "Browser smoke" }).waitFor();

    await page.getByRole("button", { name: "Listings", exact: true }).click();
    await page.getByText("No listings match these filters.").waitFor();
    await page.getByText("Import listing manually").click();
    await page.getByLabel("Listing URL").fill("https://example.com/smoke/hdd");
    await page.getByLabel("Title", { exact: true }).fill("8TB HDD browser smoke");
    await page.getByLabel("Price", { exact: true }).fill("35");
    await page.getByLabel("Currency", { exact: true }).fill("CAD");
    await page.getByRole("button", { name: "Import listing" }).click();
    await page.getByRole("heading", { name: "8TB HDD browser smoke" }).waitFor();
    await page.getByRole("button", { name: "Generate draft" }).click();
    const draft = page.getByLabel("Seller message (review and send manually)");
    await page.waitForFunction(() => document.querySelector("textarea").value.includes("SMART"));
    await draft.fill("Is this disk available?");
    await page.getByRole("button", { name: "Save draft" }).click();
    await page.getByText("Draft saved.").waitFor();
    await page.getByRole("button", { name: "Copy draft" }).click();
    await page.getByText("Draft copied.").waitFor();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), "Is this disk available?");
    await page.getByLabel("Listing state").selectOption("saved");
    await page.getByText("Listing state saved.").waitFor();

    await page.getByRole("button", { name: "Back to listings" }).click();
    await page.getByRole("button", { name: "View details" }).click();
    assert.equal(await draft.inputValue(), "Is this disk available?");
    assert.equal(await page.getByLabel("Listing state").inputValue(), "saved");
    await page.getByRole("button", { name: "Settings" }).click();
    await page.getByRole("button", { name: "Send test notification" }).click();
    await page.getByText("Test notification queued.").waitFor();
    await page.route("**/api/overview", (route) => route.abort());
    await page.getByRole("button", { name: "Overview" }).click();
    await page.getByRole("button", { name: "Retry" }).waitFor();
    await page.unroute("**/api/overview");
    await page.getByRole("button", { name: "Retry" }).click();
    await page.getByRole("heading", { name: "Deal overview" }).waitFor();
    assert.deepEqual(errors, []);
    console.log("PASS Chromium mobile smoke: empty and retry states, watchlist, import, draft, clipboard, state, and test delivery");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
