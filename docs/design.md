# EDGAR Answers — Technical Design (v1)

**Status:** pre-implementation
**Date:** 2026-07-19
**Author:** Dylan Rogers

---

## 1. What this is

A RAG-based Q&A system over SEC filings where **every citation is verified against
the source text server-side before it renders**, and clicking a citation highlights
the exact sentences in the original filing.

The differentiating feature is not the RAG loop — it's the trust machinery around it:
deterministic citation verification and exact click-to-highlight. That constraint
drives most decisions in this document.

This is a learning project

## 2. Scope decisions (v1)

| Decision | Choice | Rationale |
|---|---|---|
| Filing types | 10-K + 10-Q | Similar document structure, one canonicalizer covers both; enables quarter-over-quarter questions |
| Companies | ~10 curated large-caps | Predictable volume (~120 filings over ~3 fiscal years); eval answers can be hand-verified |
| Embeddings | Local: `bge-small-en-v1.5` via fastembed (ONNX) | High-volume cost made free; 384 dims keeps the index small; no torch in the API image |
| Generation | Claude Haiku (`claude-haiku-4-5`) | Low-volume, pennies per answer; small local models are unreliable at verbatim structured quoting, which is the core feature |
| Citation mechanism | Sentence-anchored quotes (Approach A, §6) | Deterministic verification, exact highlighting |
| XBRL | **Out** | Separate subsystem; will be well-defined in v2 extension |
| 8-K filings | **Out** | Structurally diverse; multiplies canonicalizer work |
| On-demand ticker ingestion | **Out** | Requires async job queue + progress UI; v2 |
| Auth, chat history, threading | **Out** | Not what this project is for |

## 3. Architecture

```
EDGAR APIs ──▶ Ingestion pipeline (Python CLI) ──▶ Postgres + pgvector
 (submissions,   fetch → canonicalize →                  │
  filing docs)   chunk → embed                           │
                                                         │
Next.js frontend ◀──────── FastAPI ──────────────────────┘
(/ask + filing viewer)     retrieve → generate → verify → stream (SSE)
```

Three units with hard boundaries:

- **Pipeline** (`backend/src/pipeline/`): batch CLI, writes to Postgres, never called by the API.
- **API** (`backend/src/api/`): reads Postgres, calls the LLM, owns verification. Stateless.
- **Frontend** (`frontend/`): renders streamed answers and stored filing HTML. No SEC or LLM access.

The only shared contract between pipeline and API is the database schema (§5).
The only contract between API and frontend is the HTTP/SSE interface (§8).

## 4. Ingestion pipeline

CLI entry points, idempotent per accession number (re-runs skip ingested filings
unless `--force`):

```
python -m pipeline ingest --ticker AAPL          # all in-scope filings for one company
python -m pipeline ingest --all                  # the full curated list
python -m pipeline ingest --accession <acc-no>   # one filing (debugging)
```

### 4.1 Fetch

- `https://data.sec.gov/submissions/CIK{cik}.json` lists filings; filter to 10-K/10-Q,
  last 3 fiscal years; download each primary document.
