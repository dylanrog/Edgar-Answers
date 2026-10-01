# Table Column Binding and Header-Carrying Table Chunks — Design

**Date:** 2026-09-29
**Status:** proposed
**Part of:** a two-spec series making every number in an answer checkable.
This is spec 1. Spec 2 (numeric claim verification: prose numbers and
declared calculations checked against verified quotes) is not yet written and
depends on this one.
**Builds on:** `2026-08-12-tables-first-class-design.md`, whose §11 deferred
"column-aware parsing of rows into records" and whose §4.3 ruled out split
table chunks. This spec does the first and reverses the second, for reasons
given in §5.

## 1. Problem

The product promises that an answer cannot be wrong because each fact traces
to verified source text. Two gaps break that promise wherever a number comes
from a table.

**Gap 1: a verified row does not verify a column.** A table row is one
sentence. The model quotes it, the quote matches, the citation verifies. But
the quote proves only that a number is *in the row*. Which column it sits in
— which period, which segment — and what scale it is printed in are facts
held by *other* sentences (the header rows) that the model never quotes.
NVIDIA's FY2025 10-K:

```
sid 2765  Year Ended
sid 2766  Jan 26, 2025 Jan 28, 2024 Jan 29, 2023
sid 2767  Revenue by End Market: (In millions)
sid 2768  Data Center $ 115,186 $ 47,525 $ 15,005
```

In NVIDIA's FY2026 10-K the same `115,186` sits in the *second* column:

```
sid 2669  Jan 25, 2026 Jan 26, 2025 Jan 28, 2024
sid 2671  Data Center $ 193,737 $ 115,186 $ 47,525
```

A model that misreads the columns and reports "$115,186 million in fiscal 2026"
gets a green verified badge. The arithmetic spec cannot check operands either
until a number carries its column and scale in a form code can read.

**Gap 2: the bottom of a long table is invisible to vector search.** The
tables spec chunked every table atomically so a header always travels with its
data. Measured on the current corpus:

| | chunks | over the 450-token budget |
| --- | --- | --- |
| prose | 7,922 | 119 (1.5%) |
| table | 7,510 | **3,480 (46%)** |

`bge-small-en-v1.5` truncates at 512 of its own tokens, so for those 3,480
chunks the lower rows contribute nothing to the vector arm, and one embedding
of a 1,900-token table is a blurred average of many rows. The lexical arm and
the model still see the whole chunk; the vector arm does not.

## 2. What real EDGAR tables look like

Three excerpts drive the parsing rules. `^n` is `colspan=n`; `''` is an empty
cell.

**NVIDIA, revenue by end market** — columns align by colspan, not cell index.
`115,186` is the fifth `<td>` in its row but sits at grid column 4, under
`Jan 26, 2025` (grid columns 3–5). A row without `$` has differently shaped
cells.

```
''^3 | 'Year Ended'^15
''^3 | 'Jan 26, 2025'^3 | ''^3 | 'Jan 28, 2024'^3 | ''^3 | 'Jan 29, 2023'^3
'Revenue by End Market:'^3 | '(In millions)'^15
'Data Center'^3 | '$' | '115,186' | '' | ''^3 | '$' | '47,525' | '' | ...
'Compute'^3     | '102,196'^2 | '' | ''^3 | '38,950'^2 | '' | ...
```

**Apple, segment operating results (10-Q)** — the period header **changes
mid-table**, and the scale is not in the table at all; it is in the preceding
prose sentence (`… ended March 28, 2026 and March 29, 2025 (in millions):`).

```
''^3 | 'Three Months Ended March 28, 2026'^39
''^3 | 'Americas'^3 | ''^3 | 'Europe'^3 | ...
'Net sales'^3 | '$' | '45,093' | ...
'Cost of sales'^3 | '( 23,114 )'^2 | ...
...
''^3 | 'Three Months Ended March 29, 2025'^39      <- second header band
''^3 | 'Americas'^3 | ''^3 | 'Europe'^3 | ...
'Net sales'^3 | '$' | '40,315' | ...
```

**Microsoft, segment results** — year headers look numeric; the row label
`Revenue` repeats under each segment's group row; scale and an exception share
one header cell; there is a percentage column.

