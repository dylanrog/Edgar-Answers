import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import tutorial from "../lib/tutorial.json";

// The evidence layout at /ask (spec 2026-11-17). The classic layout's specs run
// against /classic, its revert path.

const NVDA = "0001045810-25-000023";
const MSFT = "0000950170-25-100235";
const API = "http://localhost:8000";

const NVDA_HTML = `
  <p><span data-sid="29">Revenue by end market.</span></p>
  <table><tbody>
    <tr data-sid="30"><td>Year Ended</td><td></td><td>Jan 26, 2025</td><td></td><td>Jan 28, 2024</td></tr>
    <tr data-sid="31"><td>Data Center</td><td>$</td><td>115,186</td><td>$</td><td>47,525</td></tr>
    <tr data-sid="32"><td>Gaming</td><td></td><td>11,350</td><td></td><td>10,447</td></tr>
  </tbody></table>`;
const MSFT_HTML = `<p><span data-sid="49">•</span> <span data-sid="50">Microsoft Cloud revenue was $168.9 billion.</span> <span data-sid="51">Azure grew.</span></p>`;

const sse = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
const nvdaRef = { accession: NVDA, ticker: "NVDA", form_type: "10-K", filing_date: "2025-02-26" };
const msftRef = { accession: MSFT, ticker: "MSFT", form_type: "10-K", filing_date: "2025-07-30" };
const progress = (...refs: (typeof nvdaRef)[]) =>
  sse("progress", { stage: "targets", tickers: refs.map((r) => r.ticker), filings: [] }) +
  sse("progress", { stage: "retrieved", passages: 8, filings: refs.map((r) => ({ ...r, passages: 4 })) });
const done = (total: number, verified: number) =>
  sse("done", { chunks_retrieved: 8, citations_total: total, citations_verified: verified, unverified_answer: false });
const nvdaCitation = (marker: number, cell = 2, quote = "Data Center $ 115,186") =>
  sse("citation", { marker, verified: true, ...nvdaRef, sids: [31], quote, cells: [{ sid: 31, cell }] });
const msftCitation = (marker: number) =>
  sse("citation", { marker, verified: true, ...msftRef, sids: [50], quote: "Microsoft Cloud revenue was $168.9 billion." });

const TURN1 =
  progress(nvdaRef, msftRef) +
  sse("token", { text: "NVIDIA Data Center revenue was $115,186 million [1]; Microsoft Cloud was $168.9 billion [2]." }) +
  sse("progress", { stage: "verifying", quotes: 2 }) +
  nvdaCitation(1) +
  msftCitation(2) +
  done(2, 2);
const TURN2 =
  sse("resolved", { standalone_question: "What was NVIDIA's Data Center revenue in fiscal 2024?" }) +
  progress(nvdaRef) +
  sse("token", { text: "In fiscal 2024 it was $47,525 million [1]." }) +
  sse("progress", { stage: "verifying", quotes: 1 }) +
  nvdaCitation(1, 4, "Data Center $ 47,525") +
  done(1, 1);
const UNVERIFIED =
  progress(nvdaRef) +
  sse("token", { text: "Something invented [1]." }) +
  sse("progress", { stage: "verifying", quotes: 1 }) +
  sse("citation", {
    marker: 1, verified: false, accession: "", ticker: "", form_type: "", filing_date: "", sids: [], quote: "fabricated quote",
  }) +
  done(1, 0);
const NOTHING =
  progress() +
  sse("token", { text: "I cannot answer this question based on the provided excerpts." }) +
  done(0, 0);

const company = (cik: number, ticker: string, name: string) => ({
  cik, ticker, name, filings: 12, form_types: ["10-K", "10-Q"], first_filed: "2023-08-04", last_filed: "2026-07-31",
});

