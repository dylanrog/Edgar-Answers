# Tables Are First-Class Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Index financial tables deliberately — one sentence per table row, a table's rows chunked atomically — so citations land on the cited row, tables render as tables, and a quote of `$ 383,285` verifies.

**Architecture:** The canonicalizer's traversal is rewritten to yield three unit kinds in document order (leaf prose block, run of loose inline content, table row) instead of only leaf blocks. Rows carry `data-sid` on the `<tr>` itself, so cell markup survives. `Sentence` gains `table_id`, which lets the chunker keep a table whole. A narrow `normalize()` rule makes currency spacing irrelevant to verification. Because sids move, a new `reprocess` command rebuilds sentences, chunks and embeddings from cached HTML, and `evals repin` re-anchors the golden set.

**Tech Stack:** Python 3.13, psycopg 3, BeautifulSoup + lxml, pysbd, tiktoken, fastembed, pytest. Frontend: Tailwind 4 CSS only.

**Spec:** `docs/superpowers/specs/2026-08-12-tables-first-class-design.md`

## Global Constraints

- Python **3.13** everywhere. Do not reintroduce a version matrix.
- **ruff is pinned exactly** at `ruff==0.16.1`. Do not bump it.
- Every commit must leave `ruff check .` and `pytest -q` green, run from `backend/`. CI runs lint before tests, so a lint failure silently skips the whole suite.
- DB tests are marked `@pytest.mark.db` and skip unless `TEST_DATABASE_URL` is set. A skip is not a pass.
- **Commit messages must contain no AI attribution of any kind** — no Co-Authored-By, no session trailers, no tool names.
- **Do not change retrieval ranking.** No query, filter, fusion or ranking rule moves in this plan.
- Chunk size stays **450 tiktoken tokens** (`MAX_TOKENS`).
- Canonicalizer correctness is defined by fixtures in `backend/tests/fixtures/`. Add the fixture before changing extraction behavior.
- Frontend commands run from `frontend/` **in PowerShell** — node is not on the git-bash PATH on this machine.

## File Structure

**Backend — pipeline**
- `backend/migrations/002_sentence_table_id.sql` — **new**, one nullable column.
- `backend/src/pipeline/canonicalize.py` — traversal rewrite; `Sentence` gains `table_id`.
- `backend/src/pipeline/chunk.py` — table-atomic chunking.
- `backend/src/pipeline/store.py` — chunks-delete bugfix; `table_id` in the sentence COPY and SELECT; `delete_derived`, `replace_sentences`.
- `backend/src/pipeline/ingest.py` — `ReprocessStats`, `reprocess_filings`.
- `backend/src/pipeline/__main__.py` — `reprocess` subcommand.

**Backend — API**
- `backend/src/api/normalize.py` — currency/percent spacing rule.

**Backend — evals**
- `backend/evals/repin.py` — **new**, snapshot/propose/apply for gold sids.
- `backend/evals/__main__.py` — `repin` subcommand.

**Frontend**
- `frontend/app/globals.css` — row highlight rule.

---

### Task 1: Fix `store_filing` so it deletes chunks

`store_filing(replace=True)` deletes `sentences` then the `filings` row, but never `chunks`. The foreign key has no cascade, so this raises on any embedded filing — which is every filing in the corpus. Nothing else in this plan can run until it is fixed.

**Files:**
- Modify: `backend/src/pipeline/store.py:31-37`
- Test: `backend/tests/test_store_replace.py`

**Interfaces:**
- Consumes: nothing.
- Produces: no new symbols. `store_filing(..., replace=True)` becomes safe on a filing that has chunks.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_store_replace.py`:

```python
import os
from datetime import date

import psycopg
import pytest

from pipeline import db, store
from pipeline.canonicalize import canonicalize
from pipeline.companies import Company
from pipeline.edgar import FilingRef

COMPANY = Company(999999301, "TSTR", "Replace Test Co")
REF = FilingRef(
    cik=999999301,
    accession="REPLACE-TEST-0001",
    form_type="10-K",
    filing_date=date(2024, 11, 1),
    period_end=date(2024, 9, 28),
    primary_document="x.html",
)
HTML = "<html><body><p>Net sales grew. Services grew too.</p></body></html>"


@pytest.mark.db
def test_replace_succeeds_when_the_filing_already_has_chunks():
    """Every filing in the real corpus is embedded, so this is the only path
    `ingest --force` and `reprocess` ever take. Without the chunks delete it
    raises ForeignKeyViolation on chunks_filing_id_fkey."""
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        db.migrate(conn)
        canonical = canonicalize(HTML, "10-K")
        filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO chunks (filing_id, section, sid_start, sid_end, text,"
                " token_count, embedding) VALUES (%s,%s,%s,%s,%s,%s,%s::vector)",
                (filing_id, "item7", 0, 1, "Net sales grew.", 4, "[" + ",".join(["0"] * 384) + "]"),
            )
        conn.commit()

        new_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM chunks WHERE filing_id = %s", (filing_id,))
            assert cur.fetchone()[0] == 0, "stale chunks survived the replace"
            cur.execute("SELECT count(*) FROM sentences WHERE filing_id = %s", (new_id,))
            assert cur.fetchone()[0] == 2

        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE filing_id = %s", (new_id,))
            cur.execute("DELETE FROM sentences WHERE filing_id = %s", (new_id,))
            cur.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
            cur.execute("DELETE FROM companies WHERE cik = %s", (COMPANY.cik,))
        conn.commit()
```

- [ ] **Step 2: Run it to verify it fails**

Run from `backend/`: `pytest tests/test_store_replace.py -v`
Expected: FAIL — `ForeignKeyViolation: update or delete on table "filings" violates foreign key constraint "chunks_filing_id_fkey" on table "chunks"`.

(If it reports "skipped", `TEST_DATABASE_URL` is unset. Set it and re-run — a skip is not a pass.)

- [ ] **Step 3: Add the chunks delete**

In `backend/src/pipeline/store.py`, inside `store_filing`, replace the `if replace:` block with:

```python
        if replace:
            # chunks first: chunks.filing_id references filings(id) with no
            # ON DELETE CASCADE, so deleting the filing row while chunks
            # survive raises ForeignKeyViolation. Kept explicit rather than
            # adding a cascade -- a cascade would make any future filing
            # delete silently discard embeddings too.
            cur.execute(
                "DELETE FROM chunks WHERE filing_id IN"
                " (SELECT id FROM filings WHERE accession = %s)",
                (ref.accession,),
            )
            cur.execute(
                "DELETE FROM sentences WHERE filing_id IN"
                " (SELECT id FROM filings WHERE accession = %s)",
                (ref.accession,),
            )
            cur.execute("DELETE FROM filings WHERE accession = %s", (ref.accession,))
```

- [ ] **Step 4: Run the test to verify it passes**

Run from `backend/`: `pytest tests/test_store_replace.py -v`
Expected: PASS, 1 test.

- [ ] **Step 5: Run the full suite and lint, then commit**

Run from `backend/`: `pytest -q` then `ruff check .`

```bash
git add backend/src/pipeline/store.py backend/tests/test_store_replace.py
git commit -m "fix: delete a filing's chunks when replacing it

