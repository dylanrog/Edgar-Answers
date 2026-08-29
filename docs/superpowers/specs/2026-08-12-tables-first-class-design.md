# Tables Are First-Class — Design

**Date:** 2026-08-12
**Status:** approved, not yet implemented
**Supersedes:** the "tables stay viewer-only" rule in `docs/design.md` §4.2

This is **spec 1 of 4** arising from a failed question (`Who had more sales in
2023, nvidia, mta, or apple`). The other three — entity resolution, fiscal
period handling, and query decomposition — get their own specs. Order matters
only between the last two: decomposition depends on entity resolution, because
a question cannot be split per company until its company names are resolved.

## 1. Problem

Financial tables are the densest source of answers in a filing, and today the
pipeline handles them by accident rather than by design. Three symptoms, all
from one bug:

| symptom | effect on the product |
| --- | --- |
| a whole table becomes one "sentence" | clicking a citation highlights an entire financial table instead of the cited row |
| table markup is destroyed in `viewer_html` | the filing renders as a run-on paragraph of numbers |
| a bare `<table>` behaves the opposite way | whether a table is indexed at all is decided by incidental HTML shape |

Measured on the current corpus:

- **719 of 13,725 chunks (5.2%)** begin with a digit, `$` or `(` — a chunk that
  starts mid-table with no column context.
- **53 sentences exceed 2,000 characters**; the longest is 3,662. These are
  flattened tables, not sentences.
- **All 120 filings** still contain some `<table>` in `viewer_html`, so the
  corpus is a mix of preserved and destroyed tables.

A user-visible consequence, and the question that started this: the model
quoted Apple's FY2023 total net sales and the citation rendered **unverified**.
The source text reads `Total net sales $ 383,285`, with a space after the
dollar sign. Verification is an exact normalized substring match, so:

```
PASS  'Total net sales $ 383,285'
FAIL  'Total net sales $383,285'      <- only difference is that space
PASS  '$ 383,285'
FAIL  '$383,285'
```

The badge was correct — the quote was not character-for-character — but it
punished formatting, not unfaithfulness.

## 2. Root cause

`canonicalize._leaf_blocks` yields `p`/`li`/`div` elements that contain no
other block, skipping any whose **ancestor** is a `<table>`:

```python
if el.find_parent("table") is not None:
    continue  # tables stay viewer-only in v1 (spec §4.2)
```

A `<div>` that **wraps** a table has no block children and no table ancestor,
so it qualifies as a leaf block. `_rewrite_block` then calls `block.clear()`
and replaces the div's contents with a single sid-tagged span:

```
<div><table>…</table></div>
  ->  <div><span data-sid="1">Products $ 298,085 Total net sales $ 383,285</span></div>
```

A bare `<table>` has no such wrapper, is never yielded, and survives untouched.
Real EDGAR filings use both shapes, which is why the corpus is inconsistent.

## 3. Scope

**In scope:** table extraction granularity, table chunking, the quote
normalization that table text requires, restoring table markup in the viewer,
the `store_filing` foreign-key bug that blocks any reprocess, the reprocess
command itself, and re-pinning the golden set.

**Out of scope:** entity resolution, fiscal periods, query decomposition (specs
2–4). Retrieval ranking — no query, filter, fusion or ranking rule changes
here. XBRL, table linearization into prose, and column-aware parsing remain
§14 backlog: a row stays a flat string, not a parsed record.

## 4. Design

### 4.1 Canonicalizer

`_leaf_blocks` becomes a document-order traversal yielding two kinds of unit:
**prose blocks** (as today) and **table rows**.

- A block containing a `<table>` descendant is a *container*, never a leaf.
  This alone stops the flattening and stops the markup destruction.
- Each `<tr>` whose text is non-empty becomes **exactly one sentence**, its
  cells joined by a single space and whitespace collapsed.
- **No sentence segmentation inside a row.** A row is not prose; pysbd would
  split `Total net sales $ 383,285 (3) % $ 394,328` at unpredictable points,
  and a row that segments differently between runs would move sids underneath
  stored citations.
- Spacer and empty rows produce no sid. EDGAR uses them heavily for layout.
- **Nested tables:** the innermost `<tr>` wins. A row containing a nested table
  is a container and is not emitted itself, so no text is indexed twice.

Cells are joined with a **single space**, not a delimiter such as `|`. The
model quotes what it is shown, and a quote containing pipes would be both
unnatural to produce and harder to verify.

### 4.2 Viewer HTML