async function stub(page: Page, bodies: string[], { seenTour = false } = {}) {
  const requests: { question: string; conversation_id: string }[] = [];
  if (seenTour) await page.addInitScript(() => localStorage.setItem("edgar-answers.tour-seen", "1"));
  await page.route(`${API}/companies`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([company(789019, "MSFT", "Microsoft Corporation"), company(1045810, "NVDA", "NVIDIA Corporation")]),
    }),
  );
  for (const [accession, html, ref] of [[NVDA, NVDA_HTML, nvdaRef], [MSFT, MSFT_HTML, msftRef]] as const) {
    await page.route(`${API}/filings/${accession}`, (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ accession, viewer_html: html, ticker: ref.ticker, form_type: ref.form_type, filing_date: ref.filing_date, period_end: ref.filing_date }),
      }),
    );
  }
  await page.route(`${API}/ask`, async (route) => {
    const n = requests.length;
    requests.push(route.request().postDataJSON());
    await route.fulfill({ contentType: "text/event-stream", body: bodies[Math.min(n, bodies.length - 1)] });
  });
  return requests;
}

async function askTyped(page: Page, question: string) {
  await page.getByLabel("Question").fill(question);
  await page.getByRole("button", { name: "Ask", exact: true }).click();
}

const card = (page: Page, marker: number) => page.locator(`article[data-marker="${marker}"]`);
const chip = (page: Page, marker = 1) => page.getByRole("button", { name: `[${marker}]`, exact: true });
const callout = (page: Page) => page.getByTestId("tour-callout");
const evidenceColumn = (page: Page) => page.getByRole("region", { name: "Evidence column" });

test("the header says the demo covers only these companies, and the composer says so too", async ({ page }) => {
  await stub(page, [TURN1], { seenTour: true });
  await page.goto("/ask");
  await expect(page.getByTestId("coverage-line")).toHaveText(
    "Covers only Microsoft and NVIDIA · 10-K and 10-Q filings, Aug 2023 – Jul 2026",
  );
  await expect(page.getByText("Limit to:")).toBeVisible();
  await expect(page.getByLabel("Question")).toHaveAttribute("placeholder", "Ask about these 2 companies' filings…");
  // The coverage line stays once a conversation is underway.
  await askTyped(page, "anything");
  await expect(card(page, 1)).toBeVisible();
  await expect(page.getByTestId("coverage-line")).toBeVisible();
});

test("each citation gets an evidence card cut from the filing, with the cited figure marked", async ({ page }) => {
  await stub(page, [TURN1], { seenTour: true });
  await page.goto("/ask");
  await askTyped(page, "Compare them");

  const column = evidenceColumn(page);
  await expect(column.getByText("2 of 2 quotes verified")).toBeVisible();
  // Card 1: the whole table, header row included, with the cited cell marked.
  await expect(card(page, 1).getByText("Exact match in the filing")).toBeVisible();
  await expect(card(page, 1).locator("tr")).toHaveCount(3);
  await expect(card(page, 1).locator(".cited-figure")).toHaveText("115,186");
  // Card 2: the sentence with its neighbour, the wordless bullet left out.
  await expect(card(page, 2).locator(".cited-sentence")).toHaveText("Microsoft Cloud revenue was $168.9 billion.");
  await expect(card(page, 2)).toContainText("Azure grew.");
  await expect(card(page, 2)).not.toContainText("•");

  // A chip click selects its card.
  await chip(page, 2).click();
  await expect(card(page, 2)).toHaveAttribute("aria-current", "true");
  await expect(card(page, 1)).not.toHaveAttribute("aria-current", "true");
});

