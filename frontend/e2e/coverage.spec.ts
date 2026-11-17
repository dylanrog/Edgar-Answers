import { expect, test } from "@playwright/test";

const API = "http://localhost:8000";

const company = (ticker: string, name: string, first: string, last: string) => ({
  cik: ticker.length,
  ticker,
  name,
  filings: 12,
  form_types: ["10-K", "10-Q"],
  first_filed: first,
  last_filed: last,
});

test("the empty state says which forms, companies and years the demo covers", async ({ page }) => {
  await page.route(`${API}/companies`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        company("AAPL", "Apple Inc.", "2023-08-04", "2026-07-31"),
        company("JPM", "JPMorgan Chase & Co.", "2023-11-03", "2026-05-01"),
      ]),
    }),
  );
  await page.addInitScript(() => localStorage.setItem("edgar-answers.tour-seen", "1"));
  await page.goto("/ask");
  await expect(page.getByTestId("coverage")).toHaveText(
    "We have 10-K and 10-Q filings from Apple and JPMorgan Chase, filed in the last 3 years (Aug 2023 – Jul 2026).",
  );
});

test("an API without coverage fields keeps the generic prompt", async ({ page }) => {
  await page.route(`${API}/companies`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([{ cik: 320193, ticker: "AAPL", name: "Apple Inc.", filings: 13 }]),
    }),
  );
  await page.addInitScript(() => localStorage.setItem("edgar-answers.tour-seen", "1"));
  await page.goto("/ask");
  await expect(page.getByTestId("coverage")).toHaveText("Ask a question about a filing.");
});
