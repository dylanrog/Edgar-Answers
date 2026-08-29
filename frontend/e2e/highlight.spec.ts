import { expect, test } from "@playwright/test";

const ACCESSION = "0000320193-24-000123";

// Must be the API origin, not a bare `**/ask` glob: the app itself is served
// at /ask, so a loose pattern intercepts the page navigation and renders the
// SSE stub as the HTML document.
const API = "http://localhost:8000";

const SSE_BODY = [
  'event: token\ndata: {"text":"Total net sales were $391,035 million [1]."}\n\n',
  `event: citation\ndata: {"marker":1,"verified":true,"accession":"${ACCESSION}",`,
  '"ticker":"AAPL","form_type":"10-K","filing_date":"2024-11-01",',
  '"sids":[647],"quote":"Total net sales $ 391,035"}\n\n',
  'event: done\ndata: {"chunks_retrieved":8,"citations_total":1,',
  '"citations_verified":1,"unverified_answer":false}\n\n',
].join("");

const VIEWER_HTML = `
  <p><span data-sid="646">Segment information follows.</span></p>
  <p><span data-sid="647">Total net sales $ 391,035 2 % $ 383,285</span></p>
  <p><span data-sid="648">Services set a record.</span></p>`;

test("clicking a verified citation highlights the cited sentence", async ({ page }) => {
  await page.route(`${API}/companies`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        { cik: 320193, ticker: "AAPL", name: "Apple Inc.", filings: 12 },
      ]),
    }),
  );
  await page.route(`${API}/ask`, (route) =>
    route.fulfill({ contentType: "text/event-stream", body: SSE_BODY }),
  );
  await page.route(`${API}/filings/${ACCESSION}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        accession: ACCESSION,
        viewer_html: VIEWER_HTML,
        ticker: "AAPL",
        form_type: "10-K",
        filing_date: "2024-11-01",
        period_end: "2024-09-28",
      }),
    }),
  );

  await page.goto("/ask");
  await page.getByLabel("Question").fill("What were total net sales?");
  await page.getByRole("button", { name: "Ask" }).click();

  // The marker becomes a real button only once its citation event lands.
  const chip = page.getByRole("button", { name: "[1]" });
  await expect(chip).toBeVisible();
  await chip.click();

  const cited = page.locator('[data-sid="647"]');
  await expect(cited).toHaveClass(/cited-sentence/);
  await expect(page.locator('[data-sid="646"]')).not.toHaveClass(/cited-sentence/);
});

test("an unverified citation is badged and not clickable", async ({ page }) => {
  await page.route(`${API}/companies`, (route) =>
    route.fulfill({ contentType: "application/json", body: "[]" }),
  );
  await page.route(`${API}/ask`, (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body:
        'event: token\ndata: {"text":"Sales fell [1]."}\n\n' +
        'event: citation\ndata: {"marker":1,"verified":false,"accession":"",' +
        '"ticker":"","form_type":"","filing_date":"",' +
        '"sids":[],"quote":"fabricated"}\n\n' +
        'event: done\ndata: {"chunks_retrieved":8,"citations_total":1,' +
        '"citations_verified":0,"unverified_answer":false}\n\n',
    }),
  );

  await page.goto("/ask");
  await page.getByLabel("Question").fill("Did sales fall?");
  await page.getByRole("button", { name: "Ask" }).click();

  await expect(page.getByText("[1] unverified")).toBeVisible();
  await expect(page.getByRole("button", { name: "[1]" })).toHaveCount(0);
});