```
'(In millions, except percentages)' | '' | '2026'^2 | '' | '' | '2025'^2 | '' | '' | 'Percentage Change'^2
'Productivity and Business Processes'
'Revenue' | '' | '$' | '139,996' | '' | '' | '$' | '120,810' | '' | '' | '' | '16%'
```

## 3. Scope

**In scope:**

- Column binding: parsing each numeric table cell into a record carrying its
  row label, column label, value, kind and scale, persisted in new tables.
- Splitting over-budget tables into multiple chunks, each carrying a
  `context` string that is embedded, lexically indexed (amended, §12: not lexically indexed) and shown to the model
  but never used for verification.
- A per-table cap on retrieval slots.
- Cited-figure highlighting: when a citation's quote covers figures in a
  table row, those cells stand out inside the highlighted row (§5.7).
- Evals first: new golden questions and a `value_accuracy` metric, with a
  baseline recorded before any implementation lands.
- A corpus coverage report for column binding.

**Out of scope:**

- **Date or fiscal-year parsing.** `column_label` is stored as printed.
  Fiscal-calendar modelling is out of scope for this whole series: spec 2
  checks the *printed period-end date* a number sits under, never a derived
  fiscal-year name, so no company's fiscal calendar is ever needed.
- **Numeric claim verification** (spec 2): prose-number checks, declared
  calculations, the `calculation` SSE event, new badges.
- **Reranking and MMR.** The cap in §6.3 is designed as a separate step after
  scoring so a reranker can be inserted before it later.
- `rowspan`, row hierarchy by indentation, and the `<img>` 404s noted in
  CLAUDE.md.

## 4. Column binding

### 4.1 The invariant this must not break

Cells are produced in **the same DOM traversal** that already produces row
sentences, and that traversal must not change what it already produces.
Sentences, sids, canonical text and viewer HTML stay **byte-identical** to
today's output. Table parsing only *reads* the `<table>`; it never mutates the
DOM. This is testable (§8.2), and it is what lets the golden set's sid pins
survive the rebuild untouched.

### 4.2 Data model

Migration `004_table_cells.sql`:

```sql
CREATE TABLE filing_tables (
    filing_id  bigint  NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    table_id   integer NOT NULL,
    caption    text,              -- nearest preceding prose sentence, or NULL
    scale      numeric,           -- 1e3 | 1e6 | 1e9, or NULL when unknown
    splittable boolean NOT NULL,  -- §4.3 rule 7
    PRIMARY KEY (filing_id, table_id)
);

CREATE TABLE table_cells (
    filing_id     bigint  NOT NULL,
    sid           integer NOT NULL,  -- the row's sentence
    col           integer NOT NULL,  -- grid column where the cell starts
    cell_index    integer NOT NULL,  -- position among the <tr>'s td/th (DOM tr.cells[i])
    char_start    integer,           -- cell's span in the row sentence's text,
    char_end      integer,           --   half-open; NULL if alignment fails (§5.7)
    table_id      integer NOT NULL,
    raw           text    NOT NULL,  -- as printed, e.g. '( 23,114 )'
    value         numeric,           -- signed, as printed, NOT scaled; NULL for nil
    kind          text    NOT NULL,  -- 'number' | 'percent' | 'nil'
    row_label     text,              -- e.g. 'Intelligent Cloud › Revenue'
    column_label  text,              -- e.g. 'Year Ended › Jan 26, 2025', or NULL
    scale_applies boolean NOT NULL,  -- false for percent cells, per-share rows
    PRIMARY KEY (filing_id, sid, col),
    FOREIGN KEY (filing_id, table_id)
      REFERENCES filing_tables (filing_id, table_id) ON DELETE CASCADE
);

ALTER TABLE chunks ADD COLUMN context  text NOT NULL DEFAULT '';
ALTER TABLE chunks ADD COLUMN table_id integer;  -- set only on pieces of a split table
```

The FTS index change is in §5.4. `CanonicalFiling` gains `tables` and `cells`
lists (Python-side names; the database table is `filing_tables`, named to
avoid confusion with `information_schema.tables`); `store_filing`, `delete_derived` and `reprocess` write and clear them
alongside sentences.

`value` is stored **unscaled** because spec 2 matches numbers against quote
text, which is printed unscaled; scaling is a separate, explicit
multiplication by `filing_tables.scale` when `scale_applies`.