store_filing(replace=True) deleted sentences and the filings row but never
chunks, and the foreign key has no cascade, so it raised
ForeignKeyViolation on any embedded filing -- which is every filing in the
corpus. It passed in tests only because test filings have no chunks."
```

---

### Task 2: `table_id` on sentences

**Files:**
- Create: `backend/migrations/002_sentence_table_id.sql`
- Modify: `backend/src/pipeline/canonicalize.py` (`Sentence`), `backend/src/pipeline/store.py`
- Test: `backend/tests/test_store_table_id.py`

**Interfaces:**
- Consumes: Task 1's `store_filing`.
- Produces: `Sentence` gains `table_id: int | None = None` as its **last** field, so every existing positional construction keeps working. `store.load_sentences` returns it.

- [ ] **Step 1: Write the migration**

Create `backend/migrations/002_sentence_table_id.sql`:

```sql
-- Which table a sentence came from, or NULL for prose. The chunker uses it to
-- keep a table's rows in one chunk so a header row always travels with its
-- data (spec 2026-08-12 §4.3).
ALTER TABLE sentences ADD COLUMN table_id integer;
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_store_table_id.py`:

```python
import os
from datetime import date

import psycopg
import pytest

from pipeline import db, store
from pipeline.canonicalize import CanonicalFiling, Sentence
from pipeline.companies import Company
from pipeline.edgar import FilingRef

COMPANY = Company(999999302, "TSTT", "Table Id Test Co")
REF = FilingRef(
    cik=999999302,
    accession="TABLEID-TEST-0001",
    form_type="10-K",
    filing_date=date(2024, 11, 1),
    period_end=date(2024, 9, 28),
    primary_document="x.html",
)


@pytest.mark.db
def test_table_id_survives_the_database_round_trip():
    """recanonicalize compares freshly computed sentences against stored rows
    for equality, so a field that does not persist would make every filing
    look mismatched."""
    sentences = [
        Sentence(0, "item7", "Net sales grew.", 0, 15, None),
        Sentence(1, "item7", "Americas $ 162,560", 16, 34, 1),
        Sentence(2, "item7", "Europe $ 94,294", 35, 50, 1),
    ]
    canonical = CanonicalFiling("irrelevant", sentences, "<p>x</p>")
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        db.migrate(conn)
        filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
        conn.commit()

        assert store.load_sentences(conn, filing_id) == sentences

        with conn.cursor() as cur:
            cur.execute("DELETE FROM sentences WHERE filing_id = %s", (filing_id,))
            cur.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
            cur.execute("DELETE FROM companies WHERE cik = %s", (COMPANY.cik,))
        conn.commit()
```

- [ ] **Step 3: Run it to verify it fails**

Run from `backend/`: `pytest tests/test_store_table_id.py -v`
Expected: FAIL — `TypeError: Sentence.__init__() takes 6 positional arguments but 7 were given`.

- [ ] **Step 4: Add the field and persist it**

In `backend/src/pipeline/canonicalize.py`, extend `Sentence`:

```python
@dataclass(frozen=True)
class Sentence:
    sid: int
    section: str
    text: str
    char_start: int
    char_end: int
    # NULL for prose. Last field with a default so existing positional
    # constructions keep working.
    table_id: int | None = None
```

In `backend/src/pipeline/store.py`, update the COPY inside `store_filing`:

```python
        with cur.copy(
            "COPY sentences (filing_id, sid, section, text, char_start, char_end,"
            " table_id) FROM STDIN"
        ) as copy:
            for s in canonical.sentences:
                copy.write_row(
                    (filing_id, s.sid, s.section, s.text, s.char_start, s.char_end, s.table_id)
                )
```

and `load_sentences`:

```python
        cur.execute(
            "SELECT sid, section, text, char_start, char_end, table_id"
            " FROM sentences WHERE filing_id = %s ORDER BY sid",
            (filing_id,),
        )
        return [Sentence(*row) for row in cur.fetchall()]
```

- [ ] **Step 5: Apply the migration and run the tests**

Run from `backend/`: `python -m pipeline migrate` then `pytest tests/test_store_table_id.py -v`
Expected: migrate prints `applied: ['002_sentence_table_id.sql']`; test PASSES.

- [ ] **Step 6: Run the full suite and lint, then commit**

Run from `backend/`: `pytest -q` then `ruff check .`

```bash
git add backend/migrations/002_sentence_table_id.sql backend/src/pipeline/canonicalize.py backend/src/pipeline/store.py backend/tests/test_store_table_id.py
git commit -m "feat: record which table a sentence came from

Nullable table_id on sentences, carried through the COPY and the SELECT so
Sentence equality stays meaningful -- recanonicalize compares stored rows
against freshly computed ones and a non-persisted field would make every
filing look mismatched."
```

---

### Task 3: Rows are sentences

The core change. `_leaf_blocks` is replaced by a document-order traversal yielding three unit kinds.

**Files:**
- Create: `backend/tests/fixtures/table_shapes.html`
- Modify: `backend/src/pipeline/canonicalize.py:55-123`
- Test: `backend/tests/test_canonicalize_tables.py`

**Interfaces:**
- Consumes: `Sentence.table_id` from Task 2.
- Produces: `canonicalize()` keeps its signature and return type. Table rows appear as sentences with a non-`None` `table_id`; `<tr>` elements in `viewer_html` carry `data-sid`.

- [ ] **Step 1: Create the fixture**

Create `backend/tests/fixtures/table_shapes.html`. Every shape here was observed in real EDGAR HTML:

```html
<html>
<head><title>FORM 10-K</title></head>
<body>
<p>Item 7. Management's Discussion and Analysis</p>
<p>Net sales discussion follows.</p>

<div><table>
  <tr><td>&nbsp;</td><td>&nbsp;</td></tr>
  <tr><td>2023</td><td>2022</td></tr>
  <tr><td>Americas</td><td>$ 162,560</td></tr>
  <tr><td>Total net sales</td><td>$ 383,285</td></tr>
</table></div>

<table>
  <tr><td>Bare table row</td><td>$ 1,234</td></tr>
</table>

<div><span>Loose text beside a table.</span><table>
  <tr><td>Mixed shape</td><td>$ 99</td></tr>
</table></div>

<div><table>
  <tr><td>Outer label</td><td><table><tr><td>Inner cell</td><td>$ 7</td></tr></table></td></tr>
</table></div>

<p>Closing narrative sentence.</p>
</body>
</html>
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_canonicalize_tables.py`:

```python
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from pipeline.canonicalize import canonicalize

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def result():
    raw = (FIXTURES / "table_shapes.html").read_text(encoding="utf-8")
    return canonicalize(raw, "10-K")


def texts(result):
    return [s.text for s in result.sentences]


def test_each_table_row_is_its_own_sentence(result):
    assert "Americas $ 162,560" in texts(result)
    assert "Total net sales $ 383,285" in texts(result)


def test_a_row_is_never_split_into_several_sentences(result):
    """A row is not prose. pysbd would split it at unpredictable points, and a
    row that segments differently between runs moves sids under stored
    citations."""
    assert not any(t == "Total net sales $" for t in texts(result))
    assert sum(1 for t in texts(result) if "383,285" in t) == 1


def test_bare_and_div_wrapped_tables_behave_identically(result):
    assert "Bare table row $ 1,234" in texts(result)
    assert "Americas $ 162,560" in texts(result)