test("Open full filing shows the whole filing highlighted, and Escape returns focus", async ({ page }) => {
  await stub(page, [TURN1], { seenTour: true });
  await page.goto("/ask");
  await askTyped(page, "Compare them");
  const open = card(page, 1).getByRole("button", { name: "Open full filing" });
  await open.click();
  const dialog = page.getByRole("dialog", { name: "Full filing" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".cited-figure")).toHaveText("115,186");
  await expect(dialog.getByRole("button", { name: "Close", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(open).toBeFocused();
});

test("an unverified citation keeps a visible red card and stays inert", async ({ page }) => {
  await stub(page, [UNVERIFIED], { seenTour: true });
  await page.goto("/ask");
  await askTyped(page, "Invent something");
  await expect(card(page, 1).getByText("Quote not found in the filing")).toBeVisible();
  await expect(card(page, 1).getByText("“fabricated quote”")).toBeVisible();
  await expect(card(page, 1).getByRole("button", { name: "Open full filing" })).toHaveCount(0);
  await expect(evidenceColumn(page).getByText("0 of 1 quote verified")).toBeVisible();
});

test("an answer that finds nothing says what the demo covers", async ({ page }) => {
  await stub(page, [NOTHING], { seenTour: true });
  await page.goto("/ask");
  await askTyped(page, "What was Coca-Cola's revenue in 2024?");
  await expect(
    page.getByText(
      "This demo only covers 2 companies (Microsoft and NVIDIA), with filings from Aug 2023 – Jul 2026. Questions outside that can't be answered from it.",
    ),
  ).toBeVisible();
  await expect(evidenceColumn(page).getByText("This answer has no evidence to show.")).toBeVisible();
});

test("the tutorial walks from the welcome card to the closing, the source card sitting on its evidence", async ({
  page,
}) => {
  const requests = await stub(page, [TURN1, TURN2]);
  await page.goto("/ask");
  await page.getByTestId("tour-welcome").getByRole("button", { name: "Ask this question", exact: true }).click();
  await expect(page.getByText("Click the pulsing [1] to see that figure in the original filing.")).toBeVisible();

  await chip(page).first().click();
  // The source step's card sits directly above the selected evidence card.
  await expect(callout(page)).toContainText("That's the exact figure in the original filing");
  const above = await callout(page).evaluate((el) => {
    const next = el.closest("[id^=evidence-]")?.querySelector("article[data-marker]");
    return next?.getAttribute("data-marker") ?? null;
  });
  expect(above).toBe("1");
  await expect(card(page, 1)).toHaveAttribute("aria-current", "true");

  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText(/Want to see one more/)).toBeVisible();
  await page.getByRole("button", { name: `Ask "${tutorial.followUp}"`, exact: true }).click();
  await expect(callout(page).filter({ hasText: "any document collection" })).toBeVisible();

  // The closing shows the follow-up's evidence: its fiscal-2024 figure.
  await expect(evidenceColumn(page).locator('article[aria-current="true"] .cited-figure')).toHaveText("47,525");
  expect(requests).toHaveLength(2);
  expect(requests[0].conversation_id).toBe(requests[1].conversation_id);
  await page.getByRole("button", { name: "Done" }).click();
  await expect(callout(page)).toHaveCount(0);
});

test("clicking an earlier answer's citation shows that answer's evidence", async ({ page }) => {
  await stub(page, [TURN1, TURN2], { seenTour: true });
  await page.goto("/ask");
  await askTyped(page, "Compare them");
  await expect(card(page, 2)).toBeVisible();
  await askTyped(page, "What about 24?");
  await expect(evidenceColumn(page).locator(".cited-figure")).toHaveText("47,525");
  await chip(page, 2).first().click();
  await expect(evidenceColumn(page).getByText(/Showing evidence for an earlier answer/)).toBeVisible();
  await expect(card(page, 2)).toHaveAttribute("aria-current", "true");
});

test.describe("on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test("evidence sits inline under the answer, and the tutorial card scrolls into view with it", async ({ page }) => {
    await stub(page, [TURN1, TURN2]);
    await page.goto("/ask");
    await page.getByTestId("tour-welcome").getByRole("button", { name: "Ask this question", exact: true }).click();
    await expect(page.getByText(/Tap the pulsing \[1\]/)).toBeVisible();
    await expect(evidenceColumn(page)).toHaveCount(0);
    // The cards are inside the turn, under the answer.
    await expect(page.locator("article:not([data-marker]) article[data-marker]")).toHaveCount(2);

    await chip(page).first().tap();
    await expect(callout(page).getByRole("button", { name: "Next", exact: true })).toBeInViewport();
    await expect(card(page, 1).locator(".cited-figure")).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

    await card(page, 1).getByRole("button", { name: "Open full filing" }).tap();
    const dialog = page.getByRole("dialog", { name: "Full filing" });
    const box = await dialog.locator("div").nth(1).boundingBox();
    expect(box?.width).toBeGreaterThanOrEqual(389);
  });
});