**The governing rule: when unsure, store NULL, never a guess.** A NULL
`column_label` or `scale` means spec 2 cannot verify a number that depends on
it, so the user sees an unverified badge. A wrong label would put a green badge
on a wrong answer, which is the failure this series exists to remove.

### 4.3 Parsing rules

Applied once per `<table>` when its first row is reached in the traversal.

1. **Grid.** Expand `colspan` so each cell has a half-open grid range
   `[start, end)`. `rowspan` is ignored (the cell counts for its own row only).
2. **Classify cells.** After stripping whitespace, `$`, `,`:
   - matches `^\(?-?\d+(\.\d+)?\)?%?$` → numeric. Surrounding parentheses mean
     negative (`( 23,114 )` → `-23114`). A trailing `%` — or a lone `%` cell
     immediately to its right in the grid — makes it `percent`. A lone `)`
     cell to its right closes an unmatched `(`.
   - `—`, `–`, `-` alone → `nil` (value NULL).
   - a lone `$`, `%`, `)`, `(` or empty → filler, ignored.
   - anything else → text.
3. **Label columns.** The grid columns before the leftmost column in which
   any numeric cell of the table starts, ignoring year-only rows (rule 4) —
   so the year check runs before this rule, not after. Cells in label columns
   are never numeric (a footnote marker `(1)` in a label is text).
4. **Classify rows.**
   - **data row:** at least one numeric or nil cell outside the label columns.
   - **header row:** no data cells, and text in at least one value column. A
     row whose only numeric-looking cells are bare years — a 4-digit integer
     1990–2100 printed with no `$`, `,`, `.`, `%` or parentheses — is a header
     row, not a data row (the Microsoft `2026`/`2025` trap).
   - **group row:** text only in the label columns, no data cells.
   - **spacer row:** no text; already produces no sid.
5. **Header bands.** Consecutive header rows form a band; spacer rows do not
   break it. **A band that appears after data rows replaces the current band**
   (the Apple mid-table case). A numeric cell's `column_label` is the text of
   every band cell whose grid range overlaps the numeric cell's range, top row
   first, joined by ` › `, with scale phrases (rule 6) removed. If any single
   band row has **two or more** non-empty cells overlapping the numeric cell,
   `column_label` is NULL.
6. **Scale.** Search the current band's text, then the caption, for
   `in (millions|thousands|billions)` (case-insensitive; also matches
   `dollars in millions`). First match wins; none → `scale` NULL. When the
   matched text contains `except per share` / `except per-share` /
   `except percentages`, rows whose label contains `per share` get
   `scale_applies = false`. Percent cells always get `scale_applies = false`.
7. **Row labels and splittability.** `row_label` is the label-column text,
   prefixed with the most recent group row's text and ` › `. A new band clears
   the group. A table is **splittable** only if every data row has a band
   above it.
8. **Caption.** The nearest preceding sentence with `table_id` NULL, in the
   same section, with no other table between it and this one. None → NULL.

Accepted limits: `rowspan`; nesting deeper than one group row; tables whose
header is laid out as the first *column* rather than the first rows (these get
NULL labels, which is correct under rule 5 rather than wrong).

## 5. Chunking with context

### 5.1 Why this is now allowed

The tables spec rejected continuation chunks because the header would be lost,
and rejected re-injecting the header into `text` because
`verify.sentence_spans` reconstructs chunk text as `" ".join(...)` of exactly
the chunk's sentences; foreign text shifts every offset and citations resolve
to the wrong sids. The fix is to **separate a chunk's context from its
verified text**: `text` is unchanged, the new `context` column carries the
header. Chunks stay disjoint and verification's arithmetic is untouched, so
both documented invariants hold.

### 5.2 The chunker

Let `budget(table) = MAX_TOKENS − tokens(context for that table)`.

- **A table whose rows fit its budget** is never split and joins the greedy
  run with surrounding prose, as today. *(Amended, §12: it is NOT costed as a
  whole — that rule moved chunk boundaries corpus-wide and cost recall; a fitting
  table continues the current chunk exactly as before.)*
- **A table that is over budget and not splittable** stays atomic, as today.
  Nothing gets worse than now.
