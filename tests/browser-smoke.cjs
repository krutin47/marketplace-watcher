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
      baseURL: origin,
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
    await page.getByText("No listings have been collected yet.").waitFor();
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
    const createFixture = await page.request.post("/api/watchlists", {
      data: { name: "Fixture disk", phrase: "8TB HDD", max_price: 50 },
    });
    assert.equal(createFixture.status(), 201);
    const fixture = await createFixture.json();
    async function waitForRun(count) {
      for (let attempt = 0; attempt < 80; attempt += 1) {
        const runs = await (await page.request.get("/api/runs")).json();
        if (runs.some((run) => run.watchlist_id === fixture.id && run.result_count === count)) return;
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      assert.fail(`No fixture run returned ${count} listings`);
    }
    await waitForRun(1);
    await page.getByRole("button", { name: "Watchlists", exact: true }).click();
    await page.getByRole("button", { name: "Run now" }).click();
    await page.getByText("Run queued.").waitFor();
    await page.getByRole("button", { name: "Open Activity" }).click();
    await waitForRun(7);
    await page.getByRole("button", { name: "Refresh" }).click();
    const completedRun = page.locator("article").filter({hasText: "7 listings returned"});
    await completedRun.getByRole("button", { name: "View listings" }).click();
    await page.getByText("Showing 1–7 of 7 listings", { exact: true }).waitFor();
    await page.getByRole("heading", { name: "8GB HDD broken" }).waitFor();
    await page.getByRole("button", { name: "View all listings" }).click();
    await page.getByText("Showing 1–8 of 8 listings", { exact: true }).waitFor();
    await page.getByRole("heading", { name: "8TB HDD browser smoke" }).waitFor();
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
    console.log("PASS Chromium mobile smoke: run results, all listings, import, drafts, state, and test delivery");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