def test_spacer_rows_produce_no_sentence(result):
    assert all(t.strip() for t in texts(result))
    assert not any(t == "" for t in texts(result))


def test_loose_text_beside_a_table_is_not_lost(result):
    assert "Loose text beside a table." in texts(result)


def test_nested_table_text_is_indexed_once(result):
    assert sum(1 for t in texts(result) if "Inner cell" in t) == 1
    assert not any("Outer label Inner cell" in t for t in texts(result))


def test_table_rows_carry_a_table_id_and_prose_does_not(result):
    by_text = {s.text: s for s in result.sentences}
    assert by_text["Americas $ 162,560"].table_id is not None
    assert by_text["Total net sales $ 383,285"].table_id == by_text["Americas $ 162,560"].table_id
    assert by_text["Bare table row $ 1,234"].table_id != by_text["Americas $ 162,560"].table_id
    assert by_text["Net sales discussion follows."].table_id is None


def test_table_markup_survives_into_viewer_html(result):
    """The div-wrapped case used to be cleared and replaced by a span, so the
    filing rendered as a run-on paragraph of numbers."""
    soup = BeautifulSoup(result.viewer_html, "lxml")
    assert len(soup.find_all("table")) >= 3
    assert soup.find("td") is not None


def test_rows_carry_data_sid_on_the_tr(result):
    soup = BeautifulSoup(result.viewer_html, "lxml")
    rows = [tr for tr in soup.find_all("tr") if tr.has_attr("data-sid")]
    assert rows, "no row carried a data-sid"
    sids = {int(tr["data-sid"]) for tr in rows}
    row_sids = {s.sid for s in result.sentences if s.table_id is not None}
    assert sids == row_sids


def test_every_sid_appears_exactly_once_in_viewer_html(result):
    soup = BeautifulSoup(result.viewer_html, "lxml")
    marked = [int(el["data-sid"]) for el in soup.select("[data-sid]")]
    assert sorted(marked) == [s.sid for s in result.sentences]


def test_sids_are_sequential_and_offsets_align(result):
    assert [s.sid for s in result.sentences] == list(range(len(result.sentences)))
    for s in result.sentences:
        assert result.canonical_text[s.char_start:s.char_end] == s.text
```

- [ ] **Step 3: Run to verify they fail**

Run from `backend/`: `pytest tests/test_canonicalize_tables.py -v`
Expected: FAIL — rows are not extracted, so `test_each_table_row_is_its_own_sentence` fails on the missing text.

- [ ] **Step 4: Rewrite the traversal**

In `backend/src/pipeline/canonicalize.py`, add `from itertools import count` to the imports. Replace `_leaf_blocks` (lines 103-109) with:

```python
def _is_leaf(el) -> bool:
    """A block with no block-level child and no table inside it."""
    return el.find(_BLOCK_TAGS) is None and el.find("table") is None


def _innermost_rows(table):
    """Rows that contain no nested table. An outer row wrapping a nested table
    is a container: emitting it too would index the inner text twice."""
    for tr in table.find_all("tr"):
        if tr.find("table") is None:
            yield tr


def _iter_units(node, table_ids):
    """Yield (kind, payload, table_id) in document order.

    kind is 'block' (a leaf p/li/div, payload is the element), 'run' (a run of
    consecutive inline nodes directly under a container, payload is the list),
    or 'row' (payload is a <tr>). Runs exist so that loose text sitting beside
    a table inside the same div is not lost -- that div is a container, so
    without runs its text would never be visited.
    """
    run: list = []
    for child in node.children:
        name = getattr(child, "name", None)
        if name == "table":
            if run:
                yield ("run", run, None)
                run = []
            table_id = next(table_ids)
            for tr in _innermost_rows(child):
                yield ("row", tr, table_id)
        elif name in _BLOCK_TAGS:
            if run:
                yield ("run", run, None)
                run = []
            if _is_leaf(child):
                yield ("block", child, None)
            else:
                yield from _iter_units(child, table_ids)
        elif name is not None and (
            child.find(_BLOCK_TAGS) is not None or child.find("table") is not None
        ):
            # A non-block wrapper (center, font, section...) holding structure.
            # Descend so blocks and tables anywhere in the tree are still found.
            if run:
                yield ("run", run, None)
                run = []
            yield from _iter_units(child, table_ids)
        else:
            run.append(child)
    if run:
        yield ("run", run, None)


def _unit_text(nodes) -> str:
    parts = []
    for node in nodes:
        if getattr(node, "name", None) is None:
            parts.append(str(node))
        else:
            parts.append(node.get_text(" ", strip=True))
    return " ".join(" ".join(parts).split())
```

Replace the extraction loop in `canonicalize()` (lines 78-95) with:

```python
    body = soup.body if soup.body is not None else soup
    for kind, payload, table_id in list(_iter_units(body, count(1))):
        if kind == "row":
            text = " ".join(payload.get_text(" ", strip=True).split())
            if not text:
                continue  # spacer row: EDGAR uses these purely for layout
            sid = len(sentences)
            # A row is not prose, so it is never segmented, and it does not
            # feed the section tracker -- a row like "Item 7 12,345" would
            # otherwise be read as a heading.
            sentences.append(
                Sentence(sid, tracker.current, text, cursor, cursor + len(text), table_id)
            )
            cursor += len(text) + 1
            payload["data-sid"] = str(sid)
            continue

        nodes = [payload] if kind == "block" else payload
        text = _unit_text(nodes)
        if not text:
            continue
        section = tracker.update(text)
        block_sentences: list[Sentence] = []
        for span in segmenter.segment(text):
            sent_text = span.sent.strip()
            if not sent_text:
                continue
            start = cursor
            end = start + len(sent_text)
            sid = len(sentences) + len(block_sentences)
            block_sentences.append(Sentence(sid, section, sent_text, start, end))
            cursor = end + 1
        if block_sentences:
            if kind == "block":
                _rewrite_block(soup, payload, block_sentences)
            else:
                _rewrite_run(soup, payload, block_sentences)
            sentences.extend(block_sentences)
```

Note `list(...)` around `_iter_units`: the loop mutates the tree, and iterating a generator over a tree being rewritten underneath it is not safe.

Add the run rewriter beside `_rewrite_block`:

```python
def _rewrite_run(soup, run: list, run_sentences: list[Sentence]) -> None:
    """Replace a run of inline nodes with sid-tagged spans, in place.

    Spans are inserted at the run's original position and the original nodes
    removed, so document order -- and therefore sid order -- is preserved
    around any sibling table.
    """
    anchor = run[0]
    for i, s in enumerate(run_sentences):
        span = soup.new_tag("span")
        span["data-sid"] = str(s.sid)
        span.string = s.text
        anchor.insert_before(span)
        if i < len(run_sentences) - 1:
            anchor.insert_before(" ")
    for node in run:
        node.extract()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run from `backend/`: `pytest tests/test_canonicalize_tables.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 6: Confirm the existing canonicalizer fixtures still pass**

Run from `backend/`: `pytest tests/test_canonicalize.py tests/test_canonicalize_styles.py -v`
Expected: PASS. `mini_10k.html` and `styled_filing.html` both contain tables, so if sentence counts there shift, read the diff before adjusting anything — a change in *prose* extraction is a bug in this task, while a change in *table* extraction is the intended behavior.

- [ ] **Step 7: Run the full suite and lint, then commit**

Run from `backend/`: `pytest -q` then `ruff check .`

```bash
git add backend/src/pipeline/canonicalize.py backend/tests/test_canonicalize_tables.py backend/tests/fixtures/table_shapes.html
git commit -m "feat: index table rows as sentences and keep table markup