Rows are marked by setting `data-sid` **on the `<tr>` element itself**. A
`<span>` cannot legally wrap `<td>` elements, and cell markup must survive for
the table to render. Prose blocks keep their existing span rewriting.

`lib/highlight.ts` already selects `[data-sid="…"]` generically, so no frontend
logic changes. Only the highlight CSS needs a table-aware rule: `box-shadow`
renders badly on a `<tr>`, so row highlighting uses `background-color`.

Note an intentional asymmetry: for prose, the viewer text equals the sentence
text exactly; for a row, the viewer keeps original cell markup whose whitespace
may differ from the flattened sentence string. Highlighting resolves by sid,
never by text matching, so this is safe.

### 4.3 Chunking

A table's rows chunk together as **one atomic unit**, even when that exceeds
`MAX_TOKENS` (450) — the same escape hatch `chunk_sentences` already grants an
over-long single sentence. Headers therefore always travel with their data.

This requires the chunker to know which sentences share a table, so `Sentence`
gains `table_id: int | None` (`None` for prose). Because `load_sentences`
round-trips sentences through the database and `recanonicalize` compares
freshly computed sentences against stored rows for equality, the field must
persist — hence the migration in §5.

Rejected alternatives:

- *Let tables split at 450 tokens.* Continuation chunks lose their header row,
  so the model receives `Europe 94,294 (1) %` with no indication of what
  94,294 measures. This is the 5.2% of chunks that already start mid-table.
- *Repeat the header row's text in continuation chunks.* This breaks
  verification silently. `verify.sentence_spans` reconstructs chunk text as
  `" ".join(...)` of exactly the chunk's `sid_start..sid_end` sentences;
  injecting text that belongs to no sentence in that range desynchronizes every
  offset after it, and citations resolve to the **wrong sids**. Its docstring
  warns about precisely this coupling.
- *Overlapping sid ranges, so a continuation chunk starts at the header sid.*
  Offsets stay correct, but it breaks the documented invariant that chunks are
  disjoint and every sentence belongs to exactly one chunk.

**Accepted cost:** `bge-small-en-v1.5` truncates at 512 of its own tokens, so a
very large table chunk loses its tail from the vector arm. The lexical (FTS)
arm indexes the full text, so those rows stay retrievable. This is already true
of the 53 over-long sentences today, so it is not a new risk.

### 4.4 Quote verification

Extend `api.normalize.normalize` so that a whitespace run is dropped — emitting
nothing — when the previously emitted character is one of `$€£#(` or the next
character is one of `)%`. `$ 383,285` and `$383,285` then normalize
identically, as do `(3) %` and `(3)%`.

The offset-map contract is preserved: dropping characters is already how
whitespace runs behave, and the map is built per source character.

This is deliberately narrow. Verification's value is that it is strict, and a
general "ignore all whitespace" rule would let a quote match across word
boundaries that never existed in the source. The spec pins a negative case:
`net sales` must not normalize such that it matches `netsales`.

### 4.5 `store_filing` foreign-key bug (prerequisite)

`store_filing(replace=True)` deletes the filing's `sentences`, then the
`filings` row, but never its `chunks`. `chunks.filing_id REFERENCES filings(id)`
has no `ON DELETE CASCADE`, so the delete fails:

```
ForeignKeyViolation: update or delete on table "filings" violates foreign key
constraint "chunks_filing_id_fkey" on table "chunks"
```

Every filing in the corpus is embedded, so **`ingest --force` is broken on the
real corpus today**. It works in tests only because test filings have no
chunks, and the 2026-08-05 re-ingest never reached it because every filing was
skipped as already-stored.

Fix: delete the filing's `chunks` before deleting the `filings` row, beside the
existing `sentences` delete. Kept explicit rather than adding
`ON DELETE CASCADE`, because a cascade would make any future filing deletion
silently discard embeddings, and an explicit delete keeps the destruction
visible at the call site. This lands first, with its own regression test, since
nothing else in this spec can run without it.

### 4.6 `reprocess` command

```
python -m pipeline reprocess [--ticker TICKER] [--dry-run]
```

Re-canonicalizes from cached raw HTML and replaces sentences, chunks and
embeddings, in **one transaction per filing**. No EDGAR traffic — all 120
filings are cached under `backend/data/raw/`. `--dry-run` reports how many sids
would move per filing and writes nothing.

`recanonicalize` is left exactly as it is: the viewer-only tool that **aborts**
when sentences move. `reprocess` is its deliberate opposite. Keeping both, with
the contrast documented in `--help`, is what stops someone reaching for the
destructive one out of habit.

