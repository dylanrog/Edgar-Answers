import { expect, test } from "@playwright/test";

const ACCESSION = "0000320193-24-000123";
const API = "http://localhost:8000";

const SSE_BODY = [
  'event: token\ndata: {"text":"Total net sales were $391,035 million [1]."}\n\n',
  `event: citation\ndata: {"marker":1,"verified":true,"accession":"${ACCESSION}",`,
  '"ticker":"AAPL","form_type":"10-K","filing_date":"2024-11-01",',
  '"sids":[647],"quote":"Total net sales $ 391,035"}\n\n',
  'event: done\ndata: {"chunks_retrieved":8,"citations_total":1,',
  '"citations_verified":1,"unverified_answer":false}\n\n',
].join("");

test("the citation highlight survives a re-render of the page", async ({ page }) => {
  await page.route(`${API}/companies`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([{ cik: 320193, ticker: "AAPL", name: "Apple Inc.", filings: 12 }]),
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
        viewer_html: '<p><span data-sid="646">Intro.</span></p><img id="gone" src="missing-image.jpg"><p><span data-sid="647">Total net sales $ 391,035</span></p>',
        ticker: "AAPL",
        form_type: "10-K",
        filing_date: "2024-11-01",
        period_end: "2024-09-28",
      }),
    }),
  );
  await page.addInitScript(() => localStorage.setItem("edgar-answers.tour-seen", "1"));
  await page.goto("/ask");
  await page.getByLabel("Question").fill("What were total net sales?");
  await page.getByRole("button", { name: "Ask" }).click();
  await page.getByRole("button", { name: "[1]" }).click();
  await expect(page.locator(".cited-sentence")).toBeVisible();

  // A second question re-renders the whole page (new turn, streamed tokens).
  await page.getByLabel("Question").fill("another question");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("another question").first()).toBeVisible();
  await expect(page.locator(".cited-sentence")).toHaveCount(1);
  await expect(page.locator("#gone")).toBeHidden();
});