A div wrapping a table used to qualify as a leaf block, so the table was
flattened into one sentence and its markup cleared, while a bare table was
skipped entirely -- whether a table was indexed depended on incidental HTML
shape. The traversal now yields leaf blocks, runs of loose inline content,
and table rows in document order. Rows carry data-sid on the tr itself
because a span cannot wrap cells."
```

---

### Task 4: A table's rows chunk together

**Files:**
- Modify: `backend/src/pipeline/chunk.py:33-66`
- Test: `backend/tests/test_chunk_tables.py`

**Interfaces:**
- Consumes: `Sentence.table_id` from Task 2.
- Produces: `chunk_sentences` keeps its signature. A table's rows always land in one chunk.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_chunk_tables.py`:

```python
from pipeline.canonicalize import Sentence
from pipeline.chunk import MAX_TOKENS, chunk_sentences


def row(sid: int, table_id: int, text: str) -> Sentence:
    return Sentence(sid, "item7", text, 0, len(text), table_id)


def prose(sid: int, text: str) -> Sentence:
    return Sentence(sid, "item7", text, 0, len(text), None)


def test_a_table_stays_in_one_chunk_even_past_the_token_budget():
    """Splitting a table separates the header row from its numbers, leaving
    the model with 'Europe 94,294 (1) %' and no column meaning."""
    rows = [row(i, 1, f"Region {i} $ {i},000 {i} % $ {i},500 " + "filler " * 40) for i in range(40)]
    chunks = chunk_sentences(rows)
    assert len(chunks) == 1
    assert chunks[0].token_count > MAX_TOKENS
    assert chunks[0].sid_start == 0
    assert chunks[0].sid_end == 39


def test_two_tables_do_not_merge_into_one_chunk():
    sentences = [row(0, 1, "Americas $ 1"), row(1, 1, "Europe $ 2"), row(2, 2, "Other table $ 3")]
    chunks = chunk_sentences(sentences, max_tokens=6)
    assert [(c.sid_start, c.sid_end) for c in chunks] == [(0, 1), (2, 2)]


def test_chunks_stay_contiguous_and_disjoint():
    """Spec invariant 6. Table atomicity must not introduce overlap: an
    overlapping sid would belong to two chunks, and the header-repetition
    design was rejected precisely to avoid that."""
    sentences = (
        [prose(0, "Intro sentence.")]
        + [row(i, 1, f"Region {i} $ {i},000") for i in range(1, 30)]
        + [prose(30, "Closing sentence.")]
    )
    chunks = chunk_sentences(sentences, max_tokens=20)
    covered = [sid for c in chunks for sid in range(c.sid_start, c.sid_end + 1)]
    assert covered == list(range(31)), "sids must be covered once, in order"


def test_prose_still_splits_on_the_token_budget():
    sentences = [prose(i, "Sentence number %d here." % i) for i in range(20)]
    chunks = chunk_sentences(sentences, max_tokens=20)
    assert len(chunks) > 1


def test_chunk_text_is_the_space_join_of_its_sentences():
    """verify.sentence_spans reconstructs chunk text this way to map a quote
    back to sids. If the two ever disagree, citations resolve to wrong sids."""
    sentences = [prose(0, "First one."), row(1, 1, "Americas $ 1"), row(2, 1, "Europe $ 2")]
    chunks = chunk_sentences(sentences)
    assert chunks[0].text == "First one. Americas $ 1 Europe $ 2"
```

- [ ] **Step 2: Run to verify it fails**

Run from `backend/`: `pytest tests/test_chunk_tables.py -v`
Expected: FAIL — `test_a_table_stays_in_one_chunk_even_past_the_token_budget` gets several chunks.

- [ ] **Step 3: Make tables atomic**

In `backend/src/pipeline/chunk.py`, replace the loop body in `chunk_sentences`:

```python
    for sentence in sentences:
        n = count_tokens(sentence.text)
        new_section = current and sentence.section != current[0].section
        # Rows of the table already in progress never trigger a flush: a table
        # is chunked whole so a header row always travels with its data, the
        # same escape hatch an over-long single sentence already gets.
        continues_table = (
            bool(current)
            and sentence.table_id is not None
            and sentence.table_id == current[-1].table_id
        )
        over_budget = current and current_tokens + n > max_tokens and not continues_table
        if new_section or over_budget:
            flush()
        current.append(sentence)
        current_tokens += n
    flush()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run from `backend/`: `pytest tests/test_chunk_tables.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Run the full suite and lint, then commit**

Run from `backend/`: `pytest -q` then `ruff check .`

```bash
git add backend/src/pipeline/chunk.py backend/tests/test_chunk_tables.py
git commit -m "feat: chunk a table's rows as one atomic unit

A header row must travel with its data, so a table is never split on the
token budget. Repeating the header into continuation chunks was rejected:
verify.sentence_spans rebuilds chunk text from exactly the chunk's sid
range, so injected text would resolve citations to the wrong sids."
```

---

### Task 5: Currency spacing in `normalize()`

**Files:**
- Modify: `backend/src/api/normalize.py`
- Test: `backend/tests/test_normalize.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `normalize()` keeps its signature `(str) -> tuple[str, list[int]]`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_normalize.py`:

```python
def test_currency_spacing_is_ignored_when_matching():
    """Filing tables read '$ 383,285'; a model naturally writes '$383,285'.
    Verification punished formatting rather than unfaithfulness."""
    from api.normalize import normalize

    assert normalize("$ 383,285")[0] == normalize("$383,285")[0]
    assert normalize("(3) %")[0] == normalize("(3)%")[0]


def test_word_spacing_is_still_significant():
    """The narrow rule must not become 'ignore all whitespace' -- that would
    let a quote match across word boundaries that never existed."""
    from api.normalize import normalize

    assert normalize("net sales")[0] != normalize("netsales")[0]
    assert normalize("net sales")[0] == "net sales"


def test_offset_map_survives_a_dropped_space():
    from api.normalize import normalize

    text = "Total net sales $ 383,285 fell"
    normalized, offsets = normalize(text)
    index = normalized.find("$383,285")
    assert index != -1
    assert len(offsets) == len(normalized)
    assert text[offsets[index]] == "$"
    end = offsets[index + len("$383,285") - 1] + 1
    assert text[offsets[index]:end] == "$ 383,285"


def test_a_quote_written_without_the_space_now_verifies():
    from api.verify import find_quote

    source = "Rest of Asia Pacific 29,615 1 % Total net sales $ 383,285 (3) % $ 394,328"
    assert find_quote(source, "Total net sales $383,285") is not None
    assert find_quote(source, "Total net sales $ 383,285") is not None
    assert find_quote(source, "Total net sales $ 999,999") is None
```

- [ ] **Step 2: Run to verify they fail**

Run from `backend/`: `pytest tests/test_normalize.py -v`
Expected: FAIL — `normalize("$ 383,285")[0]` is `"$ 383,285"`, not `"$383,285"`.

- [ ] **Step 3: Add the rule**

