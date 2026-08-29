import { expect, test } from "@playwright/test";

const AAPL = "0000320193-24-000123";
const MSFT = "0000789019-24-000078";
const API = "http://localhost:8000";

const SSE_BODY = [
  'event: token\ndata: {"text":"Apple cites single-source suppliers [1]. ',
  'Microsoft cites datacenter hardware [2]."}\n\n',
  `event: citation\ndata: {"marker":1,"verified":true,"accession":"${AAPL}",`,
  '"ticker":"AAPL","form_type":"10-K","filing_date":"2024-11-01",',
  '"sids":[2],"quote":"single-source suppliers"}\n\n',
  `event: citation\ndata: {"marker":2,"verified":true,"accession":"${MSFT}",`,
  '"ticker":"MSFT","form_type":"10-K","filing_date":"2024-07-30",',
  '"sids":[1],"quote":"a limited number of suppliers"}\n\n',
  'event: done\ndata: {"chunks_retrieved":8,"citations_total":2,',
  '"citations_verified":2,"unverified_answer":false}\n\n',
].join("");

// Long enough that the pane scrolls, so a preserved scroll position is
// actually observable.
const filler = Array.from(
  { length: 60 },
  (_, i) => `<p><span data-sid="${100 + i}">Filler sentence ${i}.</span></p>`,
).join("");

const AAPL_HTML = `
  <p><span data-sid="1">Apple risk intro.</span></p>
  <p><span data-sid="2">single-source suppliers concentrate our exposure</span></p>
  ${filler}`;

const MSFT_HTML = `
  <p><span data-sid="1">a limited number of suppliers serve our datacenters</span></p>
  ${filler}`;

function filing(accession: string, ticker: string, html: string, filed: string) {
  return {
    accession,
    viewer_html: html,
    ticker,
    form_type: "10-K",
    filing_date: filed,
    period_end: null,
  };
}

test("an answer spanning two filings opens two tabs that keep their scroll", async ({
  page,
}) => {
  await page.route(`${API}/companies`, (route) =>
    route.fulfill({ contentType: "application/json", body: "[]" }),
  );
  await page.route(`${API}/ask`, (route) =>
    route.fulfill({ contentType: "text/event-stream", body: SSE_BODY }),
  );
  await page.route(`${API}/filings/${AAPL}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(filing(AAPL, "AAPL", AAPL_HTML, "2024-11-01")),
    }),
  );
  await page.route(`${API}/filings/${MSFT}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(filing(MSFT, "MSFT", MSFT_HTML, "2024-07-30")),
    }),
  );

  await page.goto("/ask");
  await page
    .getByLabel("Question")
    .fill("How do Apple and Microsoft describe supply chain risk?");
  await page.getByRole("button", { name: "Ask" }).click();

  // Both filings appear as sources, and the panel says so.
  await expect(page.getByText("Sources · 2 filings · 2 citations")).toBeVisible();

  // Open Apple, then Microsoft.
  await page.getByRole("button", { name: "[1]" }).click();
  await expect(page.locator(`[data-sid="2"]`).first()).toHaveClass(/cited-sentence/);

  // exact: true throughout — the close button's aria-label is
  // "Close MSFT 10-K 2024", and Playwright matches accessible names by
  // substring, so a loose name here resolves to both buttons in the tab.
  await page.getByRole("button", { name: "[2]" }).click();
  await expect(page.getByRole("button", { name: "MSFT 10-K 2024", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "AAPL 10-K 2024", exact: true })).toBeVisible();

  // Scroll the Microsoft pane, switch away, switch back: position survives.
  // Addressed by data-accession, not by a class: a ".overflow-y-auto" selector
  // would resolve to whichever pane is currently visible, so after switching
  // to Apple it would silently read Apple's scrollTop instead.
  const msftPane = page.locator(`[data-accession="${MSFT}"]`);
  await msftPane.evaluate((el) => el.scrollTo(0, 800));
  const scrolled = await msftPane.evaluate((el) => el.scrollTop);
  expect(scrolled).toBeGreaterThan(0);

  await page.getByRole("button", { name: "AAPL 10-K 2024", exact: true }).click();
  await page.getByRole("button", { name: "MSFT 10-K 2024", exact: true }).click();
  await expect.poll(() => msftPane.evaluate((el) => el.scrollTop)).toBe(scrolled);
});
