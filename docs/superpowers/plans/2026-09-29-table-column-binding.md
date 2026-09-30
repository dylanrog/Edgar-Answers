# Table Column Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parse every numeric table cell into a record carrying its row label, column label and scale; split over-budget tables into header-carrying chunks; highlight the cited figure inside a cited row — with new evals recorded before and after.

**Architecture:** A new pure module `pipeline/tables.py` parses each `<table>` inside the canonicalizer's existing single DOM traversal, read-only, so sentences and viewer HTML stay byte-identical. Cells persist to `filing_tables`/`table_cells`. The chunker gains a `context` string per table chunk that is embedded, lexically indexed and shown to the model but never verified against. `verify.py` maps a matched quote onto cell spans, and the `citation` SSE event carries the cited cells to the frontend.

**Tech Stack:** Python 3.13, BeautifulSoup/lxml, psycopg 3, Postgres + pgvector, tiktoken, fastembed; Next.js + TypeScript, vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-29-table-column-binding-design.md`

## Global Constraints

- Python `>=3.13`; backend commands run from `backend/`.
- `ruff==0.16.1`; `ruff check .` must pass on every commit (CI runs lint before pytest).
- Commit messages and PR bodies contain **no AI attribution of any kind** (no Co-Authored-By, no tool names, no session trailers).
- `MAX_TOKENS = 450` stays; it now counts context tokens for table chunks. Do not raise it.
- Embedding dimension `vector(384)` and the BGE query prefix are unchanged.
- Retrieval stays hybrid (pgvector + FTS, RRF k=60). Do not simplify either arm.
- Sentences, sids, canonical text and viewer HTML must stay **byte-identical** (spec §4.1).
- Rule: when unsure, store NULL, never a guess (spec §4.2).
- `api.retrieval._TSVECTOR` must equal the `chunks_text_fts` index expression with `ch.` removed.
- Migrations are plain numbered SQL in `backend/migrations/`, applied by `python -m pipeline migrate`.
- DB tests carry `@pytest.mark.db` and need `TEST_DATABASE_URL` (the small test DB). `DATABASE_URL` is the full corpus — never point tests at it.
- Frontend commands (`npm test`, `npm run lint`, `npm run test:e2e`) run from `frontend/` in **PowerShell**; node is not on the git-bash PATH.
- Python processes may be blocked from `localhost:5432` by the tool sandbox. If a DB test or command times out connecting, re-run it with the sandbox disabled rather than treating it as a code failure.
- Eval runs append to `backend/evals/results.jsonl`; commit after **each** run so the next run records `git_dirty: false`. Never rebase a branch that carries eval rows.

## Review Focus

1. **A real row whose cell-by-cell join differs from its sentence** (inline tags splitting a number, `&nbsp;`): cells keep values but get NULL spans and the viewer falls back to whole-row highlighting — never a wrong cell. Pinned in Task 5 (`test_char_spans_are_null_when_the_row_text_does_not_reproduce`, `test_th_headers_nbsp_and_a_bad_colspan_are_handled`).
2. **A citation quoting only a row label, or a prose sentence**: sids resolve, `cells` is empty, nothing breaks. Pinned in Task 11.
3. **A chunk holding two small tables**: context carries one line per table and the prompt renders both. Pinned in Tasks 13 and 16.
4. **Malformed `colspan` or `<th>` header cells**: default to span 1, headers still bind. Pinned in Task 5.
5. **The model quoting a table-context line**: verification must fail (unverified badge), because context is never part of `chunk.text`. Pinned in Task 15 (`test_a_quote_found_only_in_context_is_unverified`).

## Measured baseline for this plan (prototype over the full cached corpus)

A prototype of Tasks 5–7 and 13 was run against all 120 cached filings before this plan was written:

- Sentences and viewer HTML: **0 of 120 filings differ** from today's canonicalizer.
- 12,040 tables; 281,668 numeric cells; **96.1%** have a `column_label`; **100%** have char spans; 56.7% of tables have a known scale; 66.5% are splittable.
- Chunks: today 15,432 (3,599 over 450 tokens); new chunker 21,259 (**479** over 450 counting context), of which 3,419 are pieces of split tables.

Use these as the expected neighbourhood for Task 17's `table-report` and `rechunk` output. A large deviation means something differs from the prototype and needs investigating before the eval runs.

---

## Part A — Evals first (PR A, branch `table-evals`, worktree `C:\Users\dylan\Projects\SEC-RAG-table-evals`)

The fixtures `backend/tests/fixtures/edgar_{nvda_revenue,aapl_segments,msft_segments}.html` are committed with this plan (real EDGAR excerpts, styles stripped, `colspan` kept). Part B's tests read them.

### Task 1: Golden schema, `value_accuracy` and `table_tail_recall@10`

**Files:**
- Modify: `backend/evals/harness.py`
- Modify: `backend/evals/faithfulness.py`
- Test: `backend/tests/test_evals.py`

**Interfaces:**
- Produces: `GoldenQuestion.category: str` (`""`, `"table_tail"`, `"column"`); `GoldenQuestion.expected_values: tuple[tuple[str, ...], ...]`; `faithfulness.values_present(answer: str, expected: tuple[tuple[str, ...], ...]) -> bool`; metric keys `table_tail_recall@10`, `table_tail_misses@10`, `value_accuracy`, `value_questions`, `value_misses`.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_evals.py`:

```python
def test_load_golden_reads_category_and_expected_values(tmp_path):
    path = tmp_path / "golden.yaml"
    path.write_text(
        "- id: t001\n"
        "  question: What was NVIDIA's Data Center revenue in fiscal 2025?\n"
        "  ticker: NVDA\n"
        '  accession: "0001045810-25-000023"\n'
        "  section: item7\n"
        "  gold_sids: [2768]\n"
        "  category: column\n"
        '  expected_values: [["115,186", "115.2 billion"]]\n',
        encoding="utf-8",
    )
    question = harness.load_golden(path)[0]
    assert question.category == "column"
    assert question.expected_values == (("115,186", "115.2 billion"),)


def test_load_golden_defaults_category_and_expected_values(tmp_path):
    path = tmp_path / "golden.yaml"
    path.write_text(
        "- id: q001\n  question: Q?\n  ticker: AAPL\n"
        '  accession: "A-1"\n  section: item7\n  gold_sids: [1]\n',
        encoding="utf-8",
    )
    question = harness.load_golden(path)[0]
    assert question.category == ""
    assert question.expected_values == ()


@pytest.mark.parametrize(
    "extra, message",
    [
        ("  category: tables\n", "category"),
        ("  expected_values: [[115186]]\n", "expected_values"),
        ("  expected_values: [[]]\n", "expected_values"),
        ('  expected_values: ["115,186"]\n', "expected_values"),
    ],
)
def test_load_golden_rejects_malformed_new_keys(tmp_path, extra, message):
    path = tmp_path / "golden.yaml"
    path.write_text(
        "- id: q009\n  question: Q?\n  ticker: AAPL\n"
        '  accession: "A-1"\n  section: item7\n  gold_sids: [1]\n' + extra,
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=message):
        harness.load_golden(path)


def test_table_tail_recall_scores_only_table_tail_entries(monkeypatch):
    tail = GoldenQuestion(
        id="t001", question="Tail?", ticker="AAPL", accession="ACC-1",
        section="item8", gold_sids=[5], category="table_tail",
    )
    calls = []
    monkeypatch.setattr("evals.harness.retrieve", _two_arm_retrieve(calls))
    metrics = harness.run_retrieval_eval(None, None, [GOLDEN, tail])
    assert metrics["table_tail_recall@10"] == 1.0
    assert metrics["table_tail_misses@10"] == []


def test_table_tail_keys_are_absent_without_table_tail_entries(monkeypatch):
    monkeypatch.setattr("evals.harness.retrieve", _two_arm_retrieve([]))
    metrics = harness.run_retrieval_eval(None, None, [GOLDEN])
    assert "table_tail_recall@10" not in metrics


def test_values_present_normalizes_spacing_and_needs_every_figure():
    from evals.faithfulness import values_present

    answer = "Data Center revenue was $115,186 million, up from $ 47,525 million."
    assert values_present(answer, (("$ 115,186", "115.2 billion"),))
    assert values_present(answer, (("115,186",), ("47,525",)))
    assert not values_present(answer, (("115,186",), ("15,005",)))
    assert values_present("It was $115.2 Billion.", (("115,186", "115.2 billion"),))


def test_faithfulness_reports_value_accuracy_over_entries_with_expected_values(monkeypatch):
    scored = GoldenQuestion(
        id="c001", question="Data Center revenue?", ticker="NVDA",
        accession="A-1", section="item7", gold_sids=[1],
        category="column", expected_values=(("115,186",),),
    )
    unscored = GoldenQuestion(
        id="q001", question="Why?", ticker="NVDA",
        accession="A-1", section="item7", gold_sids=[1],
    )
    missed = GoldenQuestion(
        id="c002", question="Gaming revenue?", ticker="NVDA",
        accession="A-1", section="item7", gold_sids=[1],
        category="column", expected_values=(("11,350",),),
    )

    class FakeEvent:
        def __init__(self, name, data):
            self.name, self.data = name, data

    def fake_stream(*args, **kwargs):
        yield FakeEvent("token", {"text": "Revenue was $115,186 million [1]."})
        yield FakeEvent(
            "done",
            {"chunks_retrieved": 8, "citations_total": 0,
             "citations_verified": 0, "unverified_answer": False},
        )

    monkeypatch.setattr("evals.faithfulness.answer_stream", fake_stream)
    metrics = run_faithfulness_eval(None, None, None, None, [scored, unscored, missed])
    assert metrics["value_questions"] == 2
    assert metrics["value_accuracy"] == 0.5
    assert metrics["value_misses"] == ["c002"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_evals.py -v -k "category or expected_values or table_tail or values_present or value_accuracy or malformed"`
Expected: FAIL — `GoldenQuestion.__init__() got an unexpected keyword argument 'category'` / `cannot import name 'values_present'`.

- [ ] **Step 3: Implement the schema** in `backend/evals/harness.py`.

Replace the `GoldenQuestion` dataclass (adding `_CATEGORIES` above it):

```python
_CATEGORIES = ("table_tail", "column")


@dataclass(frozen=True)
class GoldenQuestion:
    id: str
    question: str
    ticker: str
    accession: str
    section: str
    gold_sids: list[int]
    group: str = ""  # set by load_golden to entry.get("group", entry["id"])
    # Spec 2026-09-29 §7: which gap an entry measures ("" for the original set).
    category: str = ""
    # One inner tuple per required figure, holding its accepted spellings.
    expected_values: tuple[tuple[str, ...], ...] = ()
```

In `load_golden`, replace the line `questions.append(GoldenQuestion(*(entry[f] for f in _REQUIRED), group))` with:

```python
        category = entry.get("category", "")
        if category and category not in _CATEGORIES:
            raise ValueError(f"golden entry {entry_id}: unknown category {category!r}")
        expected = entry.get("expected_values", [])
        # Quoted strings only: an unquoted 115,186 inside a YAML flow list
        # parses as two integers, which is exactly the mistake to catch here.
        if not isinstance(expected, list) or not all(
            isinstance(spellings, list)
            and spellings
            and all(isinstance(s, str) and s for s in spellings)
            for spellings in expected
        ):
            raise ValueError(
                f"golden entry {entry_id}: expected_values must be a list of"
                " non-empty lists of quoted strings"
            )
        questions.append(
            GoldenQuestion(
                *(entry[f] for f in _REQUIRED),
                group,
                category,
                tuple(tuple(spellings) for spellings in expected),
            )
        )
```

In `run_retrieval_eval`, directly after the line `metrics |= _score(conn, embedder, questions, ks=ks, k_each=k_each, scoped=True)`, add:

```python
    # Spec §7.2: rows past the vector arm's reach today. Scoped, like recall@k.
    tail = [q for q in questions if q.category == "table_tail"]
    if tail:
        tail_scores = _score(conn, embedder, tail, ks=(10,), k_each=k_each, scoped=True)
        metrics["table_tail_recall@10"] = tail_scores["recall@10"]
        metrics["table_tail_misses@10"] = tail_scores["misses@10"]
```

- [ ] **Step 4: Implement `values_present` and the metric** in `backend/evals/faithfulness.py`.

Add below the existing imports:

```python
from api.normalize import normalize
```

Add below `_REFUSALS`:

```python
def values_present(answer: str, expected: tuple[tuple[str, ...], ...]) -> bool:
    """Spec §7.2: every required figure appears in at least one accepted
    spelling. Deterministic -- the same normalization citations use, so
    "$ 115,186" and "$115,186" count alike and no LLM judges anything."""
    haystack, _ = normalize(answer)
    return all(
        any(normalize(spelling)[0].strip() in haystack for spelling in spellings)
        for spellings in expected
    )
```

In `run_faithfulness_eval`, add counters beside the existing ones:

```python
    value_questions = 0
    value_hits = 0
    value_misses: list[str] = []
```

Replace `answer = "".join(text_parts).lower()` with:

```python
        raw_answer = "".join(text_parts)
        answer = raw_answer.lower()
        if question.expected_values:
            value_questions += 1
            if values_present(raw_answer, question.expected_values):
                value_hits += 1
            else:
                value_misses.append(question.id)
```

Replace the final `return {...}` with:

```python
    n = len(questions) or 1
    metrics = {
        "questions": len(questions),
        "answered_rate": round(answered / n, 4),
        "citations_total": total,
        "verified_rate": round(verified / total, 4) if total else 0.0,
        "gold_sid_hit_rate": round(gold_hits / n, 4),
        "unverified_answers": unverified_answers,
    }
    if value_questions:
        metrics["value_questions"] = value_questions
        metrics["value_accuracy"] = round(value_hits / value_questions, 4)
        metrics["value_misses"] = value_misses
    return metrics
```