In `backend/src/api/normalize.py`, add beside `_REPLACEMENTS`:

```python
# Financial tables read "$ 383,285" and "(3) %"; a model quoting them writes
# "$383,285" and "(3)%". Dropping the space on exactly these boundaries makes
# the two forms match. Deliberately narrow -- a general "ignore whitespace"
# rule would let a quote match across word boundaries that never existed in
# the source, and verification's whole value is that it is strict.
_NO_SPACE_AFTER = "$€£#("
_NO_SPACE_BEFORE = ")%"
```

and, inside `normalize`, replace the body of the `else` branch that handles non-space characters so the emitted-space decision happens first:

```python
        in_space_run = False
        if out and out[-1] == " " and (
            (len(out) >= 2 and out[-2] in _NO_SPACE_AFTER) or char in _NO_SPACE_BEFORE
        ):
            # Retract the space we already emitted, dropping its offset with
            # it so the map stays one entry per emitted character.
            out.pop()
            offsets.pop()
        piece = unicodedata.normalize("NFKC", _REPLACEMENTS.get(char, char)).casefold()
        for produced in piece:
            out.append(produced)
            offsets.append(index)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run from `backend/`: `pytest tests/test_normalize.py tests/test_verify.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite and lint, then commit**

Run from `backend/`: `pytest -q` then `ruff check .`

```bash
git add backend/src/api/normalize.py backend/tests/test_normalize.py
git commit -m "fix: ignore currency and percent spacing when matching quotes

Tables read '\$ 383,285' and a model writes '\$383,285', so a faithful quote
of a real figure failed verification on a space. The rule is character
specific; a negative test pins that ordinary word spacing still matters."
```

---

### Task 6: `reprocess`

**Files:**
- Modify: `backend/src/pipeline/store.py`, `backend/src/pipeline/ingest.py`, `backend/src/pipeline/__main__.py`
- Test: `backend/tests/test_reprocess.py`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces:
  - `store.delete_derived(conn, filing_id: int) -> None`
  - `store.replace_sentences(conn, filing_id: int, sentences: list[Sentence]) -> None`
  - `ingest.ReprocessStats(reprocessed: int, missing: int, moved: list[tuple[str, int, int]])`
  - `ingest.reprocess_filings(conn, embedder, *, cache_dir: Path, ticker: str | None = None, dry_run: bool = False) -> ReprocessStats`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_reprocess.py`:

```python
import os
from datetime import date
from pathlib import Path

import psycopg
import pytest

from pipeline import db, ingest, store
from pipeline.canonicalize import canonicalize
from pipeline.chunk import chunk_sentences
from pipeline.companies import Company
from pipeline.edgar import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
COMPANY = Company(999999303, "TSTP", "Reprocess Test Co")
REF = FilingRef(
    cik=999999303,
    accession="REPROCESS-TEST-0001",
    form_type="10-K",
    filing_date=date(2024, 11, 1),
    period_end=date(2024, 9, 28),
    primary_document="table_shapes.html",
)


class FakeEmbedder:
    def embed_texts(self, texts):
        return [[0.0] * 384 for _ in texts]


def _seed(conn, cache_dir: Path) -> int:
    raw = (FIXTURES / "table_shapes.html").read_text(encoding="utf-8")
    cached = cache_dir / str(REF.cik) / f"{REF.accession}.html"
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(raw, encoding="utf-8")
    canonical = canonicalize(raw, REF.form_type)
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    chunks = chunk_sentences(canonical.sentences)
    store.store_chunks(conn, filing_id, chunks, [[0.0] * 384 for _ in chunks])
    return filing_id


def _cleanup(conn):
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM chunks WHERE filing_id IN"
            " (SELECT id FROM filings WHERE accession = %s)", (REF.accession,)
        )
        cur.execute(
            "DELETE FROM sentences WHERE filing_id IN"
            " (SELECT id FROM filings WHERE accession = %s)", (REF.accession,)
        )
        cur.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
        cur.execute("DELETE FROM companies WHERE cik = %s", (COMPANY.cik,))
    conn.commit()


@pytest.mark.db
def test_reprocess_rebuilds_sentences_chunks_and_embeddings(tmp_path):
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        db.migrate(conn)
        filing_id = _seed(conn, tmp_path)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sentences WHERE filing_id = %s AND sid > 1", (filing_id,))
        conn.commit()

        stats = ingest.reprocess_filings(
            conn, FakeEmbedder(), cache_dir=tmp_path, ticker="TSTP"
        )
        conn.commit()

        assert stats.reprocessed == 1
        assert stats.missing == 0
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM sentences WHERE filing_id = %s", (filing_id,))
            assert cur.fetchone()[0] > 2, "sentences were not rebuilt"
            cur.execute("SELECT count(*) FROM chunks WHERE filing_id = %s", (filing_id,))
            assert cur.fetchone()[0] >= 1
        _cleanup(conn)


@pytest.mark.db
def test_reprocess_dry_run_writes_nothing(tmp_path):
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        db.migrate(conn)
        filing_id = _seed(conn, tmp_path)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sentences WHERE filing_id = %s AND sid > 1", (filing_id,))
            cur.execute("SELECT count(*) FROM sentences WHERE filing_id = %s", (filing_id,))
            before = cur.fetchone()[0]
        conn.commit()

        stats = ingest.reprocess_filings(
            conn, FakeEmbedder(), cache_dir=tmp_path, ticker="TSTP", dry_run=True
        )
        conn.commit()

        assert stats.reprocessed == 0
        assert stats.moved and stats.moved[0][0] == REF.accession
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM sentences WHERE filing_id = %s", (filing_id,))
            assert cur.fetchone()[0] == before
        _cleanup(conn)


@pytest.mark.db
def test_reprocess_skips_a_filing_missing_from_the_cache(tmp_path):
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        db.migrate(conn)
        _seed(conn, tmp_path)
        conn.commit()
        (tmp_path / str(REF.cik) / f"{REF.accession}.html").unlink()

        stats = ingest.reprocess_filings(
            conn, FakeEmbedder(), cache_dir=tmp_path, ticker="TSTP"
        )
        conn.commit()
        assert stats.reprocessed == 0
        assert stats.missing == 1
        _cleanup(conn)
