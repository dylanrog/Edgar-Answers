import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import examples from "../lib/examples.json";

const ACCESSION = "0000320193-25-000079";
const API = "http://localhost:8000";
const FEATURED = examples.find((e) => e.featured)!;
const OTHER = examples.find((e) => !e.featured)!;

const TABLE_HTML = `
  <table><tbody>
    <tr data-sid="30"><td>Year Ended</td></tr>
    <tr data-sid="31"><td>Research and development</td><td>$</td><td>34,550</td><td>$</td><td>31,370</td></tr>
  </tbody></table>`;

const sse = (event: string, data: unknown) =>
  `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;

const PRELUDE =
  sse("progress", { stage: "targets", tickers: ["AAPL"], filings: [] }) +
  sse("progress", {
    stage: "retrieved",
    passages: 8,
    filings: [
      { accession: ACCESSION, ticker: "AAPL", form_type: "10-K", filing_date: "2025-10-31", passages: 8 },
    ],
  }) +
  sse("token", { text: "R&D spending was $34,550 million [1]." });

const VERIFIED_CITATION = sse("citation", {
  marker: 1,
  verified: true,
  accession: ACCESSION,
  ticker: "AAPL",
  form_type: "10-K",
  filing_date: "2025-10-31",
  sids: [31],
  quote: "Research and development $ 34,550",
  cells: [{ sid: 31, cell: 2 }],
});
const UNVERIFIED_CITATION = sse("citation", {
  marker: 1,
  verified: false,
  accession: "",
  ticker: "",
  form_type: "",
  filing_date: "",
  sids: [],
  quote: "fabricated",
});
const done = (verified: number) =>
  sse("done", {
    chunks_retrieved: 8,
    citations_total: 1,
    citations_verified: verified,
    unverified_answer: false,
  });

const OK_BODY =
  PRELUDE + sse("progress", { stage: "verifying", quotes: 1 }) + VERIFIED_CITATION + done(1);

/** Stubs the API and records each /ask request body. */
async function stub(page: Page, askBody: string) {
  const bodies: { question: string; conversation_id: string }[] = [];
  await page.route(`${API}/companies`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([{ cik: 320193, ticker: "AAPL", name: "Apple Inc.", filings: 12 }]),
    }),
  );
  await page.route(`${API}/ask`, (route) => {
    bodies.push(route.request().postDataJSON());
    return route.fulfill({ contentType: "text/event-stream", body: askBody });
  });
  await page.route(`${API}/filings/${ACCESSION}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        accession: ACCESSION,
        viewer_html: TABLE_HTML,
        ticker: "AAPL",
        form_type: "10-K",
        filing_date: "2025-10-31",
        period_end: "2025-09-27",
      }),
    }),
  );
  return bodies;
}

const welcome = (page: Page) => page.getByText("Pick a question to see how it works.");
const callout = (page: Page) => page.getByTestId("tour-callout");

/** Plays the tour from the welcome card up to the click-the-citation step. */
async function toClickStep(page: Page) {
  await page.goto("/ask");
  await page.getByRole("button", { name: FEATURED.label }).click();
  await expect(page.getByText("Click the highlighted citation")).toBeVisible();
}

test("the full tour: ask, watch, click, see the source, close", async ({ page }) => {
  await stub(page, OK_BODY);
  await page.goto("/ask");
  await expect(welcome(page)).toBeVisible();

  // The featured example is visually first.
  await expect(page.getByTestId("tour-welcome").getByRole("button").first()).toHaveText(
    FEATURED.label,
  );
  await page.getByRole("button", { name: FEATURED.label }).click();

  await expect(page.getByText(/Searched the filings: found/)).toBeVisible();
  await expect(page.getByText(/found word for word/)).toBeVisible();
  await expect(page.getByText(/table figure/)).toBeVisible();

  await expect(page.getByText("Click the highlighted citation")).toBeVisible();
  const chip = page.getByRole("button", { name: "[1]" });
  await expect(chip).toHaveClass(/ring-2/);
  await chip.click();

  await expect(page.locator(".cited-figure, .cited-sentence").first()).toBeVisible();
  await expect(page.getByText(/That's the exact sentence/)).toBeVisible();

  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText(/any document collection/)).toBeVisible();
  await expect(page.getByRole("link", { name: "See the code on GitHub" })).toHaveAttribute(
    "href",
    "https://github.com/dylanrog/Edgar-Answers",
  );

  await page.getByRole("button", { name: "Done" }).click();
  await expect(callout(page)).toHaveCount(0);
  await page.reload();
  await expect(page.getByLabel("Question")).toBeVisible();
  await expect(welcome(page)).toHaveCount(0);
});

test("skipping the welcome card ends the tour for good", async ({ page }) => {
  await stub(page, OK_BODY);
  await page.goto("/ask");
  await page.getByRole("button", { name: "Skip tour" }).click();
  await expect(welcome(page)).toHaveCount(0);
  await page.reload();
  await expect(page.getByLabel("Question")).toBeVisible();
  await expect(welcome(page)).toHaveCount(0);
  await expect(callout(page)).toHaveCount(0);
});

test("no verified citation skips the click step and goes to the closing", async ({ page }) => {
  await stub(
    page,
    PRELUDE + sse("progress", { stage: "verifying", quotes: 1 }) + UNVERIFIED_CITATION + done(0),
  );
  await page.goto("/ask");
  await page.getByRole("button", { name: FEATURED.label }).click();

  await expect(page.getByText(/any document collection/)).toBeVisible();
  await expect(page.getByText("Click the highlighted citation")).toHaveCount(0);
  await expect(page.getByText("[1] unverified")).toBeVisible();
});

test("an error mid-stream shows the interrupted callout", async ({ page }) => {
  await stub(page, PRELUDE + sse("error", { message: "The model is unavailable." }));
  await page.goto("/ask");
  await page.getByRole("button", { name: FEATURED.label }).click();

  await expect(page.getByText(/That one didn't go through\./)).toBeVisible();
  await expect(page.getByText("The model is unavailable.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
});

test("every example pick starts a fresh conversation", async ({ page }) => {
  const bodies = await stub(page, OK_BODY);
  await toClickStep(page);
  await page.getByRole("button", { name: "[1]" }).click();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText(/any document collection/)).toBeVisible();

  await page.getByRole("button", { name: OTHER.label }).click();
  await expect.poll(() => bodies.length).toBe(2);
  expect(bodies[0].conversation_id).toBeTruthy();
  expect(bodies[1].conversation_id).toBeTruthy();
  expect(bodies[1].conversation_id).not.toBe(bodies[0].conversation_id);
  expect(bodies[1].question).toBe(OTHER.question);

  // Asking from the closing step finishes the tour: no callout under the new turn.
  await expect(page.getByText(/found word for word/)).toBeVisible();
  await expect(callout(page)).toHaveCount(0);
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("the tour fits the screen and the citation click brings the highlight into view", async ({
    page,
  }) => {
    await stub(page, OK_BODY);
    await toClickStep(page);
    await page.getByRole("button", { name: "[1]" }).click();

    const cited = page.locator(".cited-sentence, .cited-figure").first();
    await expect(cited).toBeVisible();
    await expect(cited).toBeInViewport();
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(scrollWidth).toBeLessThanOrEqual(375);
  });
});