- **A table that is over budget and splittable** is isolated: its first piece
  starts a new chunk and its last piece ends one, so a piece never shares a
  chunk with prose or another table *(amended, §12: except its lead-in caption
  sentence, which moves into the first piece)*. It is split greedily at row boundaries
  against `budget(table)`, rows are never split, and a single row over budget
  becomes its own chunk (the existing escape hatch). **Band boundaries are
  preferred:** when a new band's header rows begin and the whole band would
  not fit in the current piece's remaining budget, the piece ends there. (A
  group row after data counts as a block start too; it is an equally natural
  break.) A piece never ends on header rows: if the budget runs out right
  after them, they move to the next piece with the data they label. Every
  piece gets `chunks.table_id`.

`MAX_TOKENS` stays 450 and now counts context tokens for table chunks,
because the embedder sees both. *(Amended, §12: context counts only when deciding
whether a table must split and when sizing its pieces; the greedy walk counts text.)*

### 5.3 The context string

Built deterministically from `filing_tables` and `table_cells`, so the `embed` path
can rebuild it from the database without re-reading HTML. One line per table
the chunk contains a data row of, built from the **first data row of that
table in the chunk**, with its parts joined by ` | `:

```
Table: The following table summarizes revenue by specialized markets: | Scale: in millions | Columns: Year Ended › [Jan 26, 2025; Jan 28, 2024; Jan 29, 2023]
Table: SEGMENT RESULTS OF OPERATIONS | Scale: in millions | Columns: 2026; 2025; Percentage Change | Group: Intelligent Cloud
```

- `Table:` — the caption truncated to 200 characters; omitted when NULL.
- `Scale:` — omitted when NULL.
- `Columns:` — distinct non-NULL `column_label`s of that row, in grid order.
  A label prefix shared by every column is written once
  (`Year Ended › [Jan 26, 2025; …]`): Apple's segment tables repeat a
  38-character period on all seven columns, and factoring it cut that
  context from 141 to 78 tokens.
- `Group:` — only when the row's label has a group prefix.

Every table chunk carries context, not only continuation pieces. Prose-only
chunks have `context = ''`.

### 5.4 Where context flows

| consumer | uses context? |
| --- | --- |
| embedding input | yes: `context + "\n" + text` when context is non-empty |
| lexical index | yes: migration 004 replaces `chunks_text_fts` with an index on `to_tsvector('english', context \|\| ' ' \|\| text)`; `retrieval._TSVECTOR` changes to match exactly. *Amended, §12: **no** — migration 005 restores a text-only index* |
| prompt | yes, as a labelled line above the excerpt (§5.5) |
| `verify.py` | **no** — only `chunk.text` |
| SSE events, frontend | no change |

`RetrievedChunk` gains `context` and `table_id`.

### 5.5 Prompt

`build_user_message` renders a table chunk as:

```
[chunk_id=8123] NVDA 10-K 0001045810-25-000023 (item7)
Table context (for reading columns; not quotable): Table: … | Scale: in millions | Columns: …
Data Center $ 115,186 $ 47,525 $ 15,005 …
```

`SYSTEM_PROMPT` gains two rules: quotes come only from excerpt text, never from
a table-context line; and when quoting a table row, quote from the row label
through the figure being used and stop there (so the cited-figure highlight of
§5.7 lands on that figure rather than the whole row). A model that quotes context anyway fails verification
and shows the unverified badge — the honest failure, visible as a drop in
`verified_rate`. Rule placement is load-bearing (CLAUDE.md), so it is part of
the evaluated change, not a cosmetic edit.

### 5.6 Per-table retrieval cap

Splitting lets one table produce several highly relevant pieces, which could
fill the 8 slots and push out other filings and periods. `retrieve()`
currently takes the top `k_final` of the RRF-fused list. It changes to walk
that list in rank order and **skip any chunk whose `(filing_id, table_id)`
already holds `MAX_CHUNKS_PER_TABLE = 2` slots**, taking the next candidate
instead. Both arms return up to `k_each = 20`, so up to 40 candidates back-fill
freed slots.

- The cap is 2, not 1, so a year-over-year question can receive both the
  current-period and prior-period bands of one split table (Apple's segment
  table, §2).
- It lives inside `retrieve()`, so it holds for scoped, unfiltered and every
  target of a multi-company question.
- A reranker does not replace it: a pointwise reranker scores each chunk
  alone and would score all sibling pieces highly. The intended future order
  is hybrid → RRF → rerank → cap → top 8, so the cap is a separate function
  applied after scoring.