```

- [ ] **Step 2: Run to verify it fails**

Run from `backend/`: `pytest tests/test_reprocess.py -v`
Expected: FAIL — `AttributeError: module 'pipeline.ingest' has no attribute 'reprocess_filings'`.

- [ ] **Step 3: Add the store helpers**

Append to `backend/src/pipeline/store.py`:

```python
def delete_derived(conn: psycopg.Connection, filing_id: int) -> None:
    """Drop a filing's chunks and sentences, keeping the filings row itself."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE filing_id = %s", (filing_id,))
        cur.execute("DELETE FROM sentences WHERE filing_id = %s", (filing_id,))


def replace_sentences(
    conn: psycopg.Connection, filing_id: int, sentences: list[Sentence]
) -> None:
    with conn.cursor() as cur:
        with cur.copy(
            "COPY sentences (filing_id, sid, section, text, char_start, char_end,"
            " table_id) FROM STDIN"
        ) as copy:
            for s in sentences:
                copy.write_row(
                    (filing_id, s.sid, s.section, s.text, s.char_start, s.char_end, s.table_id)
                )
```

- [ ] **Step 4: Add `reprocess_filings`**

Append to `backend/src/pipeline/ingest.py`:

```python
@dataclass
class ReprocessStats:
    reprocessed: int = 0
    missing: int = 0
    # (accession, sentences before, sentences after)
    moved: list[tuple[str, int, int]] = field(default_factory=list)


def reprocess_filings(
    conn,
    embedder,
    *,
    cache_dir: Path,
    ticker: str | None = None,
    dry_run: bool = False,
) -> ReprocessStats:
    """Rebuild sentences, chunks and embeddings from cached raw HTML.

    The deliberate opposite of recanonicalize_filings, which refuses to write
    when sentences move. This one expects them to move -- it is how a change
    to extraction reaches an already-ingested corpus. Every stored citation
    and every pinned gold sid is invalidated by design, so run
    `python -m evals repin` afterwards.

    One transaction per filing: a crash between deleting the old sentences and
    writing the new chunks would leave chunks pointing at sid ranges that no
    longer exist.
    """
    stats = ReprocessStats()
    for filing_id, cik, accession, form_type in store.filings_to_recanonicalize(
        conn, ticker=ticker
    ):
        path = Path(cache_dir) / str(cik) / f"{accession}.html"
        if not path.exists():
            stats.missing += 1
            continue
        canonical = canonicalize(path.read_text(encoding="utf-8"), form_type)
        before = len(store.load_sentences(conn, filing_id))
        after = len(canonical.sentences)
        if before != after:
            stats.moved.append((accession, before, after))
        if dry_run:
            continue
        with conn.transaction():
            store.delete_derived(conn, filing_id)
            store.replace_sentences(conn, filing_id, canonical.sentences)
            store.update_viewer_html(conn, filing_id, canonical.viewer_html)
            chunks = chunk_sentences(canonical.sentences)
            vectors = embedder.embed_texts([c.text for c in chunks])
            store.store_chunks(conn, filing_id, chunks, vectors)
        stats.reprocessed += 1
    return stats
```

- [ ] **Step 5: Run the tests to verify they pass**

Run from `backend/`: `pytest tests/test_reprocess.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 6: Add the CLI subcommand**

In `backend/src/pipeline/__main__.py`, register the parser next to `p_recanon`:

```python
    p_reprocess = sub.add_parser(
        "reprocess",
        help="rebuild sentences, chunks and embeddings from cached raw HTML"
        " (no EDGAR traffic; invalidates stored sids -- run `evals repin` after)",
    )
    p_reprocess.add_argument("--ticker", help="restrict to one curated ticker")
    p_reprocess.add_argument(
        "--dry-run", action="store_true", help="report sid movement, write nothing"
    )
```

and handle it beside the `recanonicalize` branch:

```python
    if args.cmd == "reprocess":
        from .embed import Embedder

        with db.connect() as conn:
            stats = ingest.reprocess_filings(
                conn,
                Embedder(),
                cache_dir=Path("data/raw"),
                ticker=args.ticker,
                dry_run=args.dry_run,
            )
        verb = "would reprocess" if args.dry_run else "reprocessed"
        print(f"{verb} {stats.reprocessed} filings, {stats.missing} missing from cache")
        for accession, before, after in stats.moved:
            print(f"  {accession}: {before} -> {after} sentences")
        return
```

Note the `dry_run` branch reports movement without incrementing `reprocessed`, so the dry-run line reads `would reprocess 0 filings` — the movement list is the useful output.

- [ ] **Step 7: Run the full suite and lint, then commit**

Run from `backend/`: `pytest -q` then `ruff check .`

```bash
git add backend/src/pipeline/store.py backend/src/pipeline/ingest.py backend/src/pipeline/__main__.py backend/tests/test_reprocess.py
git commit -m "feat: add reprocess to rebuild sentences, chunks and embeddings

The deliberate opposite of recanonicalize, which refuses to write when
sentences move; this one expects them to. One transaction per filing, so a
crash cannot leave chunks pointing at sid ranges that no longer exist. Reads
cached raw HTML only -- no EDGAR traffic."
```

---

### Task 7: `evals repin`

Gold sids move, and the old sentence text needed to map them is destroyed by `reprocess`. So repin is three phases: **snapshot before**, propose after, apply on confirmation.

**Files:**
- Create: `backend/evals/repin.py`
- Modify: `backend/evals/__main__.py`
- Test: `backend/tests/test_repin.py`

**Interfaces:**
- Consumes: `harness.load_golden`, `store.load_sentences`.
- Produces:
  - `repin.snapshot(conn, questions) -> dict` — `{question_id: {"accession": str, "sids": [int], "texts": [str]}}`
  - `repin.propose(conn, questions, snap: dict) -> list[Proposal]`
  - `repin.Proposal(question_id: str, old_sids: list[int], new_sids: list[int], resolved: bool, note: str)`
  - `repin.apply_to_golden(path: Path, proposals: list[Proposal]) -> int`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_repin.py`:

```python
from pathlib import Path

from evals import repin
from evals.harness import GoldenQuestion
from pipeline.canonicalize import Sentence


def question(gold_sids):
    return GoldenQuestion("q001", "Q?", "AAPL", "ACC-1", "item7", gold_sids)


def test_proposes_the_row_that_contains_the_old_text():
    """The old gold sentence was a whole flattened table; the new one is a
    single row of it, so the mapping is old-contains-new, not equality."""
    snap = {"q001": {"accession": "ACC-1", "sids": [12], "texts": ["Americas $ 1 Total net sales $ 383,285"]}}
    new_sentences = [
        Sentence(40, "item7", "Americas $ 1", 0, 12, 3),
        Sentence(41, "item7", "Total net sales $ 383,285", 13, 38, 3),
    ]
    proposals = repin.propose_from_sentences([question([12])], snap, {"ACC-1": new_sentences})
    assert len(proposals) == 1
    assert proposals[0].resolved is True
    assert set(proposals[0].new_sids) == {40, 41}


def test_exact_text_match_maps_one_to_one():
    snap = {"q001": {"accession": "ACC-1", "sids": [12], "texts": ["Net sales grew."]}}
    new_sentences = [
        Sentence(7, "item7", "Something else.", 0, 15, None),
        Sentence(8, "item7", "Net sales grew.", 16, 31, None),
    ]
    proposals = repin.propose_from_sentences([question([12])], snap, {"ACC-1": new_sentences})
    assert proposals[0].new_sids == [8]
    assert proposals[0].resolved is True


def test_unmappable_entry_is_reported_not_guessed():
    snap = {"q001": {"accession": "ACC-1", "sids": [12], "texts": ["Vanished text."]}}
    new_sentences = [Sentence(0, "item7", "Nothing alike.", 0, 14, None)]
    proposals = repin.propose_from_sentences([question([12])], snap, {"ACC-1": new_sentences})
    assert proposals[0].resolved is False
    assert proposals[0].new_sids == []


def test_apply_rewrites_only_resolved_entries(tmp_path: Path):
    golden = tmp_path / "golden.yaml"
    golden.write_text(
        '- id: q001\n  question: "Q?"\n  ticker: AAPL\n  accession: "ACC-1"\n'
        '  section: "item7"\n  gold_sids: [12]\n'
        '- id: q002\n  question: "Q2?"\n  ticker: AAPL\n  accession: "ACC-1"\n'
        '  section: "item7"\n  gold_sids: [99]\n',
        encoding="utf-8",
    )
    proposals = [
        repin.Proposal("q001", [12], [40, 41], True, "contained"),
        repin.Proposal("q002", [99], [], False, "no match"),
    ]
    changed = repin.apply_to_golden(golden, proposals)
    assert changed == 1
    text = golden.read_text(encoding="utf-8")
    assert "[40, 41]" in text
    assert "[99]" in text
```

- [ ] **Step 2: Run to verify it fails**

Run from `backend/`: `pytest tests/test_repin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.repin'`.

- [ ] **Step 3: Implement repin**

Create `backend/evals/repin.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from pipeline import store
from pipeline.canonicalize import Sentence

from .harness import GoldenQuestion

SNAPSHOT_PATH = Path(__file__).parent / "repin_snapshot.json"


@dataclass(frozen=True)
class Proposal:
    question_id: str
    old_sids: list[int]
    new_sids: list[int]
    resolved: bool
    note: str


def snapshot(conn, questions: list[GoldenQuestion]) -> dict:
    """Capture each gold sid's *text* before reprocess destroys it.

    Sids are the thing being invalidated, so text is the only stable handle
    for re-anchoring afterwards.
    """
    snap: dict = {}
    for question in questions:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM filings WHERE accession = %s", (question.accession,)
            )
            row = cur.fetchone()
        if row is None:
            continue
        by_sid = {s.sid: s.text for s in store.load_sentences(conn, row[0])}
        snap[question.id] = {
            "accession": question.accession,
            "sids": list(question.gold_sids),
            "texts": [by_sid[sid] for sid in question.gold_sids if sid in by_sid],
        }
    return snap


def propose_from_sentences(
    questions: list[GoldenQuestion],
    snap: dict,
    sentences_by_accession: dict[str, list[Sentence]],
) -> list[Proposal]:
    """Map old gold sids to new ones by sentence text.

    Two cases matter. An unchanged prose sentence matches exactly. A gold sid
    that pointed at a flattened table now corresponds to several rows, so the
    old text *contains* each new row's text -- that is the containment branch.
    Anything else is reported unresolved rather than guessed: a wrong pin
    silently invalidates every measurement taken afterwards.
    """
    proposals: list[Proposal] = []
    for question in questions:
        entry = snap.get(question.id)
        if entry is None:
            proposals.append(
                Proposal(question.id, list(question.gold_sids), [], False, "no snapshot entry")
            )
            continue
        sentences = sentences_by_accession.get(entry["accession"], [])
        new_sids: list[int] = []
        notes: list[str] = []
        for text in entry["texts"]:
            exact = [s.sid for s in sentences if s.text == text]
            if exact:
                new_sids.extend(exact)
                notes.append("exact")
                continue
            contained = [s.sid for s in sentences if s.text and s.text in text]
            if contained:
                new_sids.extend(contained)
                notes.append(f"contained ({len(contained)} rows)")
                continue
            notes.append("no match")
        resolved = bool(new_sids) and "no match" not in notes
        proposals.append(
            Proposal(
                question.id,
                list(question.gold_sids),
                sorted(set(new_sids)),
                resolved,
                "; ".join(notes),
            )
        )
    return proposals


def propose(conn, questions: list[GoldenQuestion], snap: dict) -> list[Proposal]:
    accessions = {entry["accession"] for entry in snap.values()}
    sentences_by_accession: dict[str, list[Sentence]] = {}
    for accession in accessions:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM filings WHERE accession = %s", (accession,))
            row = cur.fetchone()
        if row is not None:
            sentences_by_accession[accession] = store.load_sentences(conn, row[0])
    return propose_from_sentences(questions, snap, sentences_by_accession)


def apply_to_golden(path: Path, proposals: list[Proposal]) -> int:
    """Rewrite gold_sids for resolved proposals only. Returns how many changed."""
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    by_id = {p.question_id: p for p in proposals if p.resolved}
    changed = 0
    for entry in entries:
        proposal = by_id.get(entry.get("id"))
        if proposal is None or entry.get("gold_sids") == proposal.new_sids:
            continue
        entry["gold_sids"] = proposal.new_sids
        changed += 1
    Path(path).write_text(
        yaml.safe_dump(entries, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return changed


def write_snapshot(snap: dict, path: Path = SNAPSHOT_PATH) -> None:
    Path(path).write_text(json.dumps(snap, indent=2), encoding="utf-8")


def read_snapshot(path: Path = SNAPSHOT_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run from `backend/`: `pytest tests/test_repin.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Add the CLI subcommand**

In `backend/evals/__main__.py`, register beside the others:

```python
    p_repin = sub.add_parser(
        "repin", help="re-anchor golden gold_sids after a reprocess"
    )
    p_repin.add_argument(
        "--snapshot", action="store_true",
        help="capture current gold sentence text; run this BEFORE reprocess",
    )
    p_repin.add_argument(
        "--apply", action="store_true", help="write the proposals into golden.yaml"
    )
```

and add the handler:

```python
def cmd_repin(args) -> None:
    from . import repin

    questions = harness.load_golden()
    with db.connect() as conn:
        if args.snapshot:
            repin.write_snapshot(repin.snapshot(conn, questions))
            print(f"snapshot written to {repin.SNAPSHOT_PATH}")
            return
        proposals = repin.propose(conn, questions, repin.read_snapshot())

    for proposal in proposals:
        flag = "ok " if proposal.resolved else "MANUAL"
        print(f"{flag} {proposal.question_id}: {proposal.old_sids} -> {proposal.new_sids}"
              f"  [{proposal.note}]")
    unresolved = [p.question_id for p in proposals if not p.resolved]

    if not args.apply:
        print("\nproposal only. Re-run with --apply to write golden.yaml.")
        return
    changed = repin.apply_to_golden(harness.GOLDEN_PATH, proposals)
    print(f"\nrewrote {changed} entries")
    if unresolved:
        print(f"LEFT UNCHANGED, fix by hand: {', '.join(unresolved)}")
```

Wire it into `main`: add `"repin"` to the dispatch, e.g. replace the trailing `if/else` with

```python
    if args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "repin":
        cmd_repin(args)
    else:
        cmd_verify(args)
```

Proposing is the default and `--apply` is opt-in, the reverse of `reprocess`'s `--dry-run`. Reprocessing wrong costs a re-run; re-pinning wrong silently invalidates every later measurement.

- [ ] **Step 6: Run the full suite and lint, then commit**

Run from `backend/`: `pytest -q` then `ruff check .`

```bash
git add backend/evals/repin.py backend/evals/__main__.py backend/tests/test_repin.py
git commit -m "feat: add evals repin to re-anchor gold sids after a reprocess

Gold sids are pinned to sentence positions that reprocess redefines, and the
old text needed to map them is destroyed by the same command -- so repin
snapshots that text first, then proposes old to new by exact match or, for a
gold sid that pointed at a flattened table, by containment against its rows.
Proposing is the default and applying is opt-in: a wrong pin silently
invalidates every measurement taken afterwards."
```

---

### Task 8: Run it against the real corpus

Operational, not code. Order matters: the snapshot must be taken **before** reprocess.

**Files:**
- Modify: `backend/evals/golden.yaml` (via `repin --apply`), `backend/evals/results.jsonl`

- [ ] **Step 1: Confirm a clean tree and take the baseline eval**

Run from `backend/`: `git status --porcelain` (expect empty), then `python -m evals run`
Record `verified_rate`, `answered_rate`, `citations_total`, `gold_sid_hit_rate`.

- [ ] **Step 2: Snapshot the golden set BEFORE touching the corpus**

Run from `backend/`: `python -m evals repin --snapshot`
Expected: `snapshot written to .../evals/repin_snapshot.json`.

If this is skipped, the gold sentence text is gone and the golden set has to be re-pinned by hand from scratch.

- [ ] **Step 3: Dry-run the reprocess**

Run from `backend/`: `python -m pipeline reprocess --dry-run`
Expected: `would reprocess 0 filings, 0 missing from cache` followed by ~120 movement lines. Every filing should report a sentence-count change, because tables now contribute rows. A filing reporting **no** change either has no tables or was not re-extracted — inspect one before continuing.

- [ ] **Step 4: Reprocess one company first**

Run from `backend/`: `python -m pipeline reprocess --ticker AAPL`
Expected: `reprocessed 13 filings, 0 missing from cache`.

- [ ] **Step 5: Spot-check that a table now renders and highlights**

Run from `backend/`:

```
python -c "from pipeline.env import load_env; load_env(); from pipeline import db; c=db.connect(); cur=c.execute(\"select count(*) from sentences where table_id is not null\"); print('table-row sentences:', cur.fetchone()[0])"
```

Expected: a non-zero count. Then confirm rows are marked in the viewer:

```
python -c "from pipeline.env import load_env; load_env(); from pipeline import db; c=db.connect(); cur=c.execute(\"select count(*) from filings where viewer_html like '%<tr data-sid=%'\"); print('filings with marked rows:', cur.fetchone()[0])"
```

- [ ] **Step 6: Reprocess the rest of the corpus**

Run from `backend/`: `python -m pipeline reprocess`
Expected: `reprocessed 120 filings, 0 missing from cache`.

- [ ] **Step 7: Re-pin the golden set**

Run from `backend/`: `python -m evals repin`
Read every line. Then `python -m evals repin --apply`, then `python -m evals verify`.
Expected: `all 16 entries verified`. Any entry printed as `MANUAL` must be re-pinned by hand — open the filing, find the sentence that actually answers the question, and set `gold_sids` to it.

- [ ] **Step 8: Post-change eval and commit**

Run from `backend/`: `git status --porcelain` — commit `golden.yaml` first so the tree is clean:

```bash
git add backend/evals/golden.yaml
git commit -m "chore: re-anchor gold sids onto table rows"
```

Then `python -m evals run`, and commit the results:

```bash
git add backend/evals/results.jsonl
git commit -m "chore: record faithfulness across the table reprocess

gold_sid_hit_rate is NOT comparable across these two rows: this change
redefines the sids it scores against, and the golden set was re-pinned
between them. verified_rate and answered_rate are the comparable numbers."
```

---

### Task 9: Highlight a cited row

**Files:**
- Modify: `frontend/app/globals.css`

- [ ] **Step 1: Add the row rule**

`.cited-sentence` sets `box-shadow`, which paints outside a table row's box and bleeds across cells. Append to `frontend/app/globals.css`:

```css
/* A cited table row carries data-sid on the <tr> itself, because a <span>
   cannot wrap <td> elements. box-shadow bleeds across cell borders on a row,
   so rows get the background treatment only. */
tr.cited-sentence {
  box-shadow: none;
}

tr.cited-sentence td,
tr.cited-sentence th {
  background-color: #78350f;
  color: #fef3c7;
}
```

- [ ] **Step 2: Verify the existing suites still pass**

Run from `frontend/` in PowerShell: `npm test`, `npm run lint`, `npm run test:e2e`
Expected: 42 vitest specs, eslint clean, 3 Playwright specs.

- [ ] **Step 3: Look at it**

Start the API (`python -m uvicorn api.app:app --port 8000` from `backend/`) and the frontend (`npm run dev` from `frontend/`), ask a numeric question such as "What were Apple's total net sales in fiscal 2023?", and click the citation. Confirm the highlight lands on **one row**, not the whole table, and that the table renders as a table.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/globals.css
git commit -m "feat: highlight a cited table row without bleeding across cells"
```

---

### Task 10: Documentation

**Files:**
- Modify: `docs/design.md`, `CLAUDE.md` (gitignored — update locally, do not stage)

- [ ] **Step 1: Rewrite the §4.2 tables bullet**

In `docs/design.md` §4.2, replace the `**Tables:**` bullet with:

```markdown
- **Tables:** indexed, one sentence per table row, and preserved in viewer HTML.
  A row is never sentence-segmented (it is not prose) and carries `data-sid` on
  the `<tr>` itself, since a `<span>` cannot wrap `<td>` elements. A table's
  rows are chunked as one atomic unit even past the token budget, so a header
  row always travels with its data. Numeric questions are answered from these
  rows as well as from narrative text. Column-aware parsing of a row into a
  record remains a v2 item — a row is a flat string.
```

- [ ] **Step 2: Note the reprocess command in §4.2**

Directly after that bullet, add:

```markdown
- **Reprocessing:** `python -m pipeline recanonicalize` rebuilds `viewer_html`
  only and refuses to write when sentences move. `python -m pipeline reprocess`
  is its opposite: it rebuilds sentences, chunks and embeddings from cached raw
  HTML and expects sids to move, so every stored citation and pinned gold sid
  is invalidated. Run `python -m evals repin --snapshot` **before** it and
  `python -m evals repin` after.
```

- [ ] **Step 3: Update CLAUDE.md**

Add under "Current state":

```markdown
- **Tables are first-class** (spec `docs/superpowers/specs/2026-08-12-tables-first-class-design.md`):
  one sentence per table row, `data-sid` on the `<tr>`, a table chunked
  atomically so its header travels with its data, and `normalize()` ignoring
  currency/percent spacing so `$ 383,285` and `$383,285` verify alike. The
  corpus was rebuilt with `python -m pipeline reprocess` and the golden set
  re-anchored with `python -m evals repin`, so **gold_sid_hit_rate before and
  after that rebuild are not comparable**.
- `store_filing(replace=True)` used to raise ForeignKeyViolation on any
  embedded filing because it never deleted chunks — `ingest --force` was broken
  on the whole corpus and nobody noticed, because tests use filings with no
  chunks. Fixed; `test_store_replace.py` pins it.
```

- [ ] **Step 4: Commit**

```bash
git add docs/design.md
git commit -m "docs: record tables as indexed content and the reprocess command"
```

---

## Verification

From a clean tree:

```
cd backend && ruff check . && pytest -q
cd ../frontend && npm test && npm run lint && npm run test:e2e
```

Expected: ruff clean; backend green with db tests running (not skipped); 42 vitest specs; 3 Playwright specs.

Then confirm the corpus actually carries table rows — `reprocess` rewrites unconditionally, so its own output cannot tell you whether it ran:

```
python -c "from pipeline.env import load_env; load_env(); from pipeline import db; c=db.connect(); cur=c.execute(\"select count(*) from sentences where table_id is not null\"); print(cur.fetchone()[0])"
```

Expected: non-zero, and `select count(*) from filings where viewer_html like '%<tr data-sid=%'` should return 120.
