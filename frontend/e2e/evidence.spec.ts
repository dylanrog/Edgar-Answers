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
const evidenceColumn = (page: Page) => page.getByRole("region", { name: "Evidence column" });

const dock = (page: Page) => page.getByTestId("tour-callout");
const SUMMARY = "This app only covers the 10-K and 10-Q SEC filings of 2 companies, from the last 3 years.";

test("the landing says what the demo covers once, and the chat says it once, under the input", async ({ page }) => {
  await stub(page, [TURN1]);
  await page.goto("/ask");
  const landing = page.getByTestId("tour-welcome");
  await expect(landing.getByTestId("coverage")).toContainText(SUMMARY);
  await expect(landing.getByText("MSFT · NVDA")).toBeVisible();
  // The landing is a choice, not a chat: no input bar on it.
  await expect(page.getByLabel("Question")).toHaveCount(0);
  await expect(page.getByText(/We have 10-K/)).toHaveCount(0);

  await landing.getByRole("button", { name: "Skip, ask my own question" }).click();
  await expect(page.getByText("What do you want to know?")).toBeVisible();
  await expect(page.getByTestId("coverage-caption")).toHaveText("10-K and 10-Q filings · 2 companies · last 3 years");
  await expect(page.getByText(SUMMARY)).toHaveCount(0);
  await expect(page.getByLabel("Question")).toHaveAttribute("placeholder", "Ask about a filing…");
});

test("the company filter folds into one button and reaches the request", async ({ page }) => {
  const requests = await stub(page, [TURN1], { seenTour: true });
  await page.goto("/ask");
  await page.getByRole("button", { name: "All 2 companies" }).click();
  await page.getByRole("button", { name: "NVDA", exact: true }).click();
  await expect(page.getByRole("button", { name: /^NVDA/ }).first()).toBeVisible();
  await askTyped(page, "Data Center revenue?");
  await expect(card(page, 1)).toBeVisible();
  expect((requests[0] as unknown as { filters: { tickers: string[] } }).filters.tickers).toEqual(["NVDA"]);
  // The input stays at the bottom with a follow-up prompt.
  await expect(page.getByLabel("Question")).toHaveAttribute("placeholder", "Ask a follow-up…");
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
      "This app only covers the 10-K and 10-Q SEC filings of 2 companies, from the last 3 years. Those are Microsoft and NVIDIA. Questions outside that can't be answered from it.",
    ),
  ).toBeVisible();
  await expect(evidenceColumn(page).getByText("This answer has no evidence to show.")).toBeVisible();
});

test("the tutorial runs in the input bar's place, step by step, and hands the input back at the end", async ({
  page,
}) => {
  const requests = await stub(page, [TURN1, TURN2]);
  await page.goto("/ask");
  await page.getByRole("button", { name: "Take the 1-minute tour" }).click();

  // While the tour runs there is no input bar: the tutorial panel is in its place.
  await expect(dock(page)).toContainText("Tutorial");
  await expect(page.getByLabel("Question")).toHaveCount(0);
  // Under the hood stays folded to its summary line, tutorial or not.
  await expect(page.getByRole("button", { name: /^Under the hood/ })).toHaveAttribute("aria-expanded", "false");
  await expect(dock(page)).toContainText("Step 2 of 5");
  await expect(dock(page)).toContainText("Click the pulsing [1] to see that figure in the original filing.");

  await chip(page).first().click();
  await expect(dock(page)).toContainText("Step 3 of 5");
  await expect(dock(page)).toContainText("That's the exact figure in the original filing");
  await expect(card(page, 1)).toHaveAttribute("aria-current", "true");

  await dock(page).getByRole("button", { name: "Next", exact: true }).click();
  await expect(dock(page)).toContainText("Step 4 of 5");
  await dock(page).getByRole("button", { name: `Ask "${tutorial.followUp}"`, exact: true }).click();
  await expect(dock(page)).toContainText("any document collection");
  await expect(dock(page)).toContainText("Step 5 of 5");

  // The closing shows the follow-up's evidence: its fiscal-2024 figure.
  await expect(evidenceColumn(page).locator('article[aria-current="true"] .cited-figure')).toHaveText("47,525");
  expect(requests).toHaveLength(2);
  expect(requests[0].conversation_id).toBe(requests[1].conversation_id);

  // Done lands on a fresh chat: the tutorial's conversation was the tour's.
  await dock(page).getByRole("button", { name: "Done" }).click();
  await expect(dock(page)).toHaveCount(0);
  await expect(page.getByLabel("Question")).toBeVisible();
  await expect(page.getByLabel("Question")).toHaveAttribute("placeholder", "Ask about a filing…");
  await expect(page.getByText(tutorial.question, { exact: true })).toHaveCount(0);
  await expect(page.getByText("What do you want to know?")).toBeVisible();
});