### 5.7 Cited-figure highlighting

Today a table citation highlights its whole row. With cells known, the figures
the quote actually covers can stand out within that row.

**Cell spans.** A row sentence's text is its cells' texts joined by single
spaces (`" ".join(tr.get_text(" ", strip=True).split())`). While parsing, each
stored cell records `char_start`/`char_end` — its span within that row text —
by walking the row's `td`/`th` in order and accumulating the same join. The
parser asserts the accumulated string equals the row sentence; on mismatch the
row's cells get NULL spans and the citation falls back to row highlighting
(NULL over guess, again). `cell_index` is the cell's position among the row's
`td`/`th`, which is exactly the browser's `tr.cells[i]` in the stored viewer
HTML, so **viewer HTML is not modified** and §4.1 holds.

**Resolution.** `verify.py` already maps a matched quote to chunk-text offsets
and to sids. For each resolved sid that is a table row, it intersects the
match with the row's span, converts to row-relative offsets, and selects the
stored numeric cells whose `[char_start, char_end)` overlaps. Nil cells are
included; `$`/`%` filler cells are not stored, so they are never emphasized.

**Interface.** The `citation` SSE event gains an additive field:

```
"cells": [{"sid": 2768, "cell": 4}]   -- empty for prose or unresolvable rows
```

`lib/highlight.ts` `applyHighlight` takes the cells alongside the sids, adds a
second class (`cited-figure`) to `tr[data-sid=sid].cells[cell]`, and scrolls to
the first cited figure when there is one. The row keeps its existing
`cited-sentence` background; `cited-figure` is a stronger treatment (bold,
outlined cell) defined in `app/globals.css` for the viewer's dark theme.

**Limit.** Quotes must be contiguous, so a quote ending at a second-column
figure also covers the first column's figure, and both are emphasized. That
is an honest picture of what was quoted, never less informative than today's
whole-row highlight. Spec 2's declared operands name the exact cell and will
tighten this.

## 6. Coverage report

`python -m pipeline table-report [--ticker T]` prints, over stored
`filing_tables`/`table_cells`:

- share of numeric cells with a non-NULL `column_label`;
- share of tables with a known `scale`;
- share of tables that are splittable;
- the 10 filings with the lowest labelled-cell share.

No pass/fail gate in this spec. It measures column binding beyond the
fixtures, and tells spec 2 how much of the corpus it can verify.

## 7. Evals first

The golden set has 20 entries, 16 of them AAPL-only, and cannot see either
gap: no question targets a table's tail, and `gold_sid_hit_rate` credits the
right *row* regardless of which column's number the answer used.

### 7.1 New questions

14 entries across at least five companies, each with a `category`:

- **`table_tail`** (8): the gold row lies beyond the first 450 tiktoken
  tokens of its chunk *in the current corpus* — outside what the vector arm
  embeds today. Candidates are drawn from the database (over-budget table
  chunks, rows deep inside them); each question is written about one specific
  row.