- **Etiquette:** identifying `EDGAR_USER_AGENT` on every request; throttle to 5 req/s
  (SEC's limit is 10); exponential backoff on 429/5xx.
- Raw HTML cached at `data/raw/{cik}/{accession}.html` (gitignored). Re-runs and
  canonicalizer development never re-hit EDGAR.

### 4.2 Canonicalize — project heart

Input: one filing's raw HTML. Output: two **aligned** representations.

1. **Canonical text** — extracted text, segmented into sentences. Each sentence gets
   a stable integer ID (`sid`), assigned in document order, plus a section label.
2. **Viewer HTML** — the filing sanitized for browser rendering (scripts, styles,
   inline-XBRL tags stripped), with every extracted sentence wrapped:
   `<span data-sid="1042">…</span>`.

The same `sid` in both representations is the invariant the whole product rests on:
verification resolves quotes to sids in canonical text; the viewer highlights those
same sids. Both outputs are produced in a **single traversal** of the parsed DOM
(BeautifulSoup + lxml) — generating them in separate passes would invite drift.

- **Sentence segmentation:** `pysbd` with character spans (handles abbreviations,
  legal numbering better than naive splitting). Fixture tests define correctness;
  pysbd is replaceable if it disappoints.
- **Section detection:** regex over heading text for 10-K items (1, 1A, 3, 7, 7A, 8)
  and 10-Q parts/items. Unmatched content gets section `"other"` — never a crash.
- **Tables:** indexed, one sentence per table row, and preserved in viewer HTML.
  A row is never sentence-segmented (it is not prose) and carries `data-sid` on
  the `<tr>` itself, since a `<span>` cannot wrap `<td>` elements. A table's
  rows are chunked as one atomic unit even past the token budget, so a header
  row always travels with its data. Numeric questions are answered from these
  rows as well as from narrative text. Column-aware parsing of a row into a
  record remains a v2 item — a row is a flat string.
- **Reprocessing:** `python -m pipeline recanonicalize` rebuilds `viewer_html`
  only and refuses to write when sentences move. `python -m pipeline reprocess`
  is its opposite: it rebuilds sentences, chunks and embeddings from cached raw
  HTML and expects sids to move, so every stored citation and pinned gold sid
  is invalidated. Run `python -m evals repin --snapshot` **before** it and
  `python -m evals repin` after.
- **Inline styles:** colour-bearing declarations (`color`, `background`,
  `background-color`) are stripped from inline `style` attributes during the
  same traversal; layout declarations are kept, because EDGAR tables rely on
  width, alignment and borders for their geometry. Filings arrive with colour
  hardcoded to `#000000` on thousands of elements, which is unreadable on the
  dark viewer. `python -m pipeline recanonicalize` backfills `viewer_html` for
  already-ingested filings without re-chunking or re-embedding.
- **Degradation:** if a filing's structure defeats section detection entirely, ingest
  it as one `"other"` section and log a warning. A filing in the corpus without
  sections beats a crash.

### 4.3 Chunk

Greedy grouping of consecutive sentences **within a section** up to 450 tokens
(tiktoken count). A chunk is a contiguous, disjoint sid range — no overlap, so every
sentence belongs to exactly one chunk and citations map back unambiguously.

The budget is 450, not the ~600 this section originally specified: `bge-small-en-v1.5`
truncates at 512 of its own tokens, and tiktoken undercounts relative to the BGE
tokenizer on financial text, so a 600-token chunk loses its tail from the vector
index while still returning that tail as context. Do not restore 600.
(If retrieval quality wants more context later, expand to neighboring chunks at
query time rather than overlapping at ingestion.)

### 4.4 Embed

`bge-small-en-v1.5` via `fastembed`. Passages embed as-is; **queries get the BGE
query prefix** (`"Represent this sentence for searching relevant passages: "`) —
an easy-to-miss requirement that measurably affects retrieval. Batch-embed at
ingestion; single-query embedding at request time is <100 ms on CPU.

## 5. Data model (Postgres + pgvector)

```sql
companies (
  cik         bigint PRIMARY KEY,
  ticker      text UNIQUE NOT NULL,
  name        text NOT NULL
)

filings (
  id           bigserial PRIMARY KEY,
  cik          bigint REFERENCES companies,
  accession    text UNIQUE NOT NULL,
  form_type    text NOT NULL,            -- '10-K' | '10-Q'
  filing_date  date NOT NULL,
  period_end   date,
  viewer_html  text NOT NULL             -- sanitized, sid-annotated
)

sentences (
  filing_id  bigint REFERENCES filings,
  sid        integer NOT NULL,           -- stable per filing, document order
  section    text NOT NULL,
  text       text NOT NULL,
  char_start integer NOT NULL,           -- offsets into canonical text
  char_end   integer NOT NULL,
  PRIMARY KEY (filing_id, sid)
)

chunks (
  id          bigserial PRIMARY KEY,
  filing_id   bigint REFERENCES filings,
  section     text NOT NULL,
  sid_start   integer NOT NULL,
  sid_end     integer NOT NULL,
  text        text NOT NULL,
  token_count integer NOT NULL,
  embedding   vector(384) NOT NULL
)
-- HNSW index on chunks.embedding (cosine)
-- GIN index on to_tsvector('english', chunks.text)
```

Scale check: ~120 filings × ~5–15k sentences ≈ 1–2M sentence rows, ~30–60k chunks.
Comfortable for a single small Postgres instance; `viewer_html` totals well under 1 GB.

Migrations: plain numbered SQL files (`backend/migrations/001_*.sql`) applied by a
tiny runner script. Alembic is deliberate v2 — learn what migrations *are* first.

## 6. Query path

`POST /ask` with `{question, filters?: {ticker?, form_type?}}`, responding over SSE.

The `year` filter is **deferred to the §14 backlog** — `retrieval.retrieve()` takes
no year parameter and Phase 3 did not add one. Filtering by fiscal year needs a
decision about whether "year" means `filing_date` or `period_end`, which differ for
every 10-K; shipping the ambiguity would be worse than not shipping the filter.

### 6.1 Retrieve (hybrid)

1. Vector: pgvector cosine top-20 (query embedded with BGE prefix).
2. Lexical: Postgres full-text top-20, **`plainto_tsquery` with its `&` operators
   rewritten to `|`** — not the `websearch_to_tsquery` this section originally
   specified. Postgres' tsquery builders AND every stemmed term, so a natural
   question ("What were Apple's total net sales in fiscal 2024?") matches only a
   chunk containing all six significant terms, and the lexical arm silently returns
   nothing on realistic queries. ORing gives "any term matches, ranked by how many"
   — which is the job here. `plainto_tsquery` specifically, because the rewrite is a
   string replace on the tsquery's text form and is therefore only total if `&` is
   the sole operator that can appear; `websearch_to_tsquery` also emits `!` for a
   `-term`, and an OR'd negation matches nearly every chunk in the corpus.
3. Fuse with Reciprocal Rank Fusion (k=60); take top 8 chunks into context.

Hybrid is non-negotiable: finance is dense with exact terms
("ASC 842", "RSUs", "Item 1A") where lexical retrieval beats semantic.

### 6.2 Generate

One Claude Haiku call. The prompt contract:

- Answer **only** from the provided chunks; say so when they don't contain the answer.
- Every factual claim carries an inline marker `[1]`, `[2]`, …
- After the answer, emit a fenced JSON block:
  `{"citations": [{"marker": 1, "chunk_id": 8123, "quote": "<verbatim text from that chunk, ≤300 chars>"}]}`

The answer portion streams to the client token-by-token as it arrives; the server
buffers and parses the trailing JSON block when generation completes. (Streaming
prose + trailing structured block is simpler and cheaper than two-phase generation,
and keeps perceived latency low.) If the JSON fails to parse: one retry of the full
call; then the answer renders with an "unverified answer" notice.

### 6.3 Verify — the core feature

For each citation `{chunk_id, quote}`:

1. **Normalize** both quote and the chunk's canonical text: Unicode NFKC, curly
   quotes → straight, en/em-dashes → hyphen, collapse whitespace, casefold.
   Maintain a normalized→original offset map for the chunk text.
2. **Match:** the normalized quote must be a substring of the normalized chunk text.
   No fuzzy matching — determinism is the point.
3. **Resolve:** map the match back to original character offsets, intersect with
   sentence `[char_start, char_end)` ranges → the cited sids.
4. **Emit** a `citation` SSE event with `verified: true` and the sids — or, on any
   failure, `verified: false` with no sids.

Failed citations render with a visible **"unverified" badge** rather than being
silently dropped. That's honest, and it makes the verification machinery visible in
demos — the feature working is *more* convincing when the failure mode is on display.

### 6.4 SSE events

```
token:    {"text": "…"}                            -- answer deltas
citation: {"marker": 1, "verified": true,
           "accession": "0000320193-24-000123",
           "ticker": "AAPL", "form_type": "10-K",
           "filing_date": "2024-11-01",
           "sids": [1042, 1043], "quote": "…"}
done:     {"chunks_retrieved": 8, "citations_total": 3,
           "citations_verified": 3, "unverified_answer": false}
error:    {"message": "…"}
```

### 6.5 Other endpoints

- `GET /filings/{accession}` → `{viewer_html, ticker, form_type, filing_date, period_end}`
- `GET /companies` → curated list for the filter UI
- `GET /healthz`

## 7. Frontend (Next.js App Router + TypeScript)

One page: `/ask`, split-pane.

- **Left — answer pane:** question input, optional company/form filters, streamed
  answer. Markers render as citation chips; verified chips are clickable,
  unverified chips show the badge and are not. Below the answer, a **sources
  panel** groups every citation under its filing, so an answer drawing on
  several filings says so.
- **Right — filing viewer:** a tab strip over up to three open filings (LRU
  eviction beyond that). Inactive panes stay mounted and hidden so each keeps
  its scroll position. Clicking a citation opens or activates its filing's tab
  and highlights the cited sids. Each pane loads `GET /filings/{accession}` and
  renders the stored HTML (sanitized at ingestion, so `dangerouslySetInnerHTML`
  is acceptable — the server is the sanitizer).

Components: `app/ask/page.tsx`, `components/answer-stream.tsx`,
`components/citation-chip.tsx`, `components/filing-viewer.tsx`.
State is one object: `{activeAccession, activeSids}` — a chip click sets it, the
viewer reacts. No global state library.

## 8. Evals

Built in Phase 2, not at the end — the eval harness is the tuning instrument for
every later chunking/prompt/retrieval change.

**Golden set** (`backend/evals/golden.yaml`, ~40 questions, hand-authored while
reading real filings):

```yaml
- id: q001
  question: "What were Apple's total net sales in fiscal 2024?"
  ticker: AAPL
  accession: "0000320193-24-000123"
  gold_sids: [1042, 1043]        # where the answer lives
  section: "item7"
```

**Harnesses** (`python -m evals run [--retrieval-only]`):

1. **Retrieval — recall@k:** for each question, do the top-k fused chunks contain
   any gold sid? Report recall@5 and recall@10. No LLM cost; run constantly.
2. **Faithfulness — end-to-end:** run the full `/ask` path; report % citations
   verified and % questions answered (vs. refused). Costs pennies; run before/after
   meaningful changes.

Each run appends `{git_sha, timestamp, metrics}` to `evals/results.jsonl` —
regressions become visible instead of vibes.

## 9. Testing

| Layer | Approach |
|---|---|
| Canonicalizer | Fixture-driven: real messy filing excerpts in `backend/tests/fixtures/`; assert sentence boundaries, sids, sections, sid-alignment between canonical text and viewer HTML |
| Chunker | Unit: section boundaries respected, token budget, disjoint sid ranges |
| Verification | Unit: normalization table (curly quotes, dashes, whitespace), match/no-match cases, offset→sid resolution |
| API | Integration: fixture filing ingested into a test DB; `/ask` with a **stubbed LLM** returning a canned answer+citations block — exercises retrieval, verification, and SSE with zero API cost |
| Frontend | Typecheck + lint in CI; component tests are v2 |

CI (existing workflow): ruff + pytest on the backend; add a frontend
typecheck/lint job when the frontend lands.

## 10. Error handling

| Failure | Behavior |
|---|---|
| EDGAR 429/5xx | Backoff + retry; resume from disk cache |
| Unrecognized filing structure | Ingest as single `"other"` section; log warning |
| LLM output unparseable | One retry; then answer renders with "unverified answer" notice |
| Citation fails verification | `verified: false`, visible badge; answer still renders |
| Retrieval returns nothing relevant | Model instructed to say the corpus doesn't cover it (and the eval set includes such questions) |
| LLM API down | `error` SSE event with a human-readable message |

## 11. Deployment

- **Runtime:** Python **3.13** everywhere — local, CI, and the API image
  (`python:3.13-slim`). Pinned rather than ranged because this is one deployed
  container, not a library: `requires-python = ">=3.13"` and the CI
  `setup-python` version must move together. Verified that the heaviest
  dependencies (`fastembed` / `onnxruntime`) ship 3.13 wheels.
- **Dev:** `docker-compose` pgvector Postgres; API and frontend run locally;
  ingestion CLI run by hand.
- **Demo:** Vercel (frontend) + Fly.io or Render (API container, includes the
  ~30 MB ONNX embedding model for query-time embedding) + Supabase (DB).
  Ingestion runs from the dev machine against the Supabase DB — no ingestion
  infrastructure to deploy.
- **Cost:** ~$0 infrastructure on free tiers; Claude usage is pennies per answer.
  A `MAX_OUTPUT_TOKENS` cap keeps per-answer spend bounded, and nothing is
  publicly deployed until Phase 5, so there is no unattended public spend before then.

## 12. Build phases

Each phase ends with something runnable; no phase depends on a later one.

| Phase | Deliverable | Exit criterion | ~Hours |
|---|---|---|---|
| 0 | Plumbing: `pyproject.toml`, docker-compose, migrations 001, CI green | `pytest` and `ruff` pass on empty skeleton | 3–5 |
| 1 | Fetch + canonicalize one AAPL 10-K | Fixtures pass; sentences + viewer HTML in DB; sids aligned | 10–18 |
| 2 | Chunk + embed + hybrid retrieval + retrieval eval | recall@10 ≥ 0.8 on first ~15 golden questions | 8–12 |
| 3 | `/ask`: generation + verification + SSE | Stubbed-LLM integration test passes; live answers cite verified quotes | 8–14 |
| 4 | Frontend: ask page + viewer + highlighting | Click a citation → exact sentences highlight | 8–14 |
| 5 | Full corpus (10 companies, 10-K+10-Q), 40-question golden set, deploy | Public demo URL; faithfulness ≥ 90% citations verified | 6–12 |

Total: **~45–75 hours.** Phases 1 and 4 carry the most uncertainty (canonicalizer
edge cases; DOM highlighting quirks).

## 13. Risks

| Risk | Mitigation |
|---|---|
| Sentence segmentation edge cases in legal text | pysbd + fixture tests; segmentation errors degrade highlighting granularity, never correctness of verification |
| Haiku produces sloppy quotes → verification failures | Faithfulness eval quantifies it; escalate model (Sonnet) or fall back to Approach B (LLM cites sids directly) — the schema already stores everything B needs |
| Filing HTML too large/slow in the viewer | Load viewer only on citation click; virtualized rendering is v2 if needed |
| Canonicalizer rabbit hole (the known hardest 20%) | Fixtures define "done"; degradation path (§4.2) bounds worst case; Phase 1 has an explicit exit criterion |
| Scope creep | §2 non-goals list; v2 ideas go to §14, not into v1 |

## 14. Explicit non-goals → v2+ backlog

XBRL structured financials (exact-number answers), 8-K support, on-demand ticker
ingestion (async jobs + progress UI), table linearization, reranker
(cross-encoder), conversation history, fine-tuned embeddings, agentic
multi-step retrieval, the `year` filter on `POST /ask` (deferred out of
Phase 3 — see §6; accession-pinning in the fiscal-period-handling spec
supersedes this by sidestepping the `filing_date`-vs-`period_end` ambiguity
that caused the original deferral).

**Query decomposition** replaces the former "multi-filing comparison
questions" entry. The rendering half of that item is built (§7: the sources
panel and tabbed viewer show an answer resting on several filings). The
retrieval half is not: one embedding of "how do Apple and Microsoft describe
supply chain risk" retrieves whichever filer's boilerplate scores highest
rather than both, so a comparison question is still answered from one company.
Splitting such a question into per-company retrievals is the v2 work.

Three specs now implement this: `docs/superpowers/specs/2026-08-29-entity-resolution-design.md`
(detecting which corpus companies a question names — implemented, this
branch), `docs/superpowers/specs/2026-08-29-fiscal-period-handling-design.md`
(identifying which filing period a question means — detector and retrieval
filter implemented on this branch, not yet wired into `/ask`'s live query
path), and
`docs/superpowers/specs/2026-08-29-query-decomposition-design.md` (running
retrieval per resolved company and merging — not yet implemented, depends on
entity resolution).

**Stock price chart.** Raised (2026-08-28) as filling the blank space under an
answer with a customizable price chart, with the cited period highlighted on
it. Out of scope as stated: it needs a market-data source the pipeline never
touches today, which is a new data source, not filing content. Needs its own
scoping pass — what "customizable" means, where the price data comes from,
how a cited sentence maps to a highlighted range on the chart — before any
implementation.

Each of these is a clean extension because of the unit boundaries in §3 — none
requires reworking the citation machinery.