test("Exit tutorial hands the input back straight away", async ({ page }) => {
  await stub(page, [TURN1]);
  await page.goto("/ask");
  await page.getByRole("button", { name: "Take the 1-minute tour" }).click();
  await expect(dock(page)).toContainText("Step 2 of 5");
  await dock(page).getByRole("button", { name: "Exit tutorial" }).click();
  await expect(dock(page)).toHaveCount(0);
  await expect(page.getByLabel("Question")).toBeVisible();
  await expect(page.getByText(tutorial.question, { exact: true })).toHaveCount(0);
  await expect(page.getByText("What do you want to know?")).toBeVisible();
  await expect(page.getByRole("button", { name: "Take the tour" })).toBeVisible();
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

  test("evidence sits inline, the tutorial panel stays at the bottom, and the input comes back pinned", async ({ page }) => {
    await stub(page, [TURN1, TURN2]);
    await page.goto("/ask");
    await page.getByRole("button", { name: "Take the 1-minute tour" }).tap();
    await expect(dock(page)).toContainText(/Tap the pulsing \[1\]/);
    await expect(dock(page)).toBeInViewport();
    await expect(evidenceColumn(page)).toHaveCount(0);
    // The cards are inside the turn, under the answer.
    await expect(page.locator("article:not([data-marker]) article[data-marker]")).toHaveCount(2);

    await chip(page).first().tap();
    await expect(dock(page).getByRole("button", { name: "Next", exact: true })).toBeInViewport();
    await expect(card(page, 1).locator(".cited-figure")).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

    await card(page, 1).getByRole("button", { name: "Open full filing" }).tap();
    const dialog = page.getByRole("dialog", { name: "Full filing" });
    const box = await dialog.locator("div").nth(1).boundingBox();
    expect(box?.width).toBeGreaterThanOrEqual(389);
    await dialog.getByRole("button", { name: "Close", exact: true }).tap();

    // The follow-up scrolls its own question into view, not turn 1's first
    // evidence card (both are <article>s).
    await dock(page).getByRole("button", { name: "Next", exact: true }).tap();
    await dock(page).getByRole("button", { name: `Ask "${tutorial.followUp}"`, exact: true }).tap();
    await expect(dock(page)).toContainText("any document collection");
    await expect(page.getByText(tutorial.followUp, { exact: true }).last()).toBeInViewport();

    await dock(page).getByRole("button", { name: "Done" }).tap();
    await expect(page.getByLabel("Question")).toBeInViewport();
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(page.getByLabel("Question")).toBeInViewport();
  });

  test("the page behind an open drawer is inert", async ({ page }) => {
    await stub(page, [TURN1], { seenTour: true });
    await page.goto("/ask");
    await askTyped(page, "Compare them");
    await card(page, 1).getByRole("button", { name: "Open full filing" }).tap();
    await expect(page.getByRole("dialog", { name: "Full filing" })).toBeVisible();
    expect(await page.locator("main > section").first().evaluate((el) => (el as HTMLElement).inert)).toBe(true);
  });
});