(Delete the old `n = len(questions) or 1` line above the old return so it is not duplicated.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_evals.py -v`
Expected: all PASS (db-marked tests SKIP without `TEST_DATABASE_URL`).

- [ ] **Step 6: Lint and commit**

```bash
ruff check .
git add evals/harness.py evals/faithfulness.py tests/test_evals.py
git commit -m "feat: golden categories, value_accuracy and table_tail_recall@10"
```

---

### Task 2: `evals tail-candidates` authoring aid

**Files:**
- Create: `backend/evals/candidates.py`
- Modify: `backend/evals/__main__.py`
- Test: `backend/tests/test_candidates.py`

**Interfaces:**
- Produces: `candidates.tail_rows(sentences: list[tuple[int, str, int | None]], *, budget: int = MAX_TOKENS) -> list[tuple[int, str, int]]`; `candidates.tail_candidates(conn, *, ticker: str, limit: int = 40) -> list[tuple]`; CLI `python -m evals tail-candidates --ticker T [--limit N]`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_candidates.py`:

```python
from evals.candidates import tail_rows

from pipeline.chunk import count_tokens


def test_tail_rows_returns_numeric_table_rows_past_the_budget():
    filler = "word " * 30
    sentences = [
        (0, filler, None),  # prose, never a candidate
        (1, "Revenue $ 1,000 $ 900", 1),  # a table row inside the budget
        (2, "Header row without numbers", 1),
        (3, "Gaming $ 11,350 $ 10,447", 1),
    ]
    budget = count_tokens(filler) + count_tokens("Revenue $ 1,000 $ 900")
    rows = tail_rows(sentences, budget=budget)
    assert [(sid, text) for sid, text, _ in rows] == [(3, "Gaming $ 11,350 $ 10,447")]
    assert rows[0][2] >= budget


def test_tail_rows_is_empty_when_everything_fits():
    assert tail_rows([(0, "Revenue $ 1,000", 1)], budget=450) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_candidates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.candidates'`.

- [ ] **Step 3: Implement** — `backend/evals/candidates.py`:

```python
"""Authoring aid for the table_tail golden questions (spec 2026-09-29 §7.1).

Lists table rows that start beyond MAX_TOKENS into their chunk -- rows the
vector arm cannot see in the current corpus, because bge-small truncates
there. The offset is a running sum of per-sentence token counts, which is
close enough to choose questions; nothing is scored with it.
"""

from __future__ import annotations

from pipeline.chunk import MAX_TOKENS, count_tokens


def tail_rows(
    sentences: list[tuple[int, str, int | None]], *, budget: int = MAX_TOKENS
) -> list[tuple[int, str, int]]:
    """(sid, text, token offset) for numeric table rows past `budget`.
    `sentences` is (sid, text, table_id) in chunk order."""
    out: list[tuple[int, str, int]] = []
    offset = 0
    for sid, text, table_id in sentences:
        if table_id is not None and offset >= budget and any(ch.isdigit() for ch in text):
            out.append((sid, text, offset))
        offset += count_tokens(text)
    return out


def tail_candidates(conn, *, ticker: str, limit: int = 40) -> list[tuple]:
    """(accession, form_type, filing_date, section, sid, offset, text) rows."""
    out: list[tuple] = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ch.filing_id, ch.sid_start, ch.sid_end, ch.section,"
            " f.accession, f.form_type, f.filing_date"
            " FROM chunks ch JOIN filings f ON f.id = ch.filing_id"
            " JOIN companies c ON c.cik = f.cik"
            " WHERE c.ticker = %s AND ch.token_count > %s"
            " ORDER BY f.filing_date DESC, ch.sid_start",
            (ticker.upper(), MAX_TOKENS),
        )
        chunks = cur.fetchall()
        for filing_id, sid_start, sid_end, section, accession, form_type, filed in chunks:
            cur.execute(
                "SELECT sid, text, table_id FROM sentences"
                " WHERE filing_id = %s AND sid BETWEEN %s AND %s ORDER BY sid",
                (filing_id, sid_start, sid_end),
            )
            for sid, text, offset in tail_rows(cur.fetchall()):
                out.append(
                    (accession, form_type, filed.isoformat(), section, sid, offset, text[:120])
                )
                if len(out) >= limit:
                    return out
    return out
```

- [ ] **Step 4: Wire the CLI** in `backend/evals/__main__.py`.

Add this function above `main`:

```python
def cmd_tail_candidates(args) -> None:
    from . import candidates

    with db.connect() as conn:
        rows = candidates.tail_candidates(conn, ticker=args.ticker, limit=args.limit)
    for row in rows:
        print(" | ".join(str(value) for value in row))
    print(f"\n{len(rows)} candidate rows")
```

In `main`, after the `periods` sub-parser, add:

```python
    p_tail = sub.add_parser(
        "tail-candidates",
        help="list table rows past the vector arm's token limit (golden-set authoring aid)",
    )
    p_tail.add_argument("--ticker", required=True)
    p_tail.add_argument("--limit", type=int, default=40)
```

and in the dispatch chain, before the final `else`:

```python
    elif args.cmd == "tail-candidates":
        cmd_tail_candidates(args)
```

- [ ] **Step 5: Run tests, lint, commit**

Run: `pytest tests/test_candidates.py -v` → PASS. Then:

```bash
ruff check .
git add evals/candidates.py evals/__main__.py tests/test_candidates.py
git commit -m "feat: evals tail-candidates lists rows past the vector arm's reach"
```

---

### Task 3: Author the 14 new golden entries — CONTROLLER + DYLAN, not a subagent

This task needs Dylan's eyes on every entry (design §2: eval answers are hand-verified), so the controller does it directly. Use `DATABASE_URL` (full corpus).

- [ ] **Step 1: Draft 8 `table_tail` candidates.** Run for at least four companies (e.g. `NVDA`, `MSFT`, `AMZN`, `JPM`, `META`):

```bash
python -m evals tail-candidates --ticker NVDA --limit 30
```

Pick rows that name one clear figure (a total, a segment line, a balance-sheet line). Skip rows of bare footnote numbers. For each chosen row, read its table's header rows to learn the period:

```bash
docker exec -i sec-rag-db-1 psql -U user -d edgar_answers -c "SELECT s.sid, left(s.text, 160) FROM sentences s JOIN filings f ON f.id = s.filing_id WHERE f.accession = '<ACCESSION>' AND s.table_id = (SELECT table_id FROM sentences WHERE filing_id = f.id AND sid = <SID>) ORDER BY s.sid LIMIT 12;"
```

- [ ] **Step 2: Draft 6 `column` entries.** Each answer is a single cell whose period is **not** the first numeric column of the pinned filing's table, or which sits in a second header band. Start from these verified targets (spec §2) and find the rest the same way:
  - Microsoft Intelligent Cloud revenue, fiscal 2025 → `106,265` (second column of the FY2026 10-K segment table).
  - NVIDIA Data Center revenue, fiscal 2025 → `115,186` (second column of the FY2026 10-K; first in the FY2025 10-K).
  - Apple Americas net sales, three months ended March 29, 2025 → `40,315` (second band of the Q2 FY2026 10-Q segment table).
  - Three more across at least two other companies (e.g. an AMZN AWS or META segment figure from a prior-year column).

- [ ] **Step 3: Write each entry** at the end of `backend/evals/golden.yaml`, after a comment block explaining the categories. Format:

```yaml
# Spec 2026-09-29 §7: table_tail entries pin rows past the vector arm's reach
# in the pre-change corpus; column entries pin a single cell whose column is
# not the table's first. expected_values holds each required figure's
# accepted spellings, always quoted.

- id: t001
  question: What was NVIDIA's Gaming revenue in fiscal year 2025?
  ticker: NVDA
  accession: "0001045810-25-000023"
  section: item7
  gold_sids: [2771]
  category: table_tail
  expected_values: [["11,350", "11.4 billion"]]
```

Use ids `t001`–`t008` and `c001`–`c006`. The question names the company, the line item and the period in plain words; it never quotes the row text verbatim (that would make the lexical arm trivial). Every `table_tail` entry that asks for a single figure also carries `expected_values`.

- [ ] **Step 4: Dylan verifies every entry.** For each, show Dylan: the question, the pinned row text, the header rows, and the viewer link (`http://localhost:3000` with the filing open, or `GET http://localhost:8000/filings/<accession>` and find `data-sid="<sid>"`). **Do not commit an entry Dylan has not approved.**

- [ ] **Step 5: Validate and commit**

```bash
python -m evals verify
pytest tests/test_evals.py -v
git add evals/golden.yaml
git commit -m "evals: add table_tail and column golden questions"
```

Expected: `all 34 entries verified`.

---

### Task 4: Baseline eval ×3 and PR A — CONTROLLER

- [ ] **Step 1: Confirm a clean tree on the full corpus.** `git status` shows nothing to commit; `DATABASE_URL` points at the full corpus (120 filings).

- [ ] **Step 2: Run, commit, repeat — three times.**

```bash
python -m evals run
git add evals/results.jsonl
git commit -m "evals: record table-evals baseline run 1 of 3"
```

Repeat with `run 2 of 3` and `run 3 of 3`. Each row must show `git_dirty: false`.

- [ ] **Step 3: Record the baseline** in the PR description: for each run, `recall@10`, `unfiltered_recall@10`, `table_tail_recall@10`, `value_accuracy`, `verified_rate`, `answered_rate`, `gold_sid_hit_rate`.

- [ ] **Step 4: Push and open PR A** (no AI attribution in the body):

```bash
git push -u origin table-evals
gh pr create --base main --head table-evals --title "Evals for table column binding (baseline)" --body-file "$TMP/pr-body.md"   # write the body there first; keep it out of the repo
```

Merge after review. Part B branches from the merged `main`.

---

## Part B — Implementation (PR B, branch `table-column-binding` from `main` after PR A merges)

Create the worktree the usual way and copy `backend/.env` into it:

```bash
git -C C:/Users/dylan/Projects/SEC-RAG fetch origin
git -C C:/Users/dylan/Projects/SEC-RAG worktree add -b table-column-binding ../SEC-RAG-table-column-binding origin/main
git -C C:/Users/dylan/Projects/SEC-RAG-table-column-binding branch --unset-upstream
cp C:/Users/dylan/Projects/SEC-RAG/backend/.env C:/Users/dylan/Projects/SEC-RAG-table-column-binding/backend/.env
```

The backend package is installed editable from the primary checkout (`C:\Users\dylan\Projects\SEC-RAG\backend`), so from a sibling worktree `import api` / `import pipeline` resolve to the *primary* checkout's `src/`, not this worktree's. Run pytest, `python -m pipeline` and `python -m evals` from this worktree's `backend/` with `PYTHONPATH=src` (verify with `python -c "import api; print(api.__file__)"`), or every test and eval will exercise `main`'s code while recording this branch's `git_sha`.

### Task 5: The table parser (`pipeline/tables.py`)

**Files:**
- Create: `backend/src/pipeline/tables.py`
- Test: `backend/tests/test_tables.py`

**Interfaces:**
- Produces: `TableInfo(table_id: int, caption: str | None, scale: Decimal | None, splittable: bool)`; `Cell(sid, col, cell_index, char_start, char_end, table_id, raw, value, kind, row_label, column_label, scale_applies)` (field order exactly this); `parse_table(table_id: int, rows: list[tuple[int, str, Tag]], caption: str | None) -> tuple[TableInfo, list[Cell]]`; constants `LABEL_JOIN = " › "`, `SCALE_NAMES: dict[Decimal, str]`.

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_tables.py`:

```python
from decimal import Decimal
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from pipeline.tables import TableInfo, parse_table

FIXTURES = Path(__file__).parent / "fixtures"


def rows_of(html: str) -> list[tuple[int, str, object]]:
    """(sid, sentence text, <tr>) for each row with text, numbered from 0 --
    the same row text the canonicalizer builds."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for tr in soup.find("table").find_all("tr"):
        text = " ".join(tr.get_text(" ", strip=True).split())
        if text:
            out.append((len(out), text, tr))
    return out


def fixture_rows(name: str):
    return rows_of((FIXTURES / name).read_text(encoding="utf-8"))


def by_key(cells):
    return {(c.sid, c.col): c for c in cells}


# --- NVIDIA: colspan alignment, '$' in its own cell --------------------------


@pytest.fixture(scope="module")
def nvda():
    return parse_table(1, fixture_rows("edgar_nvda_revenue.html"), "revenue by market:")


def test_nvda_value_binds_to_its_grid_column_not_its_cell_index(nvda):
    _, cells = nvda
    cell = by_key(cells)[(3, 4)]
    assert cell.raw == "115,186"
    assert cell.value == Decimal("115186")
    assert cell.cell_index == 2
    assert cell.column_label == "Year Ended › Jan 26, 2025"
    assert cell.row_label == "Data Center"
    assert cell.kind == "number"


def test_nvda_row_without_dollar_cells_binds_to_the_same_columns(nvda):
    _, cells = nvda
    compute = [c for c in cells if c.sid == 4]
    assert [(c.raw, c.column_label) for c in compute] == [
        ("102,196", "Year Ended › Jan 26, 2025"),
        ("38,950", "Year Ended › Jan 28, 2024"),
        ("11,317", "Year Ended › Jan 29, 2023"),
    ]


def test_nvda_scale_comes_from_the_header_band_and_is_stripped_from_labels(nvda):
    info, cells = nvda
    assert info == TableInfo(1, "revenue by market:", Decimal("1000000"), True)
    assert all("millions" not in (c.column_label or "").lower() for c in cells)
    assert all(c.scale_applies for c in cells)


def test_nvda_char_spans_slice_the_row_sentence(nvda):
    _, cells = nvda
    rows = {sid: text for sid, text, _ in fixture_rows("edgar_nvda_revenue.html")}
    for cell in cells:
        assert rows[cell.sid][cell.char_start : cell.char_end] == cell.raw


# --- Apple: a second header band mid-table, scale only in the caption --------


@pytest.fixture(scope="module")
def aapl():
    return parse_table(1, fixture_rows("edgar_aapl_segments.html"), "Segments (in millions):")


def test_aapl_second_band_replaces_the_first(aapl):
    _, cells = aapl
    first = by_key(cells)[(2, 4)]
    second = by_key(cells)[(8, 4)]
    assert (first.raw, first.column_label) == (
        "45,093",
        "Three Months Ended March 28, 2026 › Americas",
    )
    assert (second.raw, second.column_label) == (
        "40,315",
        "Three Months Ended March 29, 2025 › Americas",
    )


def test_aapl_parentheses_mean_negative(aapl):
    _, cells = aapl
    cell = by_key(cells)[(3, 3)]
    assert cell.raw == "( 23,114 )"
    assert cell.value == Decimal("-23114")


def test_aapl_dash_is_a_nil_cell(aapl):
    _, cells = aapl
    cell = by_key(cells)[(2, 34)]
    assert cell.kind == "nil"
    assert cell.value is None
    assert cell.column_label == "Three Months Ended March 28, 2026 › Corporate"


def test_aapl_scale_falls_back_to_the_caption(aapl):
    info, _ = aapl
    assert info.scale == Decimal("1000000")
    assert info.splittable is True


def test_aapl_without_a_caption_scale_is_unknown():
    info, _ = parse_table(1, fixture_rows("edgar_aapl_segments.html"), None)
    assert info.scale is None


# --- Microsoft: year headers, group rows, percent column ---------------------


@pytest.fixture(scope="module")
def msft():
    return parse_table(1, fixture_rows("edgar_msft_segments.html"), "SEGMENT RESULTS OF OPERATIONS")


def test_msft_year_header_row_is_a_header_not_data(msft):
    _, cells = msft
    assert 0 not in {c.sid for c in cells}
    cell = by_key(cells)[(6, 7)]
    assert (cell.raw, cell.column_label) == ("106,265", "2025")


def test_msft_repeated_row_label_is_qualified_by_its_group(msft):
    _, cells = msft
    assert by_key(cells)[(2, 3)].row_label == "Productivity and Business Processes › Revenue"
    assert by_key(cells)[(6, 3)].row_label == "Intelligent Cloud › Revenue"


def test_msft_percent_cell_is_never_scaled(msft):
    info, cells = msft
    cell = by_key(cells)[(6, 11)]
    assert (cell.raw, cell.kind, cell.value) == ("30%", "percent", Decimal("30"))
    assert cell.column_label == "Percentage Change"
    assert cell.scale_applies is False
    assert info.scale == Decimal("1000000")


# --- NULL over guess, and cell-shape edge cases -------------------------------


def test_a_number_under_two_header_cells_gets_no_column_label():
    rows = rows_of(
        "<table><tr><td></td><td>Q1</td><td>Q2</td></tr>"
        '<tr><td>Sales</td><td colspan="2">1,000</td></tr></table>'
    )
    _, cells = parse_table(1, rows, None)
    assert cells[0].column_label is None


def test_a_table_with_no_header_band_is_not_splittable():
    rows = rows_of("<table><tr><td>Sales</td><td>1,000</td></tr></table>")
    info, cells = parse_table(1, rows, None)
    assert info.splittable is False
    assert cells[0].column_label is None
    assert cells[0].value == Decimal("1000")


def test_lone_percent_and_paren_cells_attach_to_the_number_on_their_left():
    rows = rows_of(
        "<table><tr><td></td><td>2025</td><td></td><td>Change</td><td></td></tr>"
        "<tr><td>Margin</td><td>(1,234</td><td>)</td><td>16</td><td>%</td></tr></table>"
    )
    _, cells = parse_table(1, rows, None)
    assert [(c.raw, c.value, c.kind) for c in cells] == [
        ("(1,234", Decimal("-1234"), "number"),
        ("16", Decimal("16"), "percent"),
    ]


def test_per_share_rows_are_exempt_when_the_scale_says_so():
    rows = rows_of(
        "<table><tr><td>(In millions, except per share amounts)</td><td>2025</td></tr>"
        "<tr><td>Net income</td><td>1,000</td></tr>"
        "<tr><td>Diluted earnings per share</td><td>1.65</td></tr></table>"
    )
    _, cells = parse_table(1, rows, None)
    assert [(c.row_label, c.scale_applies) for c in cells] == [
        ("Net income", True),
        ("Diluted earnings per share", False),
    ]


def test_th_headers_nbsp_and_a_bad_colspan_are_handled():
    rows = rows_of(
        '<table><tr><th></th><th colspan="bogus">2025</th></tr>'
        "<tr><td>Revenue</td><td>$&nbsp;1,234</td></tr></table>"
    )
    _, cells = parse_table(1, rows, None)
    assert (cells[0].raw, cells[0].value, cells[0].column_label) == (
        "$ 1,234",
        Decimal("1234"),
        "2025",
    )
    assert cells[0].col == 1


def test_char_spans_are_null_when_the_row_text_does_not_reproduce():
    sid, _text, tr = rows_of(
        "<table><tr><td></td><td>2025</td></tr><tr><td>Revenue</td><td>1,234</td></tr></table>"
    )[1]
    header = rows_of("<table><tr><td></td><td>2025</td></tr></table>")[0]
    _, cells = parse_table(1, [header, (sid, "not the row text", tr)], None)
    assert (cells[0].char_start, cells[0].char_end) == (None, None)
    assert cells[0].value == Decimal("1234")
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_tables.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.tables'`.

- [ ] **Step 3: Implement** — `backend/src/pipeline/tables.py`:

```python
"""Column binding: parse one <table> into numeric cell records (spec 2026-09-29 §4).

Read-only on the DOM -- the canonicalizer's sentences and viewer HTML must
come out byte-identical with or without this module (spec §4.1). Every rule
that cannot decide stores NULL rather than a guess (spec §4.2): a NULL label
makes a number unverifiable, a wrong one would make a wrong answer look
verified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise

# Spec §4.3 rule 2. Applied after removing whitespace, '$' and ','.
_NUMBER = re.compile(r"^\(?-?\d+(?:\.\d+)?\)?%?$")
_NIL = {"—", "–", "-", "−"}
_FILLER = {"", "$", "%", ")", "(", "$("}
_YEAR = re.compile(r"^\d{4}$")
_SCALE = re.compile(r"in\s+(thousands|millions|billions)", re.IGNORECASE)
_SCALE_PHRASE = re.compile(
    r"\(?\s*(?:dollars\s+|amounts\s+)?in\s+(?:thousands|millions|billions)[^)]*\)?",
    re.IGNORECASE,
)
_EXCEPT = re.compile(r"except\s+(?:per[\s-]share|percentages)", re.IGNORECASE)
_SCALES = {
    "thousands": Decimal(1000),
    "millions": Decimal(1000000),
    "billions": Decimal(1000000000),
}
SCALE_NAMES = {value: name for name, value in _SCALES.items()}
LABEL_JOIN = " › "


@dataclass(frozen=True)
class TableInfo:
    table_id: int
    caption: str | None
    scale: Decimal | None
    splittable: bool


@dataclass(frozen=True)
class Cell:
    sid: int
    col: int
    cell_index: int
    char_start: int | None
    char_end: int | None
    table_id: int
    raw: str
    value: Decimal | None
    kind: str  # 'number' | 'percent' | 'nil'
    row_label: str | None
    column_label: str | None
    scale_applies: bool


@dataclass
class _GridCell:
    index: int  # position among the row's td/th == the browser's tr.cells[i]
    start: int  # half-open grid range [start, end)
    end: int
    text: str
    kind: str  # 'number' | 'percent' | 'nil' | 'filler' | 'text'
    value: Decimal | None = None
    year: bool = False


@dataclass
class _Row:
    sid: int
    sentence_text: str
    cells: list[_GridCell]


def cell_text(td) -> str:
    return " ".join(td.get_text(" ", strip=True).split())


def _colspan(td) -> int:
    try:
        return max(1, int(td.get("colspan", 1)))
    except (TypeError, ValueError):
        return 1


def _classify(text: str) -> tuple[str, Decimal | None, bool]:
    """(kind, value, is_bare_year) for one cell's text."""
    squeezed = "".join(text.split()).replace("$", "").replace(",", "")
    if squeezed in _FILLER:
        return "filler", None, False
    if squeezed in _NIL:
        return "nil", None, False
    if not _NUMBER.match(squeezed):
        return "text", None, False
    negative = "(" in squeezed or ")" in squeezed or squeezed.startswith("-")
    digits = squeezed.strip("()%").lstrip("-")
    value = Decimal(digits)
    kind = "percent" if squeezed.endswith("%") else "number"
    bare = text.strip()
    year = bool(_YEAR.match(bare)) and 1990 <= int(bare) <= 2100
    return kind, (-value if negative else value), year


def _grid_row(sid: int, sentence_text: str, tr) -> _Row:
    cells: list[_GridCell] = []
    cursor = 0
    for index, td in enumerate(tr.find_all(["td", "th"], recursive=False)):
        span = _colspan(td)
        text = cell_text(td)
        kind, value, year = _classify(text)
        cells.append(_GridCell(index, cursor, cursor + span, text, kind, value, year))
        cursor += span
    # A lone '%' or ')' cell belongs to the number on its left (rule 2).
    for left, right in pairwise(cells):
        if left.kind == "number" and right.kind == "filler":
            if right.text == "%":
                left.kind = "percent"
            elif right.text == ")" and left.value is not None and left.value > 0:
                left.value = -left.value
    return _Row(sid, sentence_text, cells)


def _char_spans(row: _Row) -> dict[int, tuple[int, int]] | None:
    """Each cell's [start, end) inside the row sentence, or None if the
    cell-by-cell join does not reproduce the sentence exactly (spec §5.7)."""
    spans: dict[int, tuple[int, int]] = {}
    pieces: list[str] = []
    cursor = 0
    for cell in row.cells:
        if not cell.text:
            continue
        if pieces:
            cursor += 1
        spans[cell.index] = (cursor, cursor + len(cell.text))
        pieces.append(cell.text)
        cursor += len(cell.text)
    if " ".join(pieces) != row.sentence_text:
        return None
    return spans


def _is_year_row(row: _Row) -> bool:
    numeric = [c for c in row.cells if c.kind in ("number", "percent")]
    return bool(numeric) and all(c.year for c in numeric)


def parse_table(
    table_id: int, rows: list[tuple[int, str, object]], caption: str | None
) -> tuple[TableInfo, list[Cell]]:
    """Parse one table. `rows` is (sid, sentence text, <tr>) for every row that
    produced a sentence, in document order (spacer rows never do)."""
    grid = [_grid_row(sid, text, tr) for sid, text, tr in rows]
    year_rows = {id(r) for r in grid if _is_year_row(r)}

    # Rule 3: label columns end where the first value starts. Year-only
    # header rows are ignored here, or '2025' would claim to be data.
    starts = [
        c.start
        for r in grid
        if id(r) not in year_rows
        for c in r.cells
        if c.kind in ("number", "percent", "nil")
    ]
    if not starts:
        return TableInfo(table_id, caption, _scale(grid, caption)[0], False), []
    label_end = min(starts)

    scale, exempt_per_share = _scale(grid, caption)
    band: list[_Row] = []
    data_since_band = False
    group: str | None = None
    splittable = True
    out: list[Cell] = []

    for row in grid:
        values = [
            c for c in row.cells
            if c.start >= label_end and c.kind in ("number", "percent", "nil")
        ]
        label = " ".join(
            c.text for c in row.cells if c.end <= label_end and c.kind == "text" and c.text
        ) or None
        in_value_columns = any(c.text for c in row.cells if c.end > label_end)

        if values and id(row) not in year_rows:
            data_since_band = True
            if not band:
                splittable = False
            row_label = LABEL_JOIN.join(p for p in (group, label) if p) or None
            spans = _char_spans(row)
            for c in values:
                span = spans.get(c.index) if spans is not None else None
                per_share = bool(row_label) and "per share" in row_label.lower()
                out.append(
                    Cell(
                        sid=row.sid,
                        col=c.start,
                        cell_index=c.index,
                        char_start=span[0] if span else None,
                        char_end=span[1] if span else None,
                        table_id=table_id,
                        raw=c.text,
                        value=c.value,
                        kind=c.kind,
                        row_label=row_label,
                        column_label=_column_label(band, c),
                        scale_applies=c.kind == "number" and not (exempt_per_share and per_share),
                    )
                )
        elif in_value_columns:
            # Rule 5: a header row after data starts a new band (Apple's
            # mid-table period change) and clears the group.
            if data_since_band:
                band, data_since_band, group = [], False, None
            band.append(row)
        elif label:
            group = label

    return TableInfo(table_id, caption, scale, splittable), out


def _column_label(band: list[_Row], cell: _GridCell) -> str | None:
    parts: list[str] = []
    for row in band:
        over = [
            c for c in row.cells
            if c.text and c.start < cell.end and cell.start < c.end
        ]
        if len(over) > 1:
            return None  # two headers claim this number: NULL, never a guess
        if over:
            text = " ".join(_SCALE_PHRASE.sub(" ", over[0].text).split())
            if text:
                parts.append(text)
    return LABEL_JOIN.join(parts) or None


def _scale(grid: list[_Row], caption: str | None) -> tuple[Decimal | None, bool]:
    """Rule 6: header rows first, then the caption. First match wins."""
    header_texts = [
        " ".join(c.text for c in r.cells if c.text)
        for r in grid
        if not any(c.kind in ("number", "percent", "nil") for c in r.cells) or _is_year_row(r)
    ]
    for text in [*header_texts, caption or ""]:
        match = _SCALE.search(text)
        if match:
            return _SCALES[match.group(1).lower()], bool(_EXCEPT.search(text))
    return None, False
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_tables.py -v`
Expected: 18 PASS.

- [ ] **Step 5: Lint and commit**

```bash
ruff check .
git add src/pipeline/tables.py tests/test_tables.py
git commit -m "feat: parse EDGAR tables into column-bound numeric cells"
```

---

### Task 6: Table context rendering

**Files:**
- Modify: `backend/src/pipeline/tables.py` (append)
- Test: `backend/tests/test_table_context.py`

**Interfaces:**
- Consumes: `TableInfo`, `Cell`, `LABEL_JOIN`, `SCALE_NAMES` from Task 5.
- Produces: `CAPTION_CHARS = 200`; `render_context(info: TableInfo, row: list[Cell]) -> str`; `row_contexts(tables: dict[int, TableInfo], cells: list[Cell]) -> dict[int, str]` (keys are data-row sids).

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_table_context.py`:

```python
from decimal import Decimal

from pipeline.tables import Cell, TableInfo, render_context, row_contexts


def cell(sid, col, column_label, row_label="Revenue", table_id=1):
    return Cell(
        sid=sid,
        col=col,
        cell_index=col,
        char_start=None,
        char_end=None,
        table_id=table_id,
        raw="1",
        value=Decimal("1"),
        kind="number",
        row_label=row_label,
        column_label=column_label,
        scale_applies=True,
    )


NVDA = TableInfo(
    1, "The following table summarizes revenue by specialized markets:", Decimal("1E+6"), True
)


def test_context_names_caption_scale_and_factored_columns():
    row = [
        cell(6, 16, "Year Ended › Jan 29, 2023", "Data Center"),
        cell(6, 4, "Year Ended › Jan 26, 2025", "Data Center"),
        cell(6, 10, "Year Ended › Jan 28, 2024", "Data Center"),
    ]
    assert render_context(NVDA, row) == (
        "Table: The following table summarizes revenue by specialized markets:"
        " | Scale: in millions"
        " | Columns: Year Ended › [Jan 26, 2025; Jan 28, 2024; Jan 29, 2023]"
    )


def test_context_adds_the_group_and_skips_unknowns():
    info = TableInfo(1, None, None, True)
    row = [
        cell(9, 3, "2026", "Intelligent Cloud › Revenue"),
        cell(9, 7, "2025", "Intelligent Cloud › Revenue"),
        cell(9, 11, None, "Intelligent Cloud › Revenue"),
    ]
    assert render_context(info, row) == "Columns: 2026; 2025 | Group: Intelligent Cloud"


def test_columns_with_no_shared_prefix_are_listed_plainly():
    info = TableInfo(1, None, None, True)
    row = [cell(1, 1, "2026"), cell(1, 2, "Percentage Change")]
    assert render_context(info, row) == "Columns: 2026; Percentage Change"


def test_a_long_caption_is_truncated():
    info = TableInfo(1, "x" * 500, None, True)
    assert render_context(info, [cell(1, 1, None)]) == "Table: " + "x" * 200


def test_row_contexts_cover_data_rows_only_and_skip_unknown_tables():
    tables = {1: TableInfo(1, None, None, True)}
    cells = [cell(5, 1, "2025"), cell(5, 2, "2024"), cell(7, 1, "2025", table_id=2)]
    assert row_contexts(tables, cells) == {5: "Columns: 2025; 2024"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_table_context.py -v`
Expected: FAIL — `cannot import name 'render_context'`.

- [ ] **Step 3: Implement** — append to `backend/src/pipeline/tables.py`:

```python
# Spec §5.3: the caption is truncated so a long lead-in paragraph cannot eat
# the chunk's token budget.
CAPTION_CHARS = 200


def render_context(info: TableInfo, row: list[Cell]) -> str:
    """The context line for a chunk whose first data row (of this table) is
    `row` (spec §5.3). One line, ' | '-separated, parts omitted when unknown."""
    parts: list[str] = []
    if info.caption:
        parts.append(f"Table: {info.caption[:CAPTION_CHARS]}")
    if info.scale is not None:
        parts.append(f"Scale: in {SCALE_NAMES[info.scale]}")
    labels = list(
        dict.fromkeys(c.column_label for c in sorted(row, key=lambda c: c.col) if c.column_label)
    )
    if labels:
        parts.append("Columns: " + _factor(labels))
    row_label = row[0].row_label
    if row_label and LABEL_JOIN in row_label:
        parts.append("Group: " + row_label.rsplit(LABEL_JOIN, 1)[0])
    return " | ".join(parts)


def row_contexts(tables: dict[int, TableInfo], cells: list[Cell]) -> dict[int, str]:
    """sid -> context line, for every data row (a row with at least one cell)."""
    by_sid: dict[int, list[Cell]] = {}
    for cell in cells:
        by_sid.setdefault(cell.sid, []).append(cell)
    return {
        sid: render_context(tables[row[0].table_id], row)
        for sid, row in by_sid.items()
        if row[0].table_id in tables
    }


def _factor(labels: list[str]) -> str:
    """'A › x; A › y' -> 'A › [x; y]'. Apple's segment tables repeat a
    38-character period on all seven columns; factoring it out cut that
    context from 141 to 78 tokens of a 450-token budget."""
    split = [label.split(LABEL_JOIN) for label in labels]
    shared = 0
    while all(len(parts) > shared + 1 for parts in split) and len(
        {tuple(parts[: shared + 1]) for parts in split}
    ) == 1:
        shared += 1
    if shared == 0 or len(labels) == 1:
        return "; ".join(labels)
    prefix = LABEL_JOIN.join(split[0][:shared])
    rest = "; ".join(LABEL_JOIN.join(parts[shared:]) for parts in split)
    return f"{prefix}{LABEL_JOIN}[{rest}]"
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_table_context.py tests/test_tables.py -v`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
ruff check .
git add src/pipeline/tables.py tests/test_table_context.py
git commit -m "feat: render a table chunk's context line from its cells"
```

---

### Task 7: Canonicalizer integration, guarded by a byte-identity snapshot

**Files:**
- Create: `backend/tests/test_canonical_snapshot.py`
- Create: `backend/tests/fixtures/canonical_snapshot.json` (generated in Step 2, **before** touching `canonicalize.py`)
- Modify: `backend/src/pipeline/canonicalize.py`
- Test: `backend/tests/test_canonicalize_cells.py`

**Interfaces:**
- Consumes: `parse_table`, `TableInfo`, `Cell` (Task 5).
- Produces: `CanonicalFiling.tables: list[TableInfo]` and `CanonicalFiling.cells: list[Cell]` (both default `[]`, so three-argument constructions keep working).

- [ ] **Step 1: Write the snapshot test** — `backend/tests/test_canonical_snapshot.py`:

```python
"""Spec 2026-09-29 §4.1: column binding must not move a single byte of what
the canonicalizer already produces. The snapshot is recorded from the code
*before* the change; this test holds the change to it."""

import hashlib
import json
from pathlib import Path

from pipeline.canonicalize import canonicalize

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = FIXTURES / "canonical_snapshot.json"


def _digest(raw: str) -> dict:
    canonical = canonicalize(raw, "10-K")
    sentences = [
        [s.sid, s.section, s.text, s.char_start, s.char_end, s.table_id]
        for s in canonical.sentences
    ]
    return {
        "sentences": hashlib.sha256(json.dumps(sentences).encode()).hexdigest(),
        "canonical_text": hashlib.sha256(canonical.canonical_text.encode()).hexdigest(),
        "viewer_html": hashlib.sha256(canonical.viewer_html.encode()).hexdigest(),
    }


def current() -> dict:
    return {
        path.name: _digest(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURES.glob("*.html"))
    }


def write_snapshot() -> None:
    SNAPSHOT.write_text(json.dumps(current(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_column_binding_leaves_sentences_and_viewer_html_byte_identical():
    assert current() == json.loads(SNAPSHOT.read_text(encoding="utf-8"))
```

- [ ] **Step 2: Record the snapshot from the UNCHANGED canonicalizer and commit it**

```bash
python -c "from tests.test_canonical_snapshot import write_snapshot; write_snapshot()"
pytest tests/test_canonical_snapshot.py -v
git add tests/test_canonical_snapshot.py tests/fixtures/canonical_snapshot.json
git commit -m "test: snapshot canonicalizer output before column binding"
```

Expected: PASS, and the JSON has one entry per fixture `.html` (including the three `edgar_*` files).

- [ ] **Step 3: Write the failing integration tests** — `backend/tests/test_canonicalize_cells.py`:

```python
from pathlib import Path

import pytest

from pipeline.canonicalize import canonicalize

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return canonicalize((FIXTURES / name).read_text(encoding="utf-8"), "10-K")


@pytest.fixture(scope="module")
def nvda():
    return load("edgar_nvda_revenue.html")


def test_cells_carry_the_real_sentence_ids(nvda):
    by_sid = {s.sid: s.text for s in nvda.sentences}
    cell = next(c for c in nvda.cells if c.raw == "115,186")
    assert by_sid[cell.sid] == "Data Center $ 115,186 $ 47,525 $ 15,005"
    assert (cell.col, cell.cell_index) == (4, 2)
    assert cell.column_label == "Year Ended › Jan 26, 2025"


def test_the_caption_is_the_preceding_prose_sentence(nvda):
    assert [t.caption for t in nvda.tables] == [
        "The following table summarizes revenue by specialized markets:"
    ]


def test_header_rows_produce_no_cells(nvda):
    headers = {
        "Year Ended",
        "Jan 26, 2025 Jan 28, 2024 Jan 29, 2023",
        "Revenue by End Market: (In millions)",
    }
    header_sids = {s.sid for s in nvda.sentences if s.text in headers}
    assert len(header_sids) == 3
    assert header_sids.isdisjoint({c.sid for c in nvda.cells})


def test_a_table_right_after_another_has_no_caption():
    shapes = load("table_shapes.html")
    assert [t.caption for t in shapes.tables] == [
        "Net sales discussion follows.",
        None,
        "Loose text beside a table.",
        None,
    ]


@pytest.mark.parametrize(
    "name", ["edgar_nvda_revenue.html", "edgar_aapl_segments.html", "edgar_msft_segments.html"]
)
def test_every_cell_span_slices_its_row_sentence(name):
    canonical = load(name)
    by_sid = {s.sid: s.text for s in canonical.sentences}
    assert canonical.cells
    for cell in canonical.cells:
        assert by_sid[cell.sid][cell.char_start : cell.char_end] == cell.raw


def test_prose_only_filings_have_no_tables():
    canonical = canonicalize("<html><body><p>Only prose here.</p></body></html>", "10-K")
    assert canonical.tables == [] and canonical.cells == []
```

- [ ] **Step 4: Run to verify they fail**

Run: `pytest tests/test_canonicalize_cells.py -v`
Expected: FAIL — `AttributeError: 'CanonicalFiling' object has no attribute 'cells'`.

- [ ] **Step 5: Implement** in `backend/src/pipeline/canonicalize.py`.

Imports — change `from dataclasses import dataclass` to `from dataclasses import dataclass, field`, and add below `from .sections import SectionTracker`:

```python
from .tables import Cell, TableInfo, parse_table
```

`CanonicalFiling` becomes:

```python
@dataclass(frozen=True)
class CanonicalFiling:
    canonical_text: str
    sentences: list[Sentence]
    viewer_html: str
    # Column binding (spec 2026-09-29 §4). Defaulted so the three-argument
    # constructions used across the tests keep working.
    tables: list[TableInfo] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)
```

In `canonicalize()`, replace:

```python
    body = soup.body if soup.body is not None else soup
    for kind, payload, table_id in list(_iter_units(body, count(1))):
        if kind == "row":
            text = " ".join(payload.get_text(" ", strip=True).split())
```

with:

```python
    tables: list[TableInfo] = []
    cells: list[Cell] = []
    # Rows of the table being collected. A table is parsed once all its rows
    # are known, because a header band can only be read top to bottom.
    pending_rows: list[tuple[int, str, object]] = []
    pending_table: int | None = None
    pending_caption: str | None = None
    last_prose: Sentence | None = None

    def flush_table() -> None:
        nonlocal pending_rows, pending_table
        if pending_table is not None and pending_rows:
            info, table_cells = parse_table(pending_table, pending_rows, pending_caption)
            tables.append(info)
            cells.extend(table_cells)
        pending_rows, pending_table = [], None

    body = soup.body if soup.body is not None else soup
    for kind, payload, table_id in list(_iter_units(body, count(1))):
        if kind == "row":
            if table_id != pending_table:
                flush_table()
                pending_table = table_id
                # Spec §4.3 rule 8: the nearest preceding prose sentence in the
                # same section, with no other table in between -- hence the reset.
                pending_caption = (
                    last_prose.text
                    if last_prose is not None and last_prose.section == tracker.current
                    else None
                )
                last_prose = None
            text = " ".join(payload.get_text(" ", strip=True).split())
```

Replace:

```python
            payload["data-sid"] = str(sid)
            continue

        nodes = [payload] if kind == "block" else payload
```

with:

```python
            payload["data-sid"] = str(sid)
            pending_rows.append((sid, text, payload))
            continue

        flush_table()
        nodes = [payload] if kind == "block" else payload
```

Replace:

```python
            sentences.extend(block_sentences)

    canonical_text = "\n".join(s.text for s in sentences)
    viewer_html = "".join(str(child) for child in body.children)
    return CanonicalFiling(canonical_text, sentences, viewer_html)
```

with:

```python
            sentences.extend(block_sentences)
            last_prose = block_sentences[-1]

    flush_table()
    canonical_text = "\n".join(s.text for s in sentences)
    viewer_html = "".join(str(child) for child in body.children)
    return CanonicalFiling(canonical_text, sentences, viewer_html, tables, cells)
```

- [ ] **Step 6: Run the whole canonicalizer suite**

Run: `pytest tests/test_canonical_snapshot.py tests/test_canonicalize_cells.py tests/test_canonicalize.py tests/test_canonicalize_tables.py tests/test_canonicalize_styles.py -v`
Expected: all PASS — in particular the snapshot test, unchanged since Step 2.

- [ ] **Step 7: Lint and commit**

```bash
ruff check .
git add src/pipeline/canonicalize.py tests/test_canonicalize_cells.py
git commit -m "feat: canonicalizer emits column-bound cells in the same traversal"
```

---

### Task 8: Migration 004, cell persistence, and the lexical expression

**Files:**
- Create: `backend/migrations/004_table_cells.sql`
- Modify: `backend/src/pipeline/store.py`
- Modify: `backend/src/api/retrieval.py` (only `_TSVECTOR`)
- Test: `backend/tests/test_store_cells.py`, `backend/tests/test_retrieval_expression.py`

**Interfaces:**
- Consumes: `TableInfo`, `Cell`; `CanonicalFiling.tables/.cells` (Task 7).
- Produces: `store.replace_tables(conn, filing_id: int, tables: list[TableInfo], cells: list[Cell]) -> None` (caller owns the transaction); `store.load_tables(conn, filing_id: int) -> dict[int, TableInfo]`; `store.load_cells(conn, filing_id: int, sid_start: int | None = None, sid_end: int | None = None) -> list[Cell]` ordered by `(sid, col)`; `store_filing` and `delete_derived` handle tables; columns `chunks.context text NOT NULL DEFAULT ''`, `chunks.table_id integer`.

- [ ] **Step 1: Write the failing tests.**

`backend/tests/test_retrieval_expression.py`:

```python
from pathlib import Path

from api.retrieval import _TSVECTOR

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def test_lexical_expression_matches_the_index_exactly():
    """A mismatch does not error -- the planner silently falls back to a
    sequential scan over every chunk. So the spelling is pinned."""
    sql = (MIGRATIONS / "004_table_cells.sql").read_text(encoding="utf-8")
    assert "to_tsvector('english', context || ' ' || text)" in sql
    assert _TSVECTOR.replace("ch.", "") == "to_tsvector('english', context || ' ' || text)"
```

`backend/tests/test_store_cells.py`:

```python
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from pipeline import db, store
from pipeline.canonicalize import canonicalize
from pipeline.companies import Company
from pipeline.edgar import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
COMPANY = Company(999999401, "TSTX", "Cells Test Co")
REF = FilingRef(
    cik=999999401,
    accession="CELLS-TEST-0001",
    form_type="10-K",
    filing_date=date(2025, 2, 26),
    period_end=date(2025, 1, 26),
    primary_document="x.html",
)


@pytest.fixture()
def conn():
    connection = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    db.migrate(connection)
    yield connection
    with connection.cursor() as cur:
        cur.execute(
            "DELETE FROM chunks WHERE filing_id IN (SELECT id FROM filings WHERE accession = %s)",
            (REF.accession,),
        )
        cur.execute(
            "DELETE FROM sentences WHERE filing_id IN"
            " (SELECT id FROM filings WHERE accession = %s)",
            (REF.accession,),
        )
        cur.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
    connection.commit()
    connection.close()


def nvda():
    return canonicalize((FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8"), "10-K")


@pytest.mark.db
def test_tables_and_cells_survive_the_round_trip(conn):
    canonical = nvda()
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    conn.commit()
    assert store.load_tables(conn, filing_id) == {t.table_id: t for t in canonical.tables}
    assert store.load_cells(conn, filing_id) == sorted(
        canonical.cells, key=lambda c: (c.sid, c.col)
    )


@pytest.mark.db
def test_load_cells_can_be_limited_to_a_sid_range(conn):
    canonical = nvda()
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    conn.commit()
    target = next(c for c in canonical.cells if c.raw == "115,186")
    cells = store.load_cells(conn, filing_id, target.sid, target.sid)
    assert {c.sid for c in cells} == {target.sid}
    assert next(c for c in cells if c.col == 4).value == Decimal("115186")


@pytest.mark.db
def test_replacing_a_filing_cascades_its_tables_away(conn):
    canonical = nvda()
    store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    conn.commit()
    assert len(store.load_cells(conn, filing_id)) == len(canonical.cells)


@pytest.mark.db
def test_delete_derived_clears_tables_and_cells(conn):
    filing_id = store.store_filing(conn, COMPANY, REF, nvda(), replace=True)
    store.delete_derived(conn, filing_id)
    conn.commit()
    assert store.load_tables(conn, filing_id) == {}
    assert store.load_cells(conn, filing_id) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_retrieval_expression.py tests/test_store_cells.py -v`
Expected: FAIL — missing migration file / `AttributeError: module 'pipeline.store' has no attribute 'load_tables'` (db tests need `TEST_DATABASE_URL`; see Global Constraints about the sandbox).

- [ ] **Step 3: Write the migration** — `backend/migrations/004_table_cells.sql`:

```sql
-- Column binding (spec 2026-09-29 §4.2). ON DELETE CASCADE so every existing
-- "delete the filing" path -- store_filing(replace=True), test cleanups --
-- also clears its tables and cells without having to know they exist.
CREATE TABLE filing_tables (
  filing_id  bigint  NOT NULL REFERENCES filings (id) ON DELETE CASCADE,
  table_id   integer NOT NULL,
  caption    text,
  scale      numeric,
  splittable boolean NOT NULL,
  PRIMARY KEY (filing_id, table_id)
);

CREATE TABLE table_cells (
  filing_id     bigint  NOT NULL,
  sid           integer NOT NULL,
  col           integer NOT NULL,
  cell_index    integer NOT NULL,
  char_start    integer,
  char_end      integer,
  table_id      integer NOT NULL,
  raw           text    NOT NULL,
  value         numeric,
  kind          text    NOT NULL CHECK (kind IN ('number', 'percent', 'nil')),
  row_label     text,
  column_label  text,
  scale_applies boolean NOT NULL,
  PRIMARY KEY (filing_id, sid, col),
  FOREIGN KEY (filing_id, table_id)
    REFERENCES filing_tables (filing_id, table_id) ON DELETE CASCADE
);

-- Spec §5: a chunk's context is embedded, indexed and shown to the model but
-- never verified against. table_id is set only on pieces of a split table.
ALTER TABLE chunks ADD COLUMN context text NOT NULL DEFAULT '';
ALTER TABLE chunks ADD COLUMN table_id integer;

-- The lexical arm searches context too. Must stay spelled exactly like
-- api.retrieval._TSVECTOR (minus the ch. alias) or the planner falls back to
-- a sequential scan.
DROP INDEX chunks_text_fts;
CREATE INDEX chunks_text_fts ON chunks USING gin (to_tsvector('english', context || ' ' || text));
```

- [ ] **Step 4: Update the lexical expression** in `backend/src/api/retrieval.py`:

```python
# Must stay spelled exactly like the chunks_text_fts expression index
# (migration 004) or the lexical arm falls back to a sequential scan.
_TSVECTOR = "to_tsvector('english', ch.context || ' ' || ch.text)"
```

- [ ] **Step 5: Implement persistence** in `backend/src/pipeline/store.py`.

Add to the imports:

```python
from .tables import Cell, TableInfo
```

Add these functions below `replace_sentences`:

```python
def replace_tables(
    conn: psycopg.Connection, filing_id: int, tables: list[TableInfo], cells: list[Cell]
) -> None:
    """Rewrite a filing's column-binding rows. The caller owns the transaction.
    Deleting filing_tables cascades to table_cells."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM filing_tables WHERE filing_id = %s", (filing_id,))
        if tables:
            cur.executemany(
                "INSERT INTO filing_tables (filing_id, table_id, caption, scale, splittable)"
                " VALUES (%s, %s, %s, %s, %s)",
                [(filing_id, t.table_id, t.caption, t.scale, t.splittable) for t in tables],
            )
        if cells:
            with cur.copy(
                "COPY table_cells (filing_id, sid, col, cell_index, char_start, char_end,"
                " table_id, raw, value, kind, row_label, column_label, scale_applies)"
                " FROM STDIN"
            ) as copy:
                for c in cells:
                    copy.write_row(
                        (
                            filing_id, c.sid, c.col, c.cell_index, c.char_start, c.char_end,
                            c.table_id, c.raw, c.value, c.kind, c.row_label, c.column_label,
                            c.scale_applies,
                        )
                    )


def load_tables(conn: psycopg.Connection, filing_id: int) -> dict[int, TableInfo]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_id, caption, scale, splittable FROM filing_tables"
            " WHERE filing_id = %s ORDER BY table_id",
            (filing_id,),
        )
        return {row[0]: TableInfo(*row) for row in cur.fetchall()}


def load_cells(
    conn: psycopg.Connection,
    filing_id: int,
    sid_start: int | None = None,
    sid_end: int | None = None,
) -> list[Cell]:
    sql = (
        "SELECT sid, col, cell_index, char_start, char_end, table_id, raw, value, kind,"
        " row_label, column_label, scale_applies FROM table_cells WHERE filing_id = %s"
    )
    params: list[object] = [filing_id]
    if sid_start is not None and sid_end is not None:
        sql += " AND sid BETWEEN %s AND %s"
        params += [sid_start, sid_end]
    sql += " ORDER BY sid, col"
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [Cell(*row) for row in cur.fetchall()]
```

In `store_filing`, after the `with cur.copy(... sentences ...)` block and still inside the `with conn.transaction(), conn.cursor() as cur:` block, add:

```python
        replace_tables(conn, filing_id, canonical.tables, canonical.cells)
```

In `delete_derived`, add as the first statement inside the cursor block:

```python
        cur.execute("DELETE FROM filing_tables WHERE filing_id = %s", (filing_id,))
```

and update its docstring to `"""Drop a filing's chunks, sentences and tables, keeping the filings row itself."""`.

- [ ] **Step 6: Keep `reprocess` writing tables.** In `backend/src/pipeline/ingest.py` `reprocess_filings`, after `store.replace_sentences(conn, filing_id, canonical.sentences)` add:

```python
            store.replace_tables(conn, filing_id, canonical.tables, canonical.cells)
```

- [ ] **Step 7: Migrate the test DB and run the tests**

```bash
DATABASE_URL=$TEST_DATABASE_URL python -m pipeline migrate
pytest tests/test_retrieval_expression.py tests/test_store_cells.py tests/test_store.py tests/test_store_replace.py tests/test_reprocess.py tests/test_retrieval.py -v
```

Expected: all PASS.

- [ ] **Step 8: Lint and commit**

```bash
ruff check .
git add migrations/004_table_cells.sql src/pipeline/store.py src/pipeline/ingest.py src/api/retrieval.py tests/test_store_cells.py tests/test_retrieval_expression.py
git commit -m "feat: persist table cells; index chunk context for lexical search"
```

---

### Task 9: `retable` command

**Files:**
- Modify: `backend/src/pipeline/ingest.py`
- Modify: `backend/src/pipeline/__main__.py`
- Test: `backend/tests/test_retable.py`

**Interfaces:**
- Consumes: `store.replace_tables`, `store.load_tables`, `store.load_cells` (Task 8).
- Produces: `ingest.RetableStats(updated: int, missing: int, mismatched: list[str])`; `ingest.retable_filings(conn, *, cache_dir: Path, ticker: str | None = None) -> RetableStats`; CLI `python -m pipeline retable [--ticker T]`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_retable.py`:

```python
import os
from datetime import date
from pathlib import Path

import psycopg
import pytest

from pipeline import db, ingest, store
from pipeline.canonicalize import CanonicalFiling, Sentence, canonicalize
from pipeline.companies import Company
from pipeline.edgar import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
COMPANY = Company(999999402, "TSTY", "Retable Test Co")
REF = FilingRef(
    cik=999999402,
    accession="RETABLE-TEST-0001",
    form_type="10-K",
    filing_date=date(2025, 2, 26),
    period_end=date(2025, 1, 26),
    primary_document="x.html",
)


@pytest.fixture()
def conn():
    connection = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    db.migrate(connection)
    yield connection
    with connection.cursor() as cur:
        cur.execute(
            "DELETE FROM sentences WHERE filing_id IN"
            " (SELECT id FROM filings WHERE accession = %s)",
            (REF.accession,),
        )
        cur.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
    connection.commit()
    connection.close()


def cache(tmp_path: Path) -> str:
    raw = (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8")
    path = tmp_path / str(REF.cik) / f"{REF.accession}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return raw


@pytest.mark.db
def test_retable_writes_cells_for_a_filing_stored_without_them(conn, tmp_path):
    raw = cache(tmp_path)
    canonical = canonicalize(raw, REF.form_type)
    bare = CanonicalFiling(canonical.canonical_text, canonical.sentences, canonical.viewer_html)
    filing_id = store.store_filing(conn, COMPANY, REF, bare, replace=True)
    conn.commit()
    assert store.load_cells(conn, filing_id) == []

    stats = ingest.retable_filings(conn, cache_dir=tmp_path, ticker="TSTY")
    conn.commit()

    assert (stats.updated, stats.missing, stats.mismatched) == (1, 0, [])
    assert len(store.load_cells(conn, filing_id)) == len(canonical.cells)


@pytest.mark.db
def test_retable_refuses_a_filing_whose_sentences_moved(conn, tmp_path):
    cache(tmp_path)
    stale = CanonicalFiling("x", [Sentence(0, "item7", "Different text.", 0, 15)], "<p>x</p>")
    filing_id = store.store_filing(conn, COMPANY, REF, stale, replace=True)
    conn.commit()

    stats = ingest.retable_filings(conn, cache_dir=tmp_path, ticker="TSTY")
    conn.commit()

    assert stats.mismatched == [REF.accession]
    assert store.load_tables(conn, filing_id) == {}


@pytest.mark.db
def test_retable_counts_a_filing_missing_from_the_cache(conn, tmp_path):
    canonical = canonicalize(
        (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8"), REF.form_type
    )
    store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    conn.commit()
    stats = ingest.retable_filings(conn, cache_dir=tmp_path, ticker="TSTY")
    assert (stats.updated, stats.missing) == (0, 1)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_retable.py -v`
Expected: FAIL — `AttributeError: module 'pipeline.ingest' has no attribute 'retable_filings'`.

- [ ] **Step 3: Implement** — add to `backend/src/pipeline/ingest.py` after `recanonicalize_filings`:

```python
@dataclass
class RetableStats:
    updated: int = 0
    missing: int = 0
    mismatched: list[str] = field(default_factory=list)


def retable_filings(
    conn,
    *,
    cache_dir: Path,
    ticker: str | None = None,
) -> RetableStats:
    """Write filing_tables and table_cells from cached raw HTML. Writes nothing else.

    Column binding reads tables without touching the DOM, so sentences are
    invariant across it (spec 2026-09-29 §4.1). As with recanonicalize, that is
    verified per filing rather than assumed: a filing whose freshly computed
    sentences differ from the stored rows is left untouched and reported,
    because cells keyed to moved sids would point at the wrong rows.
    """
    stats = RetableStats()
    for filing_id, cik, accession, form_type in store.filings_to_recanonicalize(
        conn, ticker=ticker
    ):
        path = Path(cache_dir) / str(cik) / f"{accession}.html"
        if not path.exists():
            stats.missing += 1
            continue
        canonical = canonicalize(path.read_text(encoding="utf-8"), form_type)
        if canonical.sentences != store.load_sentences(conn, filing_id):
            stats.mismatched.append(accession)
            continue
        with conn.transaction():
            store.replace_tables(conn, filing_id, canonical.tables, canonical.cells)
        stats.updated += 1
    return stats
```

- [ ] **Step 4: Wire the CLI** in `backend/src/pipeline/__main__.py`. After the `p_recanon` argument lines add:

```python
    p_retable = sub.add_parser(
        "retable",
        help="write table cells from cached raw HTML (no re-embed; refuses if sentences moved)",
    )
    p_retable.add_argument("--ticker", help="restrict to one curated ticker")
```

and after the `recanonicalize` handler block add:

```python
    if args.cmd == "retable":
        with db.connect() as conn:
            stats = ingest.retable_filings(conn, cache_dir=Path("data/raw"), ticker=args.ticker)
        print(f"updated {stats.updated} filings, {stats.missing} missing from cache")
        if stats.mismatched:
            print(f"SENTENCE MISMATCH, left untouched: {', '.join(stats.mismatched)}")
        return
```

- [ ] **Step 5: Run tests, lint, commit**

Run: `pytest tests/test_retable.py tests/test_recanonicalize.py -v` → PASS.

```bash
ruff check .
git add src/pipeline/ingest.py src/pipeline/__main__.py tests/test_retable.py
git commit -m "feat: retable backfills table cells, refusing any filing whose sentences moved"
```

---

### Task 10: `table-report` coverage command

**Files:**
- Create: `backend/src/pipeline/report.py`
- Modify: `backend/src/pipeline/__main__.py`
- Test: `backend/tests/test_table_report.py`

**Interfaces:**
- Produces: `report.table_report(conn, *, ticker: str | None = None) -> dict` with keys `cells`, `labelled_share`, `tables`, `scaled_share`, `splittable_share`, `worst` (list of `(ticker, accession, cells, labelled_share)`, at most 10, lowest share first); CLI `python -m pipeline table-report [--ticker T]`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_table_report.py`:

```python
import os
from datetime import date
from pathlib import Path

import psycopg
import pytest

from pipeline import db, report, store
from pipeline.canonicalize import canonicalize
from pipeline.companies import Company
from pipeline.edgar import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
COMPANY = Company(999999403, "TSTZ", "Report Test Co")
REF = FilingRef(
    cik=999999403,
    accession="REPORT-TEST-0001",
    form_type="10-K",
    filing_date=date(2025, 2, 26),
    period_end=None,
    primary_document="x.html",
)


@pytest.mark.db
def test_table_report_summarises_one_tickers_cells_and_tables():
    canonical = canonicalize(
        (FIXTURES / "edgar_msft_segments.html").read_text(encoding="utf-8"), "10-K"
    )
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        db.migrate(conn)
        store.store_filing(conn, COMPANY, REF, canonical, replace=True)
        conn.commit()
        try:
            result = report.table_report(conn, ticker="TSTZ")
        finally:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM sentences WHERE filing_id IN"
                    " (SELECT id FROM filings WHERE accession = %s)",
                    (REF.accession,),
                )
                cur.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
            conn.commit()

    assert result["cells"] == len(canonical.cells) == 12
    assert result["labelled_share"] == 1.0
    assert (result["tables"], result["scaled_share"], result["splittable_share"]) == (1, 1.0, 1.0)
    assert result["worst"] == [("TSTZ", "REPORT-TEST-0001", 12, 1.0)]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_table_report.py -v`
Expected: FAIL — `ImportError: cannot import name 'report'`.

- [ ] **Step 3: Implement** — `backend/src/pipeline/report.py`:

```python
"""Column-binding coverage over stored tables (spec 2026-09-29 §6).

No pass/fail threshold: it measures binding beyond the fixtures and tells the
arithmetic spec how much of the corpus it can verify.
"""

from __future__ import annotations

_JOIN = " JOIN filings f ON f.id = {alias}.filing_id JOIN companies c ON c.cik = f.cik"


def _share(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def table_report(conn, *, ticker: str | None = None) -> dict:
    where = " WHERE c.ticker = %s" if ticker else ""
    params = [ticker.upper()] if ticker else []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*), count(tc.column_label) FROM table_cells tc"
            + _JOIN.format(alias="tc") + where,
            params,
        )
        cells, labelled = cur.fetchone()
        cur.execute(
            "SELECT count(*), count(ft.scale), count(*) FILTER (WHERE ft.splittable)"
            " FROM filing_tables ft" + _JOIN.format(alias="ft") + where,
            params,
        )
        tables, scaled, splittable = cur.fetchone()
        cur.execute(
            "SELECT c.ticker, f.accession, count(*), count(tc.column_label)"
            " FROM table_cells tc" + _JOIN.format(alias="tc") + where
            + " GROUP BY c.ticker, f.accession"
            " ORDER BY count(tc.column_label)::float / count(*), f.accession LIMIT 10",
            params,
        )
        worst = [
            (row_ticker, accession, n, _share(n_labelled, n))
            for row_ticker, accession, n, n_labelled in cur.fetchall()
        ]
    return {
        "cells": cells,
        "labelled_share": _share(labelled, cells),
        "tables": tables,
        "scaled_share": _share(scaled, tables),
        "splittable_share": _share(splittable, tables),
        "worst": worst,
    }
```

- [ ] **Step 4: Wire the CLI** in `backend/src/pipeline/__main__.py`. Change `from . import companies, db, ingest` to `from . import companies, db, ingest, report`. Add the parser after `p_retable`:

```python
    p_report = sub.add_parser("table-report", help="column-binding coverage over stored tables")
    p_report.add_argument("--ticker", help="restrict to one curated ticker")
```

and the handler after the `retable` handler:

```python
    if args.cmd == "table-report":
        with db.connect() as conn:
            result = report.table_report(conn, ticker=args.ticker)
        print(f"numeric cells:     {result['cells']}")
        print(f"  with a column:   {result['labelled_share']:.1%}")
        print(f"tables:            {result['tables']}")
        print(f"  with a scale:    {result['scaled_share']:.1%}")
        print(f"  splittable:      {result['splittable_share']:.1%}")
        print("lowest-labelled filings:")
        for row_ticker, accession, n, share in result["worst"]:
            print(f"  {row_ticker} {accession}: {share:.1%} of {n} cells")
        return
```

- [ ] **Step 5: Run tests, lint, commit**

Run: `pytest tests/test_table_report.py -v` → PASS.

```bash
ruff check .
git add src/pipeline/report.py src/pipeline/__main__.py tests/test_table_report.py
git commit -m "feat: table-report prints column-binding coverage"
```

---

### Task 11: Resolve the cited figure's cells in verification and the SSE event

**Files:**
- Modify: `backend/src/api/verify.py`
- Modify: `backend/src/api/queries.py`
- Modify: `backend/src/api/answer.py`
- Test: `backend/tests/test_verify_cells.py`, `backend/tests/test_answer_cells.py`

**Interfaces:**
- Consumes: `Cell` (Task 5); `store.load_cells` (Task 8).
- Produces: `verify.resolve_cells(spans: list[tuple[int, int, int]], start: int, end: int, cells: list[Cell]) -> list[tuple[int, int]]` returning `(sid, cell_index)`; `VerifiedCitation.cells: tuple[tuple[int, int], ...] = ()`; `verify_citation(citation, chunk, sentences, cells=())`; `queries.load_chunk_cells(conn, filing_id, sid_start, sid_end) -> list[Cell]`; SSE `citation` data gains `"cells": [{"sid": int, "cell": int}, ...]`.

- [ ] **Step 1: Write the failing unit tests** — `backend/tests/test_verify_cells.py`:

```python
from datetime import date
from pathlib import Path

from api.retrieval import RetrievedChunk
from api.verify import Citation, verify_citation
from pipeline.canonicalize import canonicalize

FIXTURES = Path(__file__).parent / "fixtures"
CANONICAL = canonicalize(
    (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8"), "10-K"
)
SENTENCES = CANONICAL.sentences
TEXT = " ".join(s.text for s in SENTENCES)
DATA_CENTER = next(s.sid for s in SENTENCES if s.text.startswith("Data Center"))


def chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=1, accession="A-1", form_type="10-K", filing_date=date(2025, 2, 26),
        ticker="NVDA", section="item7", sid_start=SENTENCES[0].sid,
        sid_end=SENTENCES[-1].sid, text=TEXT, filing_id=1, score=0.5,
    )


def verify(quote, cells=CANONICAL.cells):
    return verify_citation(Citation(1, 1, quote), chunk(), SENTENCES, cells)


def test_a_quote_ending_at_a_figure_resolves_that_cell():
    result = verify("Data Center $ 115,186")
    assert result.verified
    assert result.sids == [DATA_CENTER]
    assert result.cells == ((DATA_CENTER, 2),)


def test_a_quote_spanning_two_figures_resolves_both():
    result = verify("Data Center $ 115,186 $ 47,525")
    assert result.cells == ((DATA_CENTER, 2), (DATA_CENTER, 6))


def test_a_quote_of_only_the_row_label_resolves_the_row_and_no_cells():
    result = verify("Data Center")
    assert result.sids == [DATA_CENTER]
    assert result.cells == ()


def test_a_prose_quote_resolves_no_cells():
    result = verify("The following table summarizes revenue by specialized markets:")
    assert result.verified
    assert result.cells == ()


def test_cells_without_spans_fall_back_to_the_row():
    from dataclasses import replace

    spanless = [replace(c, char_start=None, char_end=None) for c in CANONICAL.cells]
    result = verify("Data Center $ 115,186", spanless)
    assert result.sids == [DATA_CENTER]
    assert result.cells == ()


def test_an_unverified_quote_resolves_no_cells():
    result = verify("Data Center $ 999,999")
    assert not result.verified
    assert result.cells == ()
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_verify_cells.py -v`
Expected: FAIL — `TypeError: verify_citation() takes 3 positional arguments but 4 were given`.

- [ ] **Step 3: Implement** in `backend/src/api/verify.py`.

Add to the imports:

```python
from pipeline.tables import Cell
```

Add a field at the end of `VerifiedCitation`:

```python
    # (sid, cell_index) of each table figure the quote covers (spec 2026-09-29
    # §5.7). cell_index is the browser's tr.cells[i] in the stored viewer HTML.
    cells: tuple[tuple[int, int], ...] = ()
```

Add after `resolve_sids`:

```python
def resolve_cells(
    spans: list[tuple[int, int, int]], start: int, end: int, cells: list[Cell]
) -> list[tuple[int, int]]:
    """(sid, cell_index) for every stored figure the match [start, end) covers.

    Cell spans are relative to their row sentence; `spans` places each row
    sentence in chunk-text coordinates, so the sum lines them up. A cell
    without a span (its row did not reproduce cell by cell) is skipped -- the
    row itself still resolves through its sid.
    """
    row_start = {sid: span_start for sid, span_start, _ in spans}
    hits: list[tuple[int, int]] = []
    for cell in cells:
        if cell.char_start is None or cell.char_end is None or cell.sid not in row_start:
            continue
        cell_start = row_start[cell.sid] + cell.char_start
        cell_end = row_start[cell.sid] + cell.char_end
        if cell_start < end and start < cell_end:
            hits.append((cell.sid, cell.cell_index))
    return hits
```

Replace `verify_citation` with:

```python
def verify_citation(
    citation: Citation,
    chunk: RetrievedChunk,
    sentences: list[Sentence],
    cells: list[Cell] | tuple[Cell, ...] = (),
) -> VerifiedCitation:
    match = find_quote(chunk.text, citation.quote)
    spans = sentence_spans(sentences)
    sids = resolve_sids(spans, *match) if match else []
    figures = resolve_cells(spans, *match, list(cells)) if match and sids else []
    return VerifiedCitation(
        marker=citation.marker,
        chunk_id=citation.chunk_id,
        quote=citation.quote,
        verified=bool(sids),
        accession=chunk.accession,
        sids=sids,
        ticker=chunk.ticker,
        form_type=chunk.form_type,
        filing_date=chunk.filing_date.isoformat(),
        cells=tuple(figures),
    )
```

- [ ] **Step 4: Run the unit tests**

Run: `pytest tests/test_verify_cells.py tests/test_verify.py -v` → all PASS.

- [ ] **Step 5: Write the failing end-to-end test** — `backend/tests/test_answer_cells.py` (the file contains a literal triple-backtick fence inside a string, so this block is fenced with four backticks):

````python
import os
from datetime import date
from pathlib import Path

import psycopg
import pytest
from tests.fakes import FakeEmbedder, StubCompanyDetector, StubGenerator

from api.answer import answer_stream
from pipeline import db, store
from pipeline.canonicalize import canonicalize
from pipeline.chunk import chunk_sentences
from pipeline.companies import Company
from pipeline.edgar import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
COMPANY = Company(999999404, "TSTW", "Answer Cells Co")
REF = FilingRef(
    cik=999999404,
    accession="ANSWERCELLS-0001",
    form_type="10-K",
    filing_date=date(2025, 2, 26),
    period_end=date(2025, 1, 26),
    primary_document="x.html",
)


@pytest.fixture()
def seeded():
    conn = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    db.migrate(conn)
    canonical = canonicalize(
        (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8"), "10-K"
    )
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    chunks = chunk_sentences(canonical.sentences)
    store.store_chunks(
        conn, filing_id, chunks, FakeEmbedder().embed_texts([c.text for c in chunks])
    )
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM chunks WHERE filing_id = %s", (filing_id,))
        chunk_id = cur.fetchone()[0]
    data_center = next(s.sid for s in canonical.sentences if s.text.startswith("Data Center"))
    yield conn, chunk_id, data_center
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE filing_id = %s", (filing_id,))
        cur.execute("DELETE FROM sentences WHERE filing_id = %s", (filing_id,))
        cur.execute("DELETE FROM filings WHERE id = %s", (filing_id,))
    conn.commit()
    conn.close()


@pytest.mark.db
def test_a_table_citation_carries_its_cited_cells(seeded):
    conn, chunk_id, data_center = seeded
    response = (
        "Data Center revenue was $115,186 million [1].\n\n"
        '```json\n{"citations": [{"marker": 1, "chunk_id": CHUNK, '
        '"quote": "Data Center $ 115,186"}]}\n```'
    ).replace("CHUNK", str(chunk_id))
    events = list(
        answer_stream(
            conn, FakeEmbedder(), StubGenerator(response), StubCompanyDetector(),
            "What was Data Center revenue?", tickers=["TSTW"],
        )
    )
    citation = next(e for e in events if e.name == "citation")
    assert citation.data["verified"] is True
    assert citation.data["cells"] == [{"sid": data_center, "cell": 2}]
````

- [ ] **Step 6: Run to verify it fails**

Run: `pytest tests/test_answer_cells.py -v`
Expected: FAIL — `KeyError: 'cells'`.

- [ ] **Step 7: Implement.** In `backend/src/api/queries.py` add:

```python
from pipeline.store import load_cells
from pipeline.tables import Cell


def load_chunk_cells(
    conn: psycopg.Connection, filing_id: int, sid_start: int, sid_end: int
) -> list[Cell]:
    """The table cells a chunk's rows hold, in (sid, col) order."""
    return load_cells(conn, filing_id, sid_start, sid_end)
```

(Place the two imports with the existing `from pipeline.canonicalize import Sentence` import, sorted.)

In `backend/src/api/answer.py`, replace:

```python
            sentences = queries.load_chunk_sentences(
                conn, chunk.filing_id, chunk.sid_start, chunk.sid_end
            )
            verified.append(verify_citation(citation, chunk, sentences))
```

with:

```python
            sentences = queries.load_chunk_sentences(
                conn, chunk.filing_id, chunk.sid_start, chunk.sid_end
            )
            cells = queries.load_chunk_cells(
                conn, chunk.filing_id, chunk.sid_start, chunk.sid_end
            )
            verified.append(verify_citation(citation, chunk, sentences, cells))
```

and in the `citation` event payload, add after `"quote": citation.quote,`:

```python
                    "cells": [{"sid": sid, "cell": cell} for sid, cell in citation.cells],
```

- [ ] **Step 8: Run tests, lint, commit**

Run: `pytest tests/test_answer_cells.py tests/test_answer.py tests/test_verify.py tests/test_verify_cells.py tests/test_app.py -v` → all PASS.

```bash
ruff check .
git add src/api/verify.py src/api/queries.py src/api/answer.py tests/test_verify_cells.py tests/test_answer_cells.py
git commit -m "feat: citations name the table cells their quote covers"
```

---

### Task 12: Frontend — emphasize the cited figure

**Files:**
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/answer.ts`
- Modify: `frontend/lib/highlight.ts`
- Modify: `frontend/components/filing-viewer.tsx`
- Modify: `frontend/app/ask/page.tsx`
- Modify: `frontend/app/globals.css`
- Test: `frontend/lib/__tests__/highlight.test.ts`, `frontend/lib/__tests__/answer.test.ts`, `frontend/e2e/highlight.spec.ts`

**Interfaces:**
- Consumes: SSE `citation.cells` (Task 11).
- Produces: `type CitedCell = { sid: number; cell: number }`; `Citation.cells: CitedCell[]`; `FIGURE_CLASS = "cited-figure"`; `type Highlight = { sids: number[]; cells: CitedCell[] }`; `NO_HIGHLIGHT`; `applyHighlight(container, sids, cells = [])`; `FilingViewer` prop `highlights: Record<string, Highlight>`.

- [ ] **Step 1: Write the failing unit tests.** Append to `frontend/lib/__tests__/highlight.test.ts` (and change its import to `import { FIGURE_CLASS, HIGHLIGHT_CLASS, applyHighlight } from "../highlight";`):

```ts
function tableContainer(): HTMLElement {
  const table = document.createElement("div");
  table.innerHTML = `
    <table><tbody>
      <tr data-sid="20"><td>Data Center</td><td>$</td><td>115,186</td><td>$</td><td>47,525</td></tr>
      <tr data-sid="21"><td>Compute</td><td>102,196</td><td>38,950</td></tr>
    </tbody></table>`;
  return table;
}

test("a cited cell gets the figure class inside the highlighted row", () => {
  const table = tableContainer();
  applyHighlight(table, [20], [{ sid: 20, cell: 2 }]);
  const cells = table.querySelectorAll('tr[data-sid="20"] td');
  expect(cells[2].classList.contains(FIGURE_CLASS)).toBe(true);
  expect(cells[4].classList.contains(FIGURE_CLASS)).toBe(false);
  expect(table.querySelector('tr[data-sid="20"]')?.classList.contains(HIGHLIGHT_CLASS)).toBe(true);
});

test("the cited figure is what scrolls into view", () => {
  const table = tableContainer();
  applyHighlight(table, [20], [{ sid: 20, cell: 2 }]);
  const figure = table.querySelectorAll('tr[data-sid="20"] td')[2];
  expect(figure.scrollIntoView).toHaveBeenCalledTimes(1);
});

test("the next highlight clears the previous figure", () => {
  const table = tableContainer();
  applyHighlight(table, [20], [{ sid: 20, cell: 2 }]);
  applyHighlight(table, [21], []);
  expect(table.querySelectorAll(`.${FIGURE_CLASS}`)).toHaveLength(0);
});

test("a cell index past the row's end is ignored", () => {
  const table = tableContainer();
  expect(() => applyHighlight(table, [21], [{ sid: 21, cell: 9 }])).not.toThrow();
  expect(table.querySelectorAll(`.${FIGURE_CLASS}`)).toHaveLength(0);
});
```

Append to `frontend/lib/__tests__/answer.test.ts`:

```ts
test("a citation event without cells gets an empty cell list", () => {
  const state = feed([
    ["citation", { marker: 1, verified: true, accession: "A", ticker: "AAPL", form_type: "10-K", filing_date: "2024-11-01", sids: [7], quote: "q" }],
  ]);
  expect(state.citations.get(1)?.cells).toEqual([]);
});

test("a citation event keeps its cited cells", () => {
  const state = feed([
    ["citation", { marker: 1, verified: true, accession: "A", ticker: "AAPL", form_type: "10-K", filing_date: "2024-11-01", sids: [7], quote: "q", cells: [{ sid: 7, cell: 2 }] }],
  ]);
  expect(state.citations.get(1)?.cells).toEqual([{ sid: 7, cell: 2 }]);
});
```

- [ ] **Step 2: Run to verify they fail** (PowerShell, from `frontend/`)

Run: `npm test`
Expected: FAIL — `FIGURE_CLASS` is not exported; `cells` undefined.

- [ ] **Step 3: Implement the types and reducer.**

`frontend/lib/types.ts` — add above `Citation`, and add the field:

```ts
/** A table cell a citation's quote covers: the row's sid and the cell's index
 *  in that row (the browser's `tr.cells[i]`). Design §6.4. */
export type CitedCell = { sid: number; cell: number };
```

and inside `Citation`, after `quote: string;`:

```ts
  /** Figures the quote covers inside a table row; empty for prose. */
  cells: CitedCell[];
```

`frontend/lib/answer.ts` — in the `citation` case, replace `const citation = event.data as Citation;` with:

```ts
      const raw = event.data as Citation;
      // Older servers and hand-written fixtures omit cells; normalize once here
      // so every consumer can rely on an array.
      const citation: Citation = { ...raw, cells: raw.cells ?? [] };
```

- [ ] **Step 4: Implement the highlighter** — replace `frontend/lib/highlight.ts` with:

```ts
import type { CitedCell } from "./types";

/** Plain global classes, defined in app/globals.css. They cannot be Tailwind
 *  utilities: these elements come from dangerouslySetInnerHTML, so Tailwind
 *  never sees them at build time. */
export const HIGHLIGHT_CLASS = "cited-sentence";
export const FIGURE_CLASS = "cited-figure";

/** What one filing pane should highlight. */
export type Highlight = { sids: number[]; cells: CitedCell[] };

export const NO_HIGHLIGHT: Highlight = { sids: [], cells: [] };

/**
 * Highlight the sentences a citation resolves to, inside already-mounted HTML,
 * and emphasize the table figures its quote covers (spec 2026-09-29 §5.7).
 *
 * Operates on the live container rather than rewriting the HTML string,
 * because a real 10-K's viewer_html is ~818 KB -- re-parsing that on every
 * citation click would be visibly slow, and regex-over-HTML is fragile.
 * Injection happens once per filing; this runs on every highlight change.
 */
export function applyHighlight(
  container: HTMLElement,
  sids: number[],
  cells: CitedCell[] = [],
): void {
  container
    .querySelectorAll(`.${HIGHLIGHT_CLASS}, .${FIGURE_CLASS}`)
    .forEach((el) => el.classList.remove(HIGHLIGHT_CLASS, FIGURE_CLASS));

  // Collected into arrays rather than tracked in a `let` that a callback
  // assigns: TypeScript narrows such a variable to `null` at the use site and
  // reports `scrollIntoView` on type `never`.
  const cited: Element[] = [];
  for (const sid of sids) {
    container.querySelectorAll(`[data-sid="${sid}"]`).forEach((el) => {
      el.classList.add(HIGHLIGHT_CLASS);
      cited.push(el);
    });
  }
  const figures: Element[] = [];
  for (const { sid, cell } of cells) {
    const row = container.querySelector(`tr[data-sid="${sid}"]`);
    const target = row instanceof HTMLTableRowElement ? row.cells[cell] : undefined;
    if (target !== undefined) {
      target.classList.add(FIGURE_CLASS);
      figures.push(target);
    }
  }
  (figures[0] ?? cited[0])?.scrollIntoView({ behavior: "smooth", block: "center" });
}
```

- [ ] **Step 5: Thread highlights through the page and viewer.**

`frontend/components/filing-viewer.tsx`:
- Change `import { applyHighlight } from "@/lib/highlight";` to `import { NO_HIGHLIGHT, applyHighlight } from "@/lib/highlight";` and add `import type { Highlight } from "@/lib/highlight";`.
- In `FilingPane`, replace the `sids` prop with `highlight`:

```tsx
function FilingPane({
  accession,
  highlight,
  active,
}: {
  accession: string;
  highlight: Highlight;
  active: boolean;
}) {
```

- Replace the highlight effect with:

```tsx
  useEffect(() => {
    if (active && filing !== null && containerRef.current !== null) {
      applyHighlight(containerRef.current, highlight.sids, highlight.cells);
    }
  }, [active, filing, highlight]);
```

- In `FilingViewer`, replace the `sids` prop with `highlights: Record<string, Highlight>` and pass `highlight={highlights[accession] ?? NO_HIGHLIGHT}` to each `FilingPane`.

`frontend/app/ask/page.tsx`:
- Add `import type { Highlight } from "@/lib/highlight";` with the other `@/lib` imports.
- Replace `const [sids, setSids] = useState<Record<string, number[]>>({});` with `const [highlights, setHighlights] = useState<Record<string, Highlight>>({});`.
- In `newConversation`, replace `setSids({});` with `setHighlights({});`.
- In `select`, replace the `setSids(...)` line with:

```tsx
    setHighlights((previous) => ({
      ...previous,
      [citation.accession]: { sids: citation.sids, cells: citation.cells },
    }));
```

- Replace `<FilingViewer tabs={tabs} sids={sids} />` with `<FilingViewer tabs={tabs} highlights={highlights} />`.

`frontend/app/globals.css` — append after the `tr.cited-sentence td, tr.cited-sentence th` rule:

```css
/* The figure a table citation's quote covers (spec 2026-09-29 §5.7): a
   stronger treatment inside the already-highlighted row. Outline, not
   box-shadow, for the same cell-border reason as the row rule above. */
tr.cited-sentence td.cited-figure,
tr.cited-sentence th.cited-figure {
  background-color: #b45309;
  color: #ffffff;
  font-weight: 700;
  outline: 2px solid #fbbf24;
  outline-offset: -2px;
}
```

- [ ] **Step 6: Add the e2e case** — append to `frontend/e2e/highlight.spec.ts`:

```ts
test("a cited table figure stands out inside its row", async ({ page }) => {
  const tableHtml = `
    <table><tbody>
      <tr data-sid="30"><td>Year Ended</td></tr>
      <tr data-sid="31"><td>Data Center</td><td>$</td><td>115,186</td><td>$</td><td>47,525</td></tr>
    </tbody></table>`;
  await page.route(`${API}/companies`, (route) =>
    route.fulfill({ contentType: "application/json", body: "[]" }),
  );
  await page.route(`${API}/ask`, (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body:
        'event: token\ndata: {"text":"Data Center revenue was $115,186 million [1]."}\n\n' +
        `event: citation\ndata: {"marker":1,"verified":true,"accession":"${ACCESSION}",` +
        '"ticker":"NVDA","form_type":"10-K","filing_date":"2025-02-26",' +
        '"sids":[31],"quote":"Data Center $ 115,186","cells":[{"sid":31,"cell":2}]}\n\n' +
        'event: done\ndata: {"chunks_retrieved":8,"citations_total":1,' +
        '"citations_verified":1,"unverified_answer":false}\n\n',
    }),
  );
  await page.route(`${API}/filings/${ACCESSION}`, (route) =>
    route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        accession: ACCESSION,
        viewer_html: tableHtml,
        ticker: "NVDA",
        form_type: "10-K",
        filing_date: "2025-02-26",
        period_end: "2025-01-26",
      }),
    }),
  );

  await page.goto("/ask");
  await page.getByLabel("Question").fill("What was Data Center revenue?");
  await page.getByRole("button", { name: "Ask" }).click();
  const chip = page.getByRole("button", { name: "[1]" });
  await expect(chip).toBeVisible();
  await chip.click();

  const cells = page.locator('tr[data-sid="31"] td');
  await expect(page.locator('tr[data-sid="31"]')).toHaveClass(/cited-sentence/);
  await expect(cells.nth(2)).toHaveClass(/cited-figure/);
  await expect(cells.nth(4)).not.toHaveClass(/cited-figure/);
});
```

- [ ] **Step 7: Run everything** (PowerShell, from `frontend/`)

```powershell
npm test
npm run lint
npm run test:e2e
```

Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/lib frontend/components/filing-viewer.tsx frontend/app/ask/page.tsx frontend/app/globals.css frontend/e2e/highlight.spec.ts
git commit -m "feat: emphasize the cited figure inside a highlighted table row"
```

---

### Task 13: Chunker — split over-budget tables into context-carrying pieces

**Files:**
- Modify: `backend/src/pipeline/chunk.py` (full replacement below)
- Test: `backend/tests/test_chunk_split.py`; existing `tests/test_chunk.py` and `tests/test_chunk_tables.py` must keep passing unchanged

**Interfaces:**
- Consumes: `TableInfo`, `Cell`, `row_contexts` (Tasks 5–6).
- Produces: `Chunk.context: str = ""`, `Chunk.table_id: int | None = None`; `chunk_sentences(sentences, *, max_tokens=MAX_TOKENS, tables: dict[int, TableInfo] | None = None, cells: list[Cell] | None = None) -> list[Chunk]`; `embed_input(chunk: Chunk) -> str`.

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_chunk_split.py`:

```python
from decimal import Decimal
from pathlib import Path

from pipeline.canonicalize import Sentence, canonicalize
from pipeline.chunk import MAX_TOKENS, chunk_sentences, count_tokens, embed_input
from pipeline.tables import Cell, TableInfo

FIXTURES = Path(__file__).parent / "fixtures"


def prose(sid, text="Some prose sentence about the business."):
    return Sentence(sid, "item7", text, 0, len(text), None)


def row(sid, text, table_id=1):
    return Sentence(sid, "item7", text, 0, len(text), table_id)


def data_cell(sid, table_id=1, label="2025"):
    return Cell(
        sid, 1, 1, None, None, table_id, "1", Decimal("1"), "number", "Revenue", label, True
    )


def table(rows_per_band=8, bands=2, table_id=1, start=1):
    """A header row then `rows_per_band` data rows, repeated `bands` times."""
    sentences, cells, sid = [], [], start
    for band in range(bands):
        sentences.append(row(sid, f"Three Months Ended period {band}", table_id))
        sid += 1
        for i in range(rows_per_band):
            sentences.append(
                row(sid, f"Line item {band}-{i} $ 12,345 $ 67,890 $ 11,121 $ 31,415", table_id)
            )
            cells.append(data_cell(sid, table_id, label=f"Three Months Ended period {band}"))
            sid += 1
    return sentences, cells


def covered(chunks):
    return [sid for c in chunks for sid in range(c.sid_start, c.sid_end + 1)]


def test_an_over_budget_splittable_table_is_split_into_tagged_pieces():
    rows, cells = table(rows_per_band=8, bands=2)
    sentences = [prose(0), *rows, prose(rows[-1].sid + 1)]
    tables = {1: TableInfo(1, "Segment results", Decimal("1E+6"), True)}
    chunks = chunk_sentences(sentences, max_tokens=150, tables=tables, cells=cells)
    pieces = [c for c in chunks if c.table_id == 1]
    assert len(pieces) >= 2
    assert covered(chunks) == [s.sid for s in sentences]
    for piece in pieces:
        assert piece.context.startswith("Table: Segment results | Scale: in millions")
        assert piece.token_count + count_tokens(piece.context) <= 150


def test_pieces_are_isolated_from_surrounding_prose():
    rows, cells = table(rows_per_band=8, bands=2)
    sentences = [prose(0), *rows, prose(rows[-1].sid + 1)]
    tables = {1: TableInfo(1, None, None, True)}
    chunks = chunk_sentences(sentences, max_tokens=150, tables=tables, cells=cells)
    assert (chunks[0].sid_start, chunks[0].sid_end, chunks[0].table_id) == (0, 0, None)
    assert (chunks[-1].sid_start, chunks[-1].table_id) == (rows[-1].sid + 1, None)


def test_a_piece_prefers_to_start_at_a_new_header_band():
    rows, cells = table(rows_per_band=3, bands=2)
    tables = {1: TableInfo(1, None, None, True)}
    budget = sum(count_tokens(s.text) for s in rows[:4]) + 30
    chunks = chunk_sentences(rows, max_tokens=budget, tables=tables, cells=cells)
    assert [(c.sid_start, c.sid_end) for c in chunks] == [(1, 4), (5, 8)]


def test_no_piece_ends_with_header_rows_stranded_from_their_data():
    rows, cells = table(rows_per_band=6, bands=3)
    data_sids = {c.sid for c in cells}
    tables = {1: TableInfo(1, None, None, True)}
    chunks = chunk_sentences(rows, max_tokens=90, tables=tables, cells=cells)
    for chunk in chunks:
        assert chunk.sid_end in data_sids


def test_an_over_budget_table_without_a_parsed_header_stays_whole():
    rows, cells = table(rows_per_band=8, bands=2)
    tables = {1: TableInfo(1, None, None, False)}
    chunks = chunk_sentences(rows, max_tokens=150, tables=tables, cells=cells)
    assert len(chunks) == 1
    assert chunks[0].table_id is None
    assert chunks[0].context == "Columns: Three Months Ended period 0"


def test_a_table_that_fits_joins_the_prose_around_it_and_carries_context():
    rows, cells = table(rows_per_band=2, bands=1)
    sentences = [prose(0), *rows]
    tables = {1: TableInfo(1, "Small table", None, True)}
    chunks = chunk_sentences(sentences, tables=tables, cells=cells)
    assert len(chunks) == 1
    assert chunks[0].table_id is None
    assert chunks[0].context == "Table: Small table | Columns: Three Months Ended period 0"


def test_a_chunk_with_two_tables_carries_one_context_line_per_table():
    first, first_cells = table(rows_per_band=1, bands=1, table_id=1, start=0)
    second, second_cells = table(rows_per_band=1, bands=1, table_id=2, start=2)
    tables = {1: TableInfo(1, "First", None, True), 2: TableInfo(2, "Second", None, True)}
    chunks = chunk_sentences(
        [*first, *second], tables=tables, cells=[*first_cells, *second_cells]
    )
    assert len(chunks) == 1
    assert chunks[0].context.splitlines() == [
        "Table: First | Columns: Three Months Ended period 0",
        "Table: Second | Columns: Three Months Ended period 0",
    ]


def test_prose_chunks_have_no_context_and_embed_their_text_alone():
    chunks = chunk_sentences([prose(0), prose(1)])
    assert chunks[0].context == ""
    assert embed_input(chunks[0]) == chunks[0].text


def test_embed_input_puts_context_first():
    rows, cells = table(rows_per_band=1, bands=1)
    tables = {1: TableInfo(1, "T", None, True)}
    chunk = chunk_sentences(rows, tables=tables, cells=cells)[0]
    assert embed_input(chunk) == f"{chunk.context}\n{chunk.text}"


def test_chunk_text_is_still_the_space_join_of_its_sentences():
    rows, cells = table(rows_per_band=8, bands=2)
    tables = {1: TableInfo(1, "T", None, True)}
    by_sid = {s.sid: s.text for s in rows}
    for chunk in chunk_sentences(rows, max_tokens=150, tables=tables, cells=cells):
        expected = " ".join(by_sid[sid] for sid in range(chunk.sid_start, chunk.sid_end + 1))
        assert chunk.text == expected


def test_real_nvidia_table_splits_with_its_header_in_the_first_piece():
    raw = (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8")
    canonical = canonicalize(raw, "10-K")
    tables = {t.table_id: t for t in canonical.tables}
    chunks = chunk_sentences(
        canonical.sentences, max_tokens=70, tables=tables, cells=canonical.cells
    )
    pieces = [c for c in chunks if c.table_id is not None]
    assert pieces[0].text.startswith("Year Ended Jan 26, 2025")
    assert "Data Center $ 115,186" in pieces[0].text
    assert all("Columns: Year Ended › [Jan 26, 2025" in p.context for p in pieces)
    assert MAX_TOKENS == 450
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_chunk_split.py -v`
Expected: FAIL — `cannot import name 'embed_input'`.

- [ ] **Step 3: Implement** — replace `backend/src/pipeline/chunk.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import tiktoken

from .canonicalize import Sentence
from .tables import Cell, TableInfo, row_contexts

# bge-small-en-v1.5 truncates input at 512 of its own tokens; 450 tiktoken
# tokens keeps chunks safely under that limit (amends design §4.3's ~600).
# For table chunks the budget covers context + text, since both are embedded.
MAX_TOKENS = 450


@dataclass(frozen=True)
class Chunk:
    section: str
    sid_start: int
    sid_end: int
    text: str
    token_count: int
    # Spec 2026-09-29 §5: embedded, lexically indexed and shown to the model,
    # but never part of `text`, so verification offsets are untouched.
    context: str = ""
    # Set only on the pieces of a split table; drives the retrieval cap.
    table_id: int | None = None


@lru_cache(maxsize=1)
def _encoding():
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


def embed_input(chunk: Chunk) -> str:
    """What the embedder sees: context first, so a split table's column
    meaning survives into the vector."""
    return f"{chunk.context}\n{chunk.text}" if chunk.context else chunk.text


def _context(rows: list[Sentence], contexts: dict[int, str]) -> str:
    """One line per table in the chunk, from that table's first data row."""
    lines: list[str] = []
    seen: set[int] = set()
    for s in rows:
        if s.table_id is None or s.table_id in seen or s.sid not in contexts:
            continue
        seen.add(s.table_id)
        if contexts[s.sid]:
            lines.append(contexts[s.sid])
    return "\n".join(lines)


def _make(rows: list[Sentence], contexts: dict[int, str], table_id: int | None = None) -> Chunk:
    text = " ".join(s.text for s in rows)
    return Chunk(
        section=rows[0].section,
        sid_start=rows[0].sid,
        sid_end=rows[-1].sid,
        text=text,
        token_count=count_tokens(text),
        context=_context(rows, contexts),
        table_id=table_id,
    )


def _split_table(
    rows: list[Sentence], contexts: dict[int, str], max_tokens: int
) -> list[Chunk]:
    """Spec §5.2: break at row boundaries only; prefer to start a piece where
    a new header or group block begins; never leave header rows stranded at
    the end of a piece, away from the data they label."""
    table_id = rows[0].table_id
    ctx_tokens = max(
        (count_tokens(contexts[s.sid]) for s in rows if s.sid in contexts), default=0
    )
    budget = max_tokens - ctx_tokens
    tokens = {s.sid: count_tokens(s.text) for s in rows}
    pieces: list[Chunk] = []
    piece: list[Sentence] = []

    def has_data(part: list[Sentence]) -> bool:
        return any(s.sid in contexts for s in part)

    def close() -> None:
        nonlocal piece
        carry: list[Sentence] = []
        while piece and piece[-1].sid not in contexts and has_data(piece[:-1]):
            carry.insert(0, piece.pop())
        if piece:
            pieces.append(_make(piece, contexts, table_id))
        piece = carry

    for k, current in enumerate(rows):
        is_data = current.sid in contexts
        used = sum(tokens[s.sid] for s in piece)
        block_starts = not is_data and bool(piece) and piece[-1].sid in contexts
        if block_starts:
            block: list[Sentence] = []
            for later in rows[k:]:
                if block and later.sid not in contexts and block[-1].sid in contexts:
                    break
                block.append(later)
            if used + sum(tokens[s.sid] for s in block) > budget:
                close()
        elif has_data(piece) and used + tokens[current.sid] > budget:
            close()
        piece.append(current)
    if piece:
        pieces.append(_make(piece, contexts, table_id))
    return pieces


def chunk_sentences(
    sentences: list[Sentence],
    *,
    max_tokens: int = MAX_TOKENS,
    tables: dict[int, TableInfo] | None = None,
    cells: list[Cell] | None = None,
) -> list[Chunk]:
    """Greedy grouping of consecutive sentences within a section (design §4.3).

    Chunks are contiguous, disjoint sid ranges. A table is costed as a whole,
    context included: one that fits joins the greedy run like any sentence;
    an over-budget one stays atomic unless its header was parsed
    (splittable), in which case it is isolated and split by _split_table.
    Without `tables`/`cells` every table is unsplittable and context-free,
    which is exactly the pre-2026-09-29 behaviour."""
    tables = tables or {}
    contexts = row_contexts(tables, cells or [])
    chunks: list[Chunk] = []
    current: list[Sentence] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if current:
            chunks.append(_make(current, contexts))
            current, current_tokens = [], 0

    i = 0
    while i < len(sentences):
        first = sentences[i]
        j = i + 1
        if first.table_id is not None:
            while j < len(sentences) and sentences[j].table_id == first.table_id:
                j += 1
        unit = sentences[i:j]
        cost = sum(count_tokens(s.text) for s in unit)
        ctx = _context(unit, contexts)
        if ctx:
            cost += count_tokens(ctx)
        info = tables.get(first.table_id) if first.table_id is not None else None
        if info is not None and info.splittable and cost > max_tokens:
            flush()
            chunks.extend(_split_table(unit, contexts, max_tokens))
            i = j
            continue
        new_section = current and first.section != current[0].section
        over_budget = current and current_tokens + cost > max_tokens
        if new_section or over_budget:
            flush()
        current.extend(unit)
        current_tokens += cost
        i = j
    flush()
    return chunks
```

- [ ] **Step 4: Run the chunker suites**

Run: `pytest tests/test_chunk_split.py tests/test_chunk.py tests/test_chunk_tables.py -v`
Expected: all PASS (the two existing files unchanged).

- [ ] **Step 5: Lint and commit**

```bash
ruff check .
git add src/pipeline/chunk.py tests/test_chunk_split.py
git commit -m "feat: split over-budget tables into context-carrying chunks"
```

---

### Task 14: Persist context; embed with it; `rechunk`

**Files:**
- Modify: `backend/src/pipeline/store.py` (`store_chunks`, new `delete_chunks`)
- Modify: `backend/src/pipeline/ingest.py` (`embed_filings`, `reprocess_filings`, new `rechunk_filings`)
- Modify: `backend/src/pipeline/__main__.py`
- Test: `backend/tests/test_rechunk.py`

**Interfaces:**
- Consumes: `chunk_sentences(..., tables=, cells=)`, `embed_input` (Task 13); `store.load_tables/load_cells/replace_tables` (Task 8).
- Produces: `store_chunks` writes `context` and `table_id`; `store.delete_chunks(conn, filing_id) -> None`; `ingest.rechunk_filings(conn, embedder, *, ticker: str | None = None) -> tuple[int, int]` (filings, chunks); CLI `python -m pipeline rechunk [--ticker T]`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_rechunk.py`:

```python
import os
from datetime import date
from pathlib import Path

import psycopg
import pytest
from tests.fakes import FakeEmbedder

from pipeline import db, ingest, store
from pipeline.canonicalize import canonicalize
from pipeline.chunk import chunk_sentences
from pipeline.companies import Company
from pipeline.edgar import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
COMPANY = Company(999999405, "TSTV", "Rechunk Test Co")
REF = FilingRef(
    cik=999999405,
    accession="RECHUNK-TEST-0001",
    form_type="10-K",
    filing_date=date(2025, 2, 26),
    period_end=date(2025, 1, 26),
    primary_document="x.html",
)


class RecordingEmbedder(FakeEmbedder):
    def __init__(self):
        self.inputs: list[str] = []

    def embed_texts(self, texts):
        self.inputs.extend(texts)
        return super().embed_texts(texts)


@pytest.fixture()
def conn():
    connection = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    db.migrate(connection)
    yield connection
    with connection.cursor() as cur:
        cur.execute(
            "DELETE FROM chunks WHERE filing_id IN (SELECT id FROM filings WHERE accession = %s)",
            (REF.accession,),
        )
        cur.execute(
            "DELETE FROM sentences WHERE filing_id IN"
            " (SELECT id FROM filings WHERE accession = %s)",
            (REF.accession,),
        )
        cur.execute("DELETE FROM filings WHERE accession = %s", (REF.accession,))
    connection.commit()
    connection.close()


@pytest.mark.db
def test_rechunk_rebuilds_chunks_with_context_and_embeds_it(conn):
    canonical = canonicalize(
        (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8"), "10-K"
    )
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    stale = chunk_sentences(canonical.sentences)  # the pre-change, context-free chunks
    store.store_chunks(conn, filing_id, stale, FakeEmbedder().embed_texts([c.text for c in stale]))
    conn.commit()

    embedder = RecordingEmbedder()
    filings, chunks = ingest.rechunk_filings(conn, embedder, ticker="TSTV")
    conn.commit()

    assert (filings, chunks) == (1, 1)
    with conn.cursor() as cur:
        cur.execute("SELECT context, text, table_id FROM chunks WHERE filing_id = %s", (filing_id,))
        rows = cur.fetchall()
    assert len(rows) == 1
    context, text, table_id = rows[0]
    assert context.startswith("Table: The following table summarizes revenue")
    assert "Columns: Year Ended › [Jan 26, 2025" in context
    assert table_id is None  # the fixture table fits, so it is not split
    assert embedder.inputs == [f"{context}\n{text}"]


@pytest.mark.db
def test_embed_filings_uses_stored_cells_for_context(conn):
    canonical = canonicalize(
        (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8"), "10-K"
    )
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    conn.commit()
    embedder = RecordingEmbedder()
    ingest.embed_filings(conn, embedder, ticker="TSTV")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT context FROM chunks WHERE filing_id = %s", (filing_id,))
        rows = cur.fetchall()
    assert len(rows) == 1
    context = rows[0][0]
    assert "Scale: in millions" in context
    assert embedder.inputs[0].startswith(context)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_rechunk.py -v`
Expected: FAIL — `AttributeError: module 'pipeline.ingest' has no attribute 'rechunk_filings'`.

- [ ] **Step 3: Implement storage** in `backend/src/pipeline/store.py`. Replace the `executemany` in `store_chunks` with:

```python
        cur.executemany(
            "INSERT INTO chunks (filing_id, section, sid_start, sid_end, text,"
            " token_count, embedding, context, table_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s, %s)",
            [
                (
                    filing_id,
                    chunk.section,
                    chunk.sid_start,
                    chunk.sid_end,
                    chunk.text,
                    chunk.token_count,
                    to_pgvector(vector),
                    chunk.context,
                    chunk.table_id,
                )
                # Backstops the length check above: without strict, a future
                # refactor that drops that guard would silently truncate.
                for chunk, vector in zip(chunks, vectors, strict=True)
            ],
        )
```

Add after `store_chunks`:

```python
def delete_chunks(conn: psycopg.Connection, filing_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE filing_id = %s", (filing_id,))
```

- [ ] **Step 4: Implement the embed paths** in `backend/src/pipeline/ingest.py`. Change `from .chunk import chunk_sentences` to `from .chunk import chunk_sentences, embed_input`.

Replace the body of the loop in `embed_filings` with:

```python
    for filing_id in store.filing_ids_without_chunks(conn, ticker=ticker):
        sentences = store.load_sentences(conn, filing_id)
        chunks = chunk_sentences(
            sentences,
            tables=store.load_tables(conn, filing_id),
            cells=store.load_cells(conn, filing_id),
        )
        vectors = embedder.embed_texts([embed_input(c) for c in chunks])
        chunks_stored += store.store_chunks(conn, filing_id, chunks, vectors)
        filings_done += 1
```

In `reprocess_filings`, replace:

```python
            chunks = chunk_sentences(canonical.sentences)
            vectors = embedder.embed_texts([c.text for c in chunks])
```

with:

```python
            chunks = chunk_sentences(
                canonical.sentences,
                tables={t.table_id: t for t in canonical.tables},
                cells=canonical.cells,
            )
            vectors = embedder.embed_texts([embed_input(c) for c in chunks])
```

Add after `embed_filings`:

```python
def rechunk_filings(conn, embedder, *, ticker: str | None = None) -> tuple[int, int]:
    """Rebuild every filing's chunks from stored sentences and cells, re-embedding.

    The explicit rebuild for a chunking change (spec 2026-09-29 §9): embed
    only touches filings with no chunks. Sentences are read, never written, so
    sids and the golden set's pins are untouched. Embedding happens before the
    per-filing transaction so a slow model never holds a lock.
    """
    filings_done = 0
    chunks_stored = 0
    for filing_id, _cik, _accession, _form in store.filings_to_recanonicalize(
        conn, ticker=ticker
    ):
        chunks = chunk_sentences(
            store.load_sentences(conn, filing_id),
            tables=store.load_tables(conn, filing_id),
            cells=store.load_cells(conn, filing_id),
        )
        vectors = embedder.embed_texts([embed_input(c) for c in chunks])
        with conn.transaction():
            store.delete_chunks(conn, filing_id)
            chunks_stored += store.store_chunks(conn, filing_id, chunks, vectors)
        filings_done += 1
    return filings_done, chunks_stored
```

- [ ] **Step 5: Wire the CLI** in `backend/src/pipeline/__main__.py`. Add after `p_report`:

```python
    p_rechunk = sub.add_parser(
        "rechunk",
        help="rebuild chunks and embeddings from stored sentences and cells (keeps sids)",
    )
    p_rechunk.add_argument("--ticker", help="restrict to one curated ticker")
```

and after the `table-report` handler:

```python
    if args.cmd == "rechunk":
        from .embed import Embedder

        with db.connect() as conn:
            filings_done, chunks_stored = ingest.rechunk_filings(
                conn, Embedder(), ticker=args.ticker
            )
        print(f"rechunked {filings_done} filings into {chunks_stored} chunks")
        return
```

- [ ] **Step 6: Run tests, lint, commit**

Run: `pytest tests/test_rechunk.py tests/test_store_chunks.py tests/test_reprocess.py tests/test_ingest.py -v` → all PASS.

```bash
ruff check .
git add src/pipeline/store.py src/pipeline/ingest.py src/pipeline/__main__.py tests/test_rechunk.py
git commit -m "feat: embed chunks with their table context; add rechunk"
```

---

### Task 15: Retrieval — return context, cap slots per table

**Files:**
- Modify: `backend/src/api/retrieval.py`
- Test: `backend/tests/test_retrieval_cap.py`; one db test appended to `backend/tests/test_retrieval.py`

**Interfaces:**
- Consumes: `chunks.context`, `chunks.table_id` (Tasks 8, 14).
- Produces: `RetrievedChunk.context: str = ""`, `RetrievedChunk.table_id: int | None = None` (after `score`); `MAX_CHUNKS_PER_TABLE = 2`; `cap_per_table(ranked: list[tuple[int, float]], table_of: dict[int, tuple[int, int] | None], k_final: int, *, cap: int = MAX_CHUNKS_PER_TABLE) -> list[tuple[int, float]]`.

- [ ] **Step 1: Write the failing tests** — `backend/tests/test_retrieval_cap.py`:

```python
from datetime import date

from api.retrieval import MAX_CHUNKS_PER_TABLE, RetrievedChunk, cap_per_table
from api.verify import Citation, verify_citation
from pipeline.canonicalize import Sentence


def test_a_table_gets_at_most_two_slots_and_the_rest_back_fill():
    ranked = [(i, 1.0 - i / 100) for i in range(1, 11)]
    table_of = {i: (7, 3) if i <= 5 else None for i in range(1, 11)}
    kept = cap_per_table(ranked, table_of, k_final=4)
    assert [chunk_id for chunk_id, _ in kept] == [1, 2, 6, 7]
    assert MAX_CHUNKS_PER_TABLE == 2


def test_the_same_table_id_in_two_filings_is_two_tables():
    ranked = [(1, 0.9), (2, 0.8), (3, 0.7), (4, 0.6)]
    table_of = {1: (7, 3), 2: (7, 3), 3: (8, 3), 4: (8, 3)}
    assert [c for c, _ in cap_per_table(ranked, table_of, k_final=4)] == [1, 2, 3, 4]


def test_order_is_preserved_and_short_lists_are_fine():
    ranked = [(5, 0.9), (6, 0.5)]
    assert cap_per_table(ranked, {5: None, 6: None}, k_final=8) == ranked


def test_a_quote_found_only_in_context_is_unverified():
    """Spec §5.4: context is shown to the model but never verified against."""
    sentence = Sentence(0, "item7", "Data Center $ 115,186", 0, 21, 1)
    chunk = RetrievedChunk(
        chunk_id=1, accession="A-1", form_type="10-K", filing_date=date(2025, 2, 26),
        ticker="NVDA", section="item7", sid_start=0, sid_end=0,
        text=sentence.text, filing_id=1, score=0.5,
        context="Table: Revenue by market | Scale: in millions", table_id=None,
    )
    result = verify_citation(Citation(1, 1, "Revenue by market"), chunk, [sentence])
    assert result.verified is False
```

Append to `backend/tests/test_retrieval.py`:

```python
@pytest.mark.db
def test_context_is_returned_and_lexically_searchable(seeded_conn):
    text = "Pieces of a table with no distinctive words."
    sentence = Sentence(0, "item7", text, 0, len(text))
    canonical = CanonicalFiling(text, [sentence], f'<p><span data-sid="0">{text}</span></p>')
    ref = FilingRef(
        cik=ALPHA.cik, accession="TESTC-24-000003", form_type="10-K",
        filing_date=date(2024, 11, 1), period_end=None, primary_document="t.htm",
    )
    filing_id = store.store_filing(seeded_conn, ALPHA, ref, canonical)
    chunk = Chunk("item7", 0, 0, text, 10, context="Table: okapi appendix", table_id=4)
    store.store_chunks(seeded_conn, filing_id, [chunk], FakeEmbedder().embed_texts([text]))
    seeded_conn.commit()
    try:
        results = retrieve(
            seeded_conn, FakeEmbedder(), "okapi appendix", k_final=8, ticker=ALPHA.ticker
        )
        hit = next(r for r in results if r.accession == "TESTC-24-000003")
        assert hit.context == "Table: okapi appendix"
        assert hit.table_id == 4
        assert hit.text == text
    finally:
        with seeded_conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE filing_id = %s", (filing_id,))
            cur.execute("DELETE FROM sentences WHERE filing_id = %s", (filing_id,))
            cur.execute("DELETE FROM filings WHERE id = %s", (filing_id,))
        seeded_conn.commit()
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_retrieval_cap.py tests/test_retrieval.py -v`
Expected: FAIL — `cannot import name 'cap_per_table'`.

- [ ] **Step 3: Implement** in `backend/src/api/retrieval.py`.

`_BASE` becomes:

```python
_BASE = (
    "SELECT ch.id, f.accession, f.form_type, f.filing_date, c.ticker, ch.section,"
    " ch.sid_start, ch.sid_end, ch.text, ch.filing_id, ch.context, ch.table_id"
    " FROM chunks ch"
    " JOIN filings f ON f.id = ch.filing_id"
    " JOIN companies c ON c.cik = f.cik"
)
```

Add two fields at the end of `RetrievedChunk`:

```python
    # Spec 2026-09-29 §5: shown to the model, never verified against.
    context: str = ""
    # Set only on pieces of a split table; the per-table cap keys on it.
    table_id: int | None = None
```

Add below `RRF_K = 60`:

```python
# Spec 2026-09-29 §5.6: a split table may take at most this many of the final
# slots. Two, not one, so a year-over-year question can receive both the
# current and prior-period band of one table.
MAX_CHUNKS_PER_TABLE = 2
```

Add above `retrieve`:

```python
def cap_per_table(
    ranked: list[tuple[int, float]],
    table_of: dict[int, tuple[int, int] | None],
    k_final: int,
    *,
    cap: int = MAX_CHUNKS_PER_TABLE,
) -> list[tuple[int, float]]:
    """Walk the fused ranking best-first, skipping a chunk once its table
    (keyed by filing and table id) already holds `cap` slots.

    A separate step after scoring on purpose: a pointwise reranker would score
    every sibling piece of a relevant table highly, so the cap is what keeps
    one table from filling the slots. A reranker slots in before this call.
    """
    kept: list[tuple[int, float]] = []
    used: dict[tuple[int, int], int] = {}
    for chunk_id, score in ranked:
        key = table_of.get(chunk_id)
        if key is not None:
            if used.get(key, 0) >= cap:
                continue
            used[key] = used.get(key, 0) + 1
        kept.append((chunk_id, score))
        if len(kept) == k_final:
            break
    return kept
```

At the end of `retrieve`, replace:

```python
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k_final]
    return [RetrievedChunk(*rows_by_id[chunk_id], score) for chunk_id, score in ranked]
```

with:

```python
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    table_of = {
        chunk_id: (row[9], row[11]) if row[11] is not None else None
        for chunk_id, row in rows_by_id.items()
    }
    kept = cap_per_table(ranked, table_of, k_final)
    return [
        RetrievedChunk(*rows_by_id[chunk_id][:10], score, *rows_by_id[chunk_id][10:])
        for chunk_id, score in kept
    ]
```

- [ ] **Step 4: Run tests, lint, commit**

Run: `pytest tests/test_retrieval_cap.py tests/test_retrieval.py tests/test_targets.py tests/test_evals.py tests/test_answer.py -v` → all PASS.

```bash
ruff check .
git add src/api/retrieval.py tests/test_retrieval_cap.py tests/test_retrieval.py
git commit -m "feat: retrieval returns chunk context and caps slots per split table"
```

---

### Task 16: Prompt — show table context, forbid quoting it

**Files:**
- Modify: `backend/src/api/generate.py`
- Test: `backend/tests/test_generate.py`

**Interfaces:**
- Consumes: `RetrievedChunk.context` (Task 15).
- Produces: `CONTEXT_LABEL = "Table context (for reading columns; not quotable):"`; `build_user_message` renders one labelled line per context line; `SYSTEM_PROMPT` rules 4–5 (context not quotable; table quotes end at the figure).

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_generate.py`:

```python
def test_a_table_chunk_shows_each_context_line_labelled_above_its_text():
    from dataclasses import replace

    from api.generate import CONTEXT_LABEL

    table = replace(
        chunk(7, "Data Center $ 115,186 $ 47,525"),
        context="Table: First | Columns: 2025; 2024\nTable: Second | Columns: 2025",
    )
    message = build_user_message("Data Center revenue?", [table])
    lines = message.splitlines()
    header = next(i for i, line in enumerate(lines) if "chunk_id=7" in line)
    assert lines[header + 1] == f"{CONTEXT_LABEL} Table: First | Columns: 2025; 2024"
    assert lines[header + 2] == f"{CONTEXT_LABEL} Table: Second | Columns: 2025"
    assert lines[header + 3] == "Data Center $ 115,186 $ 47,525"


def test_a_prose_chunk_has_no_context_line():
    from api.generate import CONTEXT_LABEL

    message = build_user_message("Why?", [chunk(8, "Net sales rose.")])
    assert CONTEXT_LABEL not in message


def test_system_prompt_forbids_quoting_context_before_the_output_example():
    from api.generate import FENCE, SYSTEM_PROMPT

    assert "never quote them" in SYSTEM_PROMPT
    assert "through that figure" in SYSTEM_PROMPT
    assert SYSTEM_PROMPT.index("never quote them") < SYSTEM_PROMPT.index(FENCE)
    assert SYSTEM_PROMPT.index("through that figure") < SYSTEM_PROMPT.index(FENCE)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_generate.py -v`
Expected: FAIL — `cannot import name 'CONTEXT_LABEL'`.

- [ ] **Step 3: Implement** in `backend/src/api/generate.py`.

Replace `SYSTEM_PROMPT` with the version below: two rules are inserted before the "After the answer, emit a fenced JSON block" rule, which is renumbered to 6. Everything else is unchanged. (The string contains a literal triple-backtick fence, so this block is fenced with four backticks.)

````python
SYSTEM_PROMPT = """You answer questions about SEC filings using only the excerpts provided.

Rules:
1. Answer ONLY from the provided excerpts. If they do not contain the answer, say
   so plainly and stop — do not fall back on general knowledge about the company.
2. Attach an inline marker to every factual claim: [1], [2], and so on, numbered
   from 1 in the order they first appear.
3. When excerpts from more than one filing support the answer, cite each of
   them. Do not collapse several filings into a single citation, and do not
   answer only from whichever excerpt appeared first.
4. Lines labelled "Table context" give a table's caption, scale and column
   headings so you can tell which column a figure sits in. Use them to read
   the table, but never quote them: quotes come only from excerpt text.
5. When you cite a figure from a table row, quote from the start of the row
   through that figure and stop there.
6. After the answer, emit a fenced JSON block and nothing after it:

```json
{"citations": [{"marker": 1, "chunk_id": 8123, "quote": "verbatim text from that chunk"}]}
```

Every quote must be copied character-for-character from the excerpt whose
chunk_id you cite, and must be 300 characters or fewer. Do not paraphrase,
reformat, join sentences with an ellipsis, or fix typography inside a quote —
quotes are checked against the source text and a mismatch is shown to the user
as unverified. Prefer a short exact quote over a long approximate one."""
````

Add directly below the existing `FENCE` constant:

```python
# Spec 2026-09-29 §5.5. The label itself tells the model the line is not
# quotable; rule 4 of SYSTEM_PROMPT says so again.
CONTEXT_LABEL = "Table context (for reading columns; not quotable):"
```

Replace `build_user_message` with:

```python
def build_user_message(question: str, chunks: list[RetrievedChunk]) -> str:
    blocks = []
    for c in chunks:
        header = f"[chunk_id={c.chunk_id}] {c.ticker} {c.form_type} {c.accession} ({c.section})"
        context = "".join(
            f"\n{CONTEXT_LABEL} {line}" for line in c.context.splitlines() if line
        )
        blocks.append(f"{header}{context}\n{c.text}")
    excerpts = "\n\n".join(blocks) if blocks else "(no excerpts were retrieved)"
    return f"Excerpts:\n\n{excerpts}\n\nQuestion: {question}"
```

- [ ] **Step 4: Run tests, lint, commit**

Run: `pytest tests/test_generate.py tests/test_answer.py -v` → all PASS (including `test_multi_filing_rule_precedes_the_output_format_example`).

```bash
ruff check .
git add src/api/generate.py tests/test_generate.py
git commit -m "feat: show table context to the model and keep it out of quotes"
```

---

### Task 17: Corpus rollout, eval ×3, docs, PR B — CONTROLLER

Run from `backend/` in the Part B worktree with `DATABASE_URL` = full corpus. These commands need network access to Postgres (and the Anthropic API for evals).

- [ ] **Step 1: Full test suite green.** `pytest -v` and `ruff check .`; `npm test`, `npm run lint`, `npm run test:e2e` in PowerShell from `frontend/`.

- [ ] **Step 2: Migrate and backfill cells.**

```bash
python -m pipeline migrate
python -m pipeline retable
python -m pipeline table-report
```

Expected: `updated 120 filings, 0 missing from cache`, no `SENTENCE MISMATCH` line; the report near the measured baseline at the top of this plan (≈281,668 cells, ≈96% labelled, ≈57% scaled, ≈66% splittable). Record the report in the PR description. Stop and investigate on any mismatch.

- [ ] **Step 3: Rebuild chunks** (long — local embedding of ~21k chunks; run in the background and wait for it).

```bash
python -m pipeline rechunk
docker exec -i sec-rag-db-1 psql -U user -d edgar_answers -c "SELECT count(*), count(*) FILTER (WHERE table_id IS NOT NULL), count(*) FILTER (WHERE token_count > 450) FROM chunks;"
```

Expected: ≈21,259 chunks, ≈3,419 split pieces. Record in the PR.

- [ ] **Step 4: Eval ×3, committing after each run.**

```bash
python -m evals run
git add evals/results.jsonl
git commit -m "evals: record table column binding run 1 of 3"
```

Repeat for runs 2 and 3. Every row must show `git_dirty: false`.

- [ ] **Step 5: Compare against PR A's three baseline rows** and write the comparison into the PR description: `table_tail_recall@10`, `value_accuracy`, `recall@10`, `unfiltered_recall@10`, `verified_rate`, `answered_rate`, and the range (not a single value) of `gold_sid_hit_rate`. Call out q004 and q009 specifically. If `verified_rate` dropped, grep the run's unverified quotes for text that appears only in a context line.

- [ ] **Step 6: Update the docs.**
  - `docs/design.md`: §4.2 (tables are parsed into cells; `filing_tables`/`table_cells`), §4.3 (split tables, context, the 450 budget covering context), §5 (schema additions), §6.1 (context in the lexical index; per-table cap of 2), §6.2 (the two new prompt rules), §6.3 (cell resolution), §6.4 (`cells` on the `citation` event), §7 (cited-figure highlight).
  - `CLAUDE.md` "Current state": one bullet summarising column binding, the eval comparison numbers, and that `retable` / `rechunk` exist; note `table_cells` is the substrate for the arithmetic-verification spec.

```bash
git add docs/design.md CLAUDE.md
git commit -m "docs: record table column binding in the design and project notes"
```

- [ ] **Step 7: Push and open PR B** (no AI attribution in the body):

```bash
git push -u origin table-column-binding
gh pr create --base main --head table-column-binding --title "Table column binding, header-carrying table chunks, cited-figure highlight" --body-file "$TMP/pr-body.md"   # write the body there first; keep it out of the repo
```