### 4.7 Golden set re-pinning

Row-level sentences move gold sids, and the golden set is the instrument that
tells us whether any of this worked.

```
python -m evals repin            # proposes only; writes a mapping file, never touches golden.yaml
python -m evals repin --apply    # applies a mapping file a human has reviewed
```

Proposing is the default and applying requires the explicit flag — the reverse
of `reprocess`, where `--dry-run` is opt-in. The asymmetry is deliberate:
reprocessing wrong costs a re-run, while re-pinning wrong silently invalidates
every measurement taken afterwards.

For each golden entry the helper matches the stored gold sentence text against
the new sentences and proposes an old → new mapping. Table-derived golds need
this most: the old text is a whole table and the new text is one of its rows,
so the helper proposes candidate rows and a human chooses. An entry it cannot
map confidently is reported as unresolved rather than guessed.

## 5. Data model

One numbered migration, `002_sentence_table_id.sql`:

```sql
ALTER TABLE sentences ADD COLUMN table_id integer;
```

Nullable, `NULL` for prose sentences. The sentence `INSERT` inside
`store.store_filing` and the `SELECT` in `store.load_sentences` both carry it,
so `Sentence` equality — which `recanonicalize` depends on — stays meaningful.
(There is no separate `store_sentences` function; sentences are written as part
of `store_filing`.)

## 6. Invariants

These must hold after the change, and each gets a test:

1. Every sid appears exactly once in `viewer_html`.
2. No source text is indexed twice (the nested-table trap).
3. `chunk.text == " ".join(sentences in its sid range)` — the contract
   `verify.sentence_spans` depends on.
4. A table's rows never split across chunks.
5. `<table>` survives into `viewer_html` for both bare and div-wrapped input.
6. Chunks remain contiguous, disjoint sid ranges.

## 7. Testing

New fixtures in `backend/tests/fixtures/`: bare table, div-wrapped table,
nested table, spacer rows, header row, and a table large enough to cross a
chunk boundary. Per project convention, a fixture is added **before** the
extraction behavior it pins changes.

Test coverage beyond the invariants above:

- `normalize()` currency and percent spacing, positive and negative cases.
- `store_filing(replace=True)` succeeds on a filing that already has chunks
  (the §4.5 regression).
- `reprocess --dry-run` writes nothing.
- `reprocess` leaves a filing's citations resolvable: sids exist, chunks
  reference live sid ranges.
- Existing canonicalizer fixtures still pass unchanged.

## 8. Evals and measurement

Eval runs before and after, on clean trees.

- `verified_rate` is the metric this work targets: expect stable or up.
- Retrieval recall may shift, because chunk boundaries genuinely change. That
  is expected, not automatically a regression.
- **`gold_sid_hit_rate` is not comparable across the re-pin.** The sids it
  scores against are redefined by this change. This must be stated in the
  results commit so a future reader does not mistake the discontinuity for a
  signal.

## 9. Risks

| risk | mitigation |
| --- | --- |
| `reprocess` destroys derived data | per-filing transaction; `--dry-run`; raw HTML is cached, so the source of truth is never at stake |
| a re-pin quietly corrupts the golden set | mappings are proposed, never applied silently; human confirms each |
| over-broad normalization creates false-positive verifications | rule is narrow and character-specific; a negative test pins it |
| large table chunks truncate at the embedding limit | lexical arm indexes full text; already true today |
| sentence extraction changes break stored citations | that is the point of this change, which is why `recanonicalize` stays strict and `reprocess` is a separate, explicit command |

## 10. Sequencing

1. `store_filing` chunks-delete bugfix, with regression test (§4.5).
2. Migration `002_sentence_table_id.sql` and the `Sentence.table_id` field.
3. Canonicalizer: rows as sentences, containers, `data-sid` on `<tr>` (§4.1–4.2).
4. Chunker: table-atomic chunks (§4.3).
5. `normalize()` currency spacing (§4.4).
6. `reprocess` command, with `--dry-run` first (§4.6).
7. Baseline eval run, reprocess the corpus, `evals repin`, second eval run.
8. Frontend CSS for row highlighting.
9. `docs/design.md` §4.2 rewrite and `CLAUDE.md` update.

## 11. Deferred

- Column-aware parsing of rows into records — a row stays a flat string.
- Repeating headers into continuation chunks, which §4.3 rules out on
  correctness grounds rather than effort.
- `<img>` inside filings still 404s against the frontend origin (75 of 120
  filings). Cosmetic, unrelated, unfixed.
