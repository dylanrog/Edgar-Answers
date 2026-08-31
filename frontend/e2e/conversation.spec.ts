import { expect, test } from "@playwright/test";

const FY24 = "0000320193-24-000123";
const FY23 = "0000320193-23-000106";
const API = "http://localhost:8000";

// Keep the marker at the very end of the token text: AnswerStream splits
// "[1]" into its own <button>, so an assertion that straddles the marker
// ("...2024 [1].") would span two elements and not match with getByText.
const FIRST_SSE = [
  'event: token\ndata: {"text":"Total net sales in fiscal 2024 were 391 billion [1]"}\n\n',
  `event: citation\ndata: {"marker":1,"verified":true,"accession":"${FY24}",`,
  '"ticker":"AAPL","form_type":"10-K","filing_date":"2024-11-01",',
  '"sids":[1],"quote":"Total net sales 391,035"}\n\n',
  'event: done\ndata: {"chunks_retrieved":8,"citations_total":1,',
  '"citations_verified":1,"unverified_answer":false}\n\n',
].join("");

const FOLLOWUP_SSE = [
  'event: resolved\ndata: {"standalone_question":"What was the revenue for fiscal 2023?"}\n\n',
  'event: token\ndata: {"text":"Total net sales in fiscal 2023 were 383 billion [1]"}\n\n',
  `event: citation\ndata: {"marker":1,"verified":true,"accession":"${FY23}",`,
  '"ticker":"AAPL","form_type":"10-K","filing_date":"2023-11-03",',
  '"sids":[1],"quote":"Total net sales 383,285"}\n\n',
  'event: done\ndata: {"chunks_retrieved":8,"citations_total":1,',
  '"citations_verified":1,"unverified_answer":false}\n\n',
].join("");

function filing(accession: string, html: string, filed: string) {
  return {
    accession,
    viewer_html: html,
    ticker: "AAPL",
    form_type: "10-K",
    filing_date: filed,
    period_end: null,
  };
}

test("a follow-up streams a second turn and shows the rewritten query", async ({
  page,
}) => {
  await page.route(`${API}/companies`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify([
        { cik: 320193, ticker: "AAPL", name: "Apple Inc.", filings: 13 },
      ]),
    }),
  );

  const conversationIds: Array<string | undefined> = [];
  await page.route(`${API}/ask`, (route) => {
    const body = route.request().postDataJSON() as {
      question: string;
      conversation_id?: string;
    };
    conversationIds.push(body.conversation_id);
    const isFollowup = /2023|and in/i.test(body.question);
    route.fulfill({
      contentType: "text/event-stream",
      body: isFollowup ? FOLLOWUP_SSE : FIRST_SSE,
    });
  });

  await page.route(`${API}/filings/${FY24}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(
        filing(FY24, '<p><span data-sid="1">Total net sales 391,035</span></p>', "2024-11-01"),
      ),
    }),
  );
  await page.route(`${API}/filings/${FY23}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify(
        filing(FY23, '<p><span data-sid="1">Total net sales 383,285</span></p>', "2023-11-03"),
      ),
    }),
  );

  await page.goto("/ask");

  await page.getByLabel("Question").fill("What were Apple's total net sales in fiscal 2024?");
  await page.getByRole("button", { name: "Ask" }).click();
  await expect(page.getByText("in fiscal 2024 were 391 billion")).toBeVisible();

  await page.getByLabel("Question").fill("and in fiscal 2023?");
  await page.getByRole("button", { name: "Ask" }).click();

  // The second turn shows the rewritten, self-contained question ...
  const caption = page.getByText(/^Searched for:/);
  await expect(caption).toContainText("What was the revenue for fiscal 2023?");
  // ... its own answer, and the first turn is still on the page.
  await expect(page.getByText("in fiscal 2023 were 383 billion")).toBeVisible();
  await expect(page.getByText("in fiscal 2024 were 391 billion")).toBeVisible();

  // Both /ask calls carried the same, non-empty conversation id.
  expect(conversationIds[0]).toBeTruthy();
  expect(conversationIds[1]).toBe(conversationIds[0]);

  // "New conversation" clears the thread.
  await page.getByRole("button", { name: "New conversation" }).click();
  await expect(page.getByText("in fiscal 2024 were 391 billion")).toHaveCount(0);
  await expect(page.getByText("Ask a question about a filing.")).toBeVisible();
});
