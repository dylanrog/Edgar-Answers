/**
 * One decoded SSE frame from POST /ask. Event names:
 * - `token`     {"text": string}                       — answer deltas
 * - `resolved`  {"standalone_question": string}         — a rewritten follow-up,
 *                                                         emitted once before the
 *                                                         first token, only when
 *                                                         the rewrite changed the text
 * - `citation`  Citation
 * - `done`      {chunks_retrieved, citations_total, citations_verified, unverified_answer}
 * - `error`     {"message": string}
 */
export type SSEEvent = { event: string; data: unknown };

/** A citation event, post-verification (design §6.4). */
export type Citation = {
  marker: number;
  verified: boolean;
  /** Empty when the model cited a chunk it was never shown — unattributable. */
  accession: string;
  ticker: string;
  form_type: string;
  filing_date: string;
  sids: number[];
  quote: string;
};

/** GET /filings/{accession} */
export type Filing = {
  accession: string;
  viewer_html: string;
  ticker: string;
  form_type: string;
  filing_date: string;
  period_end: string | null;
};

/** GET /companies */
export type Company = {
  cik: number;
  ticker: string;
  name: string;
  filings: number;
};