- **`column`** (6): the answer is a single cell of a multi-period or
  multi-band table, e.g. NVIDIA Data Center FY2025 (whose value appears in a
  different column of the following year's 10-K) and an Apple segment figure
  from a table's second band. Each carries `expected_values`.

Claude drafts questions and pins from the database; **Dylan checks every entry
against the filing viewer before it is committed** (design §2: eval answers
are hand-verified), and `python -m evals verify` validates every pin.

### 7.2 Schema and metrics

`golden.yaml` entries gain two optional keys:

- `category` — `table_tail` | `column`; absent on existing entries.
- `expected_values` — a list of accepted-spelling lists, e.g.
  `[["115,186", "115.2 billion"]]`. Every inner list must have at least one
  spelling present in the answer.

New metrics appended to `results.jsonl`:

- **`value_accuracy`** — over entries with `expected_values`, the share whose
  answer text (after `api.normalize.normalize`) contains, for every inner
  list, at least one of its spellings (also normalized). Deterministic, no
  LLM judge.
- **`table_tail_recall@10`** — scoped `recall@10` restricted to
  `category: table_tail`.

Existing metrics keep their definitions; their denominators grow, so rows
before this change are not comparable to rows after it, which is why the
baseline is re-run (§9).

## 8. Testing

### 8.1 Fixtures first

Per CLAUDE.md, the §2 excerpts are snipped from cached raw HTML into
`backend/tests/fixtures/` (NVIDIA revenue, Apple segment with two bands and a
caption-only scale, Microsoft segment with year headers, groups and percents).
Tests assert exact `table_cells` records. Negative fixtures: two header cells
over one number (→ `column_label` NULL) and a table with data rows but no
header band (→ not splittable, labels NULL).

### 8.2 Invariant tests

- Canonicalizing every existing fixture yields byte-identical sentences,
  sids, canonical text and viewer HTML before and after this change.
- Every chunk's `text` equals `" ".join` of its sentences; chunks are
  disjoint and cover every sentence.
- Context tokens plus text tokens ≤ `MAX_TOKENS` for every piece of a split
  table (single over-long rows excepted).
- `verify_citation` is unaffected by a non-empty `context` (a test passes a
  chunk whose context contains the quoted string and asserts `verified` is
  false).
- With five pieces of one table ranked top, `retrieve()` returns at most two
  of them and back-fills from the next candidates.
- Cell spans: for every fixture row, slicing the row sentence at each cell's
  `[char_start, char_end)` returns that cell's text.
- `verify_citation` returns `cells` for a quote ending at a table figure, none
  for a prose quote, and none (with the row still resolved) when spans are
  NULL.
- Frontend: a vitest spec for `applyHighlight` with cells (the figure gets
  `cited-figure`, the row keeps `cited-sentence`, the reducer passes `cells`
  through), and the existing `e2e/highlight.spec.ts` gains an assertion that
  a cited figure is emphasized.
- The FTS expression in `retrieval._TSVECTOR` matches the migration's index
  expression (string equality test, since a mismatch silently degrades to a
  sequential scan).

DB-dependent tests carry `@pytest.mark.db` as usual.

## 9. Rollout

Two PRs.

**PR A — evals (branch `table-evals`).** This spec, the implementation plan,
the new golden entries, the schema keys and the two metrics. Run the full eval
**at least three times** on this branch's code against the current corpus and
commit those rows to `results.jsonl`: this is the baseline. Merge.

**PR B — implementation (branched from `main` after PR A merges).**

1. **Cells.** Migration 004, parsing, and a `retable` command modelled on
   `recanonicalize`: per filing, re-parse the cached raw HTML, **assert
   sentences are unchanged**, write `filing_tables` and `table_cells` in one
   transaction. No retrieval change, so no eval run; run `table-report` and
   record its numbers in the PR description. Cited-figure highlighting
   (§5.7: cell resolution in `verify.py`, the `cells` SSE field, frontend)
   ships in this step, since it needs only cells.
2. **Chunks.** The chunker, context, prompt rule, cap and FTS index change,
   plus a `rechunk` command: per filing, in one transaction, delete chunks,
   re-chunk from stored sentences + cells, re-embed. (The existing `embed`
   stage only embeds filings with no chunks; `rechunk` is the explicit
   rebuild.) Then run the eval at least three times and compare with PR A's
   rows. No `evals repin`: sentences did not change, and the golden set pins
   sids, not chunk ids.
3. **Docs.** `docs/design.md` §4.2–4.3, §5, §6.1–6.4, §7; CLAUDE.md.

**What to compare:** `table_tail_recall@10` (expected to rise), `value_accuracy`
(expected to rise), `recall@10` scoped and unfiltered, `verified_rate` (watch
for context being quoted), `answered_rate` (q004 and q009 are the known period
failures). `gold_sid_hit_rate` is known to vary between identical runs, so it
is read only across the repeated runs, never from one.

## 10. Risks

| risk | mitigation |
| --- | --- |
| parser assigns a wrong `column_label` | NULL-over-guess rules (§4.3 rule 5); fixtures; coverage report surfaces worst filings |
| canonicalizer change moves sids | cells are read-only on the DOM; byte-identical invariant test; `retable` asserts per filing |
| model quotes the context line | explicit prompt rule; failure shows as unverified, measured by `verified_rate` |
| split pieces crowd retrieval | per-table cap of 2 (§5.6), enforced by construction |
| more chunks slow ingest | local embedding; one-off `rechunk`; estimated a few thousand extra chunks |
| lexical index mismatch falls back to seq scan | string-equality test between `_TSVECTOR` and the migration |
| model quotes a whole row, so every figure is emphasized | prompt rule (§5.5); worst case equals today's whole-row highlight |
| new golden entries are wrong | hand-checked by Dylan in the viewer; `evals verify` on every pin |

## 11. Deferred

- Spec 2, numeric claim verification, including parsing printed dates in
  `column_label` for comparison against a model-declared column.
- Reranking (possibly Jev) and MMR, inserted before the cap.
- `rowspan`, multi-level row hierarchy, column-oriented headers.

## 12. Amendments after the corpus rollout (2026-09-30)

Measured on the full corpus during PR B; the plan's code was changed to match.

- **§4.3 rule 1 — `rowspan` is honoured, not ignored.** Ignoring it did not give NULL
  labels as assumed: header rows below a `rowspan` cell shifted left and bound to the
  wrong columns (JPM 1Q26 net revenue labelled "4Q25"; ~4.8% of labelled cells in a
  20-filing sample). The grid now carries a rowspan occupancy map over all of a table's
  own `<tr>`s. Two further NULL-over-guess guards: two figures in one row sharing a label
  both get NULL; a bare year in the label columns is label text, not a figure; a
  scale-phrase-only row is not a group row. Known remaining limit: JPM level-3
  rollforward tables whose header cells span only the gap columns (browser-identical grid).
- **§5.2 — only split what must be split.** "A fitting table is costed whole and starts a
  new chunk if it does not fit after the preceding prose" changed boundaries corpus-wide,
  separated tables from their lead-in sentences and (with a follow-up "a table ends its
  chunk" rule) fragmented prose; scoped recall@10 fell from 0.56 to 0.29–0.35. The chunker
  now reproduces the pre-change greedy chunker exactly for every chunk that does not
  involve an over-budget splittable table; only those tables are isolated and split. A
  split table's lead-in (caption) sentence moves into its first piece, and a chunk whose
  text holds the caption omits `Table:` from that table's context line. The 450 budget
  counts context only when deciding whether a table must split.
- **§5.4 — the lexical index is text-only.** Context in the FTS index slightly hurt
  (column-heading years match almost every question); migration 005 restores
  `to_tsvector('english', text)`. Context stays in the embedding — ablation: removing it
  dropped table-tail recall from 0.375 to 0.125 — and in the prompt.
- **§5.6 — the cap stays** (neutral on the current golden set; it guards a future
  reranker from filling slots with sibling pieces).

Final eval (×3, `git_dirty: false`, rows at git_sha 02cfe57 / 570b2cb / 1b9c7f5),
against two "before" references: PR A's recorded rows, and a like-for-like
re-measurement of the pre-change corpus (the corpus dump restored, `main`'s code,
same harness; its retrieval numbers differ from the recorded rows because the
restored HNSW index planned filtered vector queries differently — see CLAUDE.md).

| metric | PR A rows ×3 | pre-change, like-for-like | final ×3 |
| --- | --- | --- | --- |
| recall@10 (ticker-scoped) | 0.5294 | 0.5588 | 0.6176 |
| unfiltered_recall@10 | 0.4706 | 0.4706 | 0.5294 |
| targeted_recall@10 (production path) | 0.5294 | 0.5588 | 0.5882–0.6176 |
| table_tail_recall@10 (ticker-scoped) | 0.375 | **0.50** | **0.375 (−1 of 8)** |
| targeted table-tail recall@10 * | — | 0.50 | 0.625 |
| value_accuracy | 0.36–0.57 | — | 0.5714 (all 3 runs) |
| answered_rate | 0.71–0.74 | — | 0.74–0.76 |
| gold_sid_hit_rate | 0.41–0.44 | — | 0.4706 |
| verified_rate | 0.92–0.975 | — | 0.89–0.95 |

\* scratch measurement (`run_retrieval_eval`'s targeted arm restricted to the 8
`table_tail` entries, live detectors), not recorded in `results.jsonl`.

Like-for-like, ticker-scoped table-tail recall went **down by one question**:
t003/t004/t006's gold rows now sit in ~280-token split pieces that compete with the
same table's pieces from the company's other filings (the old 800–975-token atomic
chunk won lexically). With the period targeting production uses, tail recall rises.
verified_rate's low run (0.8947) is ordinary misquotes (joined cells); an extra pass
found no quote taken from a context line.
