# Fiscal Period Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve a question, given a known company, to the specific SEC
filing(s) it refers to (by accession number), so retrieval can narrow to
exactly those filings before ranking runs — attacking the documented
q004/q009 period-confusion gap where AAPL's near-identical quarterly
boilerplate crowds out the correct period.

**Architecture:** A new `detect_periods` LLM call (mirroring entity
resolution's `detect_companies`, living in the same `api/detect.py` module)
reads a company's actual filing list and picks specific accession(s) rather
than computing a fiscal year — sidestepping the `filing_date`-vs-`period_end`
ambiguity `design.md` already ruled out. `retrieval.py` gains a matching
`accessions` filter, the same shape as its existing `ticker`/`form_type`
clauses. A hand-labeled case set scores detection accuracy independently of
retrieval, plus a direct verification that retrieval itself now surfaces
q004/q009's previously-missing gold sentences.

**Tech Stack:** Python 3.13, `anthropic` (already a dependency), `pytest`,
`pyyaml`.

**Spec:** `docs/superpowers/specs/2026-08-29-fiscal-period-handling-design.md`

## Global Constraints

- Python 3.13 everywhere; no new dependencies.
- Model is `claude-haiku-4-5` (import `MODEL` from `api.generate`, don't
  redefine the string) at `temperature=0`.
- `ruff==0.16.1` is pinned exactly; run `ruff check .` before each commit.
- `anthropic` is bounded `>=0.120.2,<1` in `backend/pyproject.toml` — do not
  loosen or tighten this range as part of this plan.
- No database migration is needed — `filings.accession`, `.form_type`,
  `.filing_date`, and `.period_end` already exist; this plan only reads them.
- Commit messages: no AI attribution of any kind (no Co-Authored-By, no
  session trailers, no tool names) — hard project rule.
- Unit tests never call the live Anthropic API — `AnthropicPeriodDetector`
  itself is exercised only by the manual `python -m evals periods` command
  in Task 5, matching the precedent set by `AnthropicCompanyDetector` and
  `AnthropicGenerator` (neither has pytest coverage of its `.detect()`/
  `.stream()` method).
- A detector that isn't confident returns `[]` (or, for periods, an empty
  list meaning "scope to the ticker only") — never guess. This must hold
  even under the accession-validation logic: an accession that doesn't
  belong to the ticker's own filing list is dropped, not surfaced.

---

### Task 1: `accessions` filter on retrieval

**Files:**
- Modify: `backend/src/api/retrieval.py`
- Test: `backend/tests/test_retrieval.py`

**Interfaces:**
- Produces: `retrieval._filters(ticker, form_type, accessions=None)`,
  `retrieval.vector_search(..., accessions: list[str] | None = None)`,
  `retrieval.lexical_search(..., accessions: list[str] | None = None)`,
  `retrieval.retrieve(..., accessions: list[str] | None = None)`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_retrieval.py  (append)
@pytest.mark.db
def test_accessions_filter_narrows_to_one_filing_within_a_ticker(seeded_conn):
    # Both ALPHA filings mention "zebra" (TESTC-24-000001's covenant text and
    # TESTC-24-000002's logistics text), so an unscoped or ticker-only query
    # would surface both. Pinning accessions must exclude the other one even
    # though it belongs to the same company.
    results = retrieve(
        seeded_conn, FakeEmbedder(), "zebra imports",
        ticker="TSTC", accessions=["TESTC-24-000001"],
    )
    accessions = {r.accession for r in results}
    assert accessions == {"TESTC-24-000001"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_retrieval.py::test_accessions_filter_narrows_to_one_filing_within_a_ticker -v`
Expected: FAIL with `TypeError: retrieve() got an unexpected keyword argument 'accessions'`

- [ ] **Step 3: Write the minimal implementation**

```python
# backend/src/api/retrieval.py
# Replace the existing _filters function with:
def _filters(
    ticker: str | None, form_type: str | None, accessions: list[str] | None = None
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if ticker:
        clauses.append("c.ticker = %s")
        params.append(ticker.upper())
    if form_type:
        clauses.append("f.form_type = %s")
        params.append(form_type)
    if accessions:
        clauses.append("f.accession = ANY(%s)")
        params.append(accessions)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


# Replace vector_search's signature and body with:
def vector_search(
    conn: psycopg.Connection,
    query_vector: list[float],
    *,
    k: int = 20,
    ticker: str | None = None,
    form_type: str | None = None,
    accessions: list[str] | None = None,
) -> list[tuple]:
    where, params = _filters(ticker, form_type, accessions)
    sql = _BASE + where + " ORDER BY ch.embedding <=> %s::vector LIMIT %s"
    with conn.cursor() as cur:
        cur.execute(sql, [*params, to_pgvector(query_vector), k])
        return cur.fetchall()


# Replace lexical_search's signature and body with:
def lexical_search(
    conn: psycopg.Connection,
    question: str,
    *,
    k: int = 20,
    ticker: str | None = None,
    form_type: str | None = None,
    accessions: list[str] | None = None,
) -> list[tuple]:
    where, params = _filters(ticker, form_type, accessions)
    match = f"{_TSVECTOR} @@ {_OR_TSQUERY}"
    where = where + (" AND " if where else " WHERE ") + match
    sql = (
        _BASE
        + where
        + f" ORDER BY ts_rank_cd({_TSVECTOR}, {_OR_TSQUERY}) DESC LIMIT %s"
    )
    with conn.cursor() as cur:
        cur.execute(sql, [*params, question, question, k])
        return cur.fetchall()


# Replace retrieve's signature and body with:
def retrieve(
    conn: psycopg.Connection,
    embedder,
    question: str,
    *,
    k_each: int = 20,
    k_final: int = 8,
    ticker: str | None = None,
    form_type: str | None = None,
    accessions: list[str] | None = None,
) -> list[RetrievedChunk]:
    """Hybrid retrieval per design §6.1: vector + lexical arms fused with RRF."""
    query_vector = embedder.embed_query(question)
    vector_rows = vector_search(
        conn, query_vector, k=k_each, ticker=ticker, form_type=form_type,
        accessions=accessions,
    )
    lexical_rows = lexical_search(
        conn, question, k=k_each, ticker=ticker, form_type=form_type,
        accessions=accessions,
    )

    scores: dict[int, float] = {}
    rows_by_id: dict[int, tuple] = {}
    for rows in (vector_rows, lexical_rows):
        for rank, row in enumerate(rows):
            chunk_id = row[0]
            rows_by_id[chunk_id] = row
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k_final]
    return [RetrievedChunk(*rows_by_id[chunk_id], score) for chunk_id, score in ranked]
```

Note: `evals/harness.py::_score` calls `retrieve(..., **({"ticker": question.ticker} if scoped else {}))` — this passes no `accessions` kwarg either way, so it is unaffected by this change and needs no edit.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_retrieval.py -v`
Expected: PASS (all tests in the file, including the new one)

- [ ] **Step 5: Run the full suite and lint, then commit**

```bash
cd backend
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/api/retrieval.py tests/test_retrieval.py
git add src/api/retrieval.py tests/test_retrieval.py
git commit -m "feat: add an accessions filter to retrieval"
```

---

### Task 2: Load a company's filing list

**Files:**
- Modify: `backend/src/api/queries.py`
- Create: `backend/tests/test_queries.py`

**Interfaces:**
- Produces: `queries.load_filings_for_ticker(conn, ticker: str) -> list[dict]`
  (each dict: `{accession, form_type, filing_date, period_end}`, `filing_date`
  and `period_end` as ISO date strings, `period_end` possibly `None`).
- Consumes: nothing new.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_queries.py
import os
from datetime import date

import psycopg
import pytest

from api.queries import load_filings_for_ticker
from pipeline import db, store
from pipeline.canonicalize import CanonicalFiling, Sentence
from pipeline.chunk import Chunk
from pipeline.companies import Company
from pipeline.edgar import FilingRef
from tests.fakes import FakeEmbedder

COMPANY = Company(999999006, "TSTF", "Test Co F")


def seed_filing(conn, accession, filing_date, period_end, form_type="10-Q"):
    text = f"Filing {accession} reports quarterly results."
    sentence = Sentence(0, "item1", text, 0, len(text))
    canonical = CanonicalFiling(
        text, [sentence], f'<p><span data-sid="0">{text}</span></p>'
    )
    ref = FilingRef(
        cik=COMPANY.cik,
        accession=accession,
        form_type=form_type,
        filing_date=filing_date,
        period_end=period_end,
        primary_document="t.htm",
    )
    filing_id = store.store_filing(conn, COMPANY, ref, canonical)
    store.store_chunks(
        conn, filing_id, [Chunk("item1", 0, 0, text, 10)],
        FakeEmbedder().embed_texts([text]),
    )


@pytest.fixture()
def seeded_conn():
    conn = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    db.migrate(conn)
    with conn.cursor() as cur:
        for accession in ("TESTF-24-000001", "TESTF-24-000002"):
            cur.execute(
                "DELETE FROM chunks WHERE filing_id IN"
                " (SELECT id FROM filings WHERE accession = %s)",
                (accession,),
            )
            cur.execute(
                "DELETE FROM sentences WHERE filing_id IN"
                " (SELECT id FROM filings WHERE accession = %s)",
                (accession,),
            )
            cur.execute("DELETE FROM filings WHERE accession = %s", (accession,))
    conn.commit()
    seed_filing(conn, "TESTF-24-000001", date(2024, 2, 1), date(2023, 12, 30))
    seed_filing(conn, "TESTF-24-000002", date(2024, 5, 1), date(2024, 3, 30))
    yield conn
    conn.close()


@pytest.mark.db
def test_load_filings_for_ticker_returns_oldest_first_with_all_fields(seeded_conn):
    filings = load_filings_for_ticker(seeded_conn, "TSTF")
    assert [f["accession"] for f in filings] == ["TESTF-24-000001", "TESTF-24-000002"]
    assert filings[0]["form_type"] == "10-Q"
    assert filings[0]["filing_date"] == "2024-02-01"
    assert filings[0]["period_end"] == "2023-12-30"


@pytest.mark.db
def test_load_filings_for_ticker_returns_empty_for_unknown_ticker(seeded_conn):
    assert load_filings_for_ticker(seeded_conn, "ZZZZ") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_queries.py -v`
Expected: FAIL with `ImportError: cannot import name 'load_filings_for_ticker'`

- [ ] **Step 3: Write the minimal implementation**

```python
# backend/src/api/queries.py  (append)
def load_filings_for_ticker(conn: psycopg.Connection, ticker: str) -> list[dict]:
    """A company's filings, oldest first -- the candidate list a period
    detector picks specific accessions from."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT f.accession, f.form_type, f.filing_date, f.period_end"
            " FROM filings f JOIN companies c ON c.cik = f.cik"
            " WHERE c.ticker = %s ORDER BY f.filing_date",
            (ticker.upper(),),
        )
        return [
            {
                "accession": accession,
                "form_type": form_type,
                "filing_date": filing_date.isoformat(),
                "period_end": period_end.isoformat() if period_end else None,
            }
            for accession, form_type, filing_date, period_end in cur.fetchall()
        ]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_queries.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite and lint, then commit**

```bash
cd backend
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/api/queries.py tests/test_queries.py
git add src/api/queries.py tests/test_queries.py
git commit -m "feat: load a company's filing list for period detection"
```

---

### Task 3: Period detector — parsing, prompt, and the Anthropic call

**Files:**
- Modify: `backend/src/api/detect.py`
- Modify: `backend/tests/test_detect.py`

**Interfaces:**
- Consumes: `api.detect._FENCE`, `api.detect._OBJECT` (already defined,
  reused as-is), `api.generate.MODEL`.
- Produces: `api.detect.PeriodDetector` (Protocol; `detect(self, question: str, ticker: str, filings: list[dict]) -> list[str]`),
  `api.detect.parse_detected_periods(raw: str, known_accessions: set[str]) -> list[str]`,
  `api.detect.DETECTION_PERIOD_SYSTEM_PROMPT: str`,
  `api.detect.build_period_prompt(question: str, ticker: str, filings: list[dict]) -> str`,
  `api.detect.AnthropicPeriodDetector` (dataclass; fields `model: str`,
  `max_tokens: int`, `api_key: str | None`; implements `PeriodDetector`).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_detect.py  (append)
from api.detect import (
    build_period_prompt,
    parse_detected_periods,
)

KNOWN_ACCESSIONS = {"0000320193-24-000123", "0000320193-24-000069"}


def test_parse_periods_parses_bare_json():
    raw = '{"accessions": ["0000320193-24-000123"]}'
    assert parse_detected_periods(raw, KNOWN_ACCESSIONS) == ["0000320193-24-000123"]


def test_parse_periods_parses_fenced_json():
    raw = '```json\n{"accessions": ["0000320193-24-000069"]}\n```'
    assert parse_detected_periods(raw, KNOWN_ACCESSIONS) == ["0000320193-24-000069"]


def test_parse_periods_empty_accessions_list_is_valid():
    assert parse_detected_periods('{"accessions": []}', KNOWN_ACCESSIONS) == []


def test_parse_periods_malformed_json_returns_empty():
    assert parse_detected_periods('{"accessions": [oops}', KNOWN_ACCESSIONS) == []


def test_parse_periods_no_json_object_returns_empty():
    assert parse_detected_periods("Not sure which filing.", KNOWN_ACCESSIONS) == []


def test_parse_periods_non_list_field_returns_empty():
    assert parse_detected_periods(
        '{"accessions": "0000320193-24-000123"}', KNOWN_ACCESSIONS
    ) == []


def test_parse_periods_unknown_accession_is_dropped():
    raw = '{"accessions": ["0000320193-24-000123", "0000000000-00-000000"]}'
    assert parse_detected_periods(raw, KNOWN_ACCESSIONS) == ["0000320193-24-000123"]


def test_parse_periods_two_periods_for_a_comparison_question():
    raw = (
        '{"accessions": ["0000320193-24-000069", "0000320193-24-000123"]}'
    )
    result = parse_detected_periods(raw, KNOWN_ACCESSIONS)
    assert result == ["0000320193-24-000069", "0000320193-24-000123"]


def test_period_prompt_lists_each_filing_and_the_question():
    filings = [
        {
            "accession": "0000320193-24-000123",
            "form_type": "10-K",
            "filing_date": "2024-11-01",
            "period_end": "2024-09-28",
        },
    ]
    prompt = build_period_prompt(
        "How many RSUs were excluded from Apple's diluted EPS for fiscal 2023?",
        "AAPL",
        filings,
    )
    assert "0000320193-24-000123" in prompt
    assert "10-K" in prompt
    assert "2024-11-01" in prompt
    assert "2024-09-28" in prompt
    assert "AAPL" in prompt
    assert "How many RSUs were excluded" in prompt
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_detect.py -k parse_periods -v`
Expected: FAIL with `ImportError: cannot import name 'parse_detected_periods'`

- [ ] **Step 3: Write the minimal implementation**

```python
# backend/src/api/detect.py  (append)
class PeriodDetector(Protocol):
    """The LLM, narrowed to the one thing fiscal period handling needs."""

    def detect(self, question: str, ticker: str, filings: list[dict]) -> list[str]:
        """Return the accessions (from `filings`) the question refers to."""
        ...


def parse_detected_periods(raw: str, known_accessions: set[str]) -> list[str]:
    """Defensive parse of a period detector's raw response into an accession list.

    Mirrors parse_detected_companies's contract: a hallucinated or
    wrong-company accession, malformed JSON, or a response with no JSON
    object at all all degrade to `[]` -- "no period pin" means "scope to
    the ticker only," which is always a safe fallback.
    """
    text = _FENCE.sub("", raw.strip())
    match = _OBJECT.search(text)
    if match is None:
        return []
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    accessions = payload.get("accessions")
    if not isinstance(accessions, list):
        return []
    seen: list[str] = []
    for accession in accessions:
        if (
            isinstance(accession, str)
            and accession in known_accessions
            and accession not in seen
        ):
            seen.append(accession)
    return seen


DETECTION_PERIOD_SYSTEM_PROMPT = (
    """You identify which specific SEC filing(s) of one company a question refers to.

You will be given the company's filings, each shown as its accession number,
form type, filing date, and period-end date. Decide which filing(s), if any,
the question is asking about, based on the fiscal year, quarter, or other
period language in the question.

Respond with strict JSON and nothing else:

{"accessions": ["0000320193-24-000123"]}

Rules:
1. Only use accession numbers that appear in the filing list you were given.
   Never invent one.
2. If the question does not reference a specific period, or you are not
   confident which filing(s) it means, respond with {"accessions": []}.
   Do not guess.
3. A question comparing two periods may name two filings.
"""
)


def build_period_prompt(question: str, ticker: str, filings: list[dict]) -> str:
    roster = "\n".join(
        f"{f['accession']} | {f['form_type']} | filed {f['filing_date']}"
        f" | period_end {f['period_end']}"
        for f in filings
    )
    return f"Company: {ticker}\nFilings:\n{roster}\n\nQuestion: {question}"


@dataclass
class AnthropicPeriodDetector:
    model: str = MODEL
    max_tokens: int = 256
    api_key: str | None = None

    def detect(self, question: str, ticker: str, filings: list[dict]) -> list[str]:
        import anthropic

        client = anthropic.Anthropic(
            api_key=self.api_key or os.environ["ANTHROPIC_API_KEY"]
        )
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0,
            system=DETECTION_PERIOD_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": build_period_prompt(question, ticker, filings),
                }
            ],
        )
        raw = "".join(block.text for block in message.content if block.type == "text")
        known_accessions = {f["accession"] for f in filings}
        return parse_detected_periods(raw, known_accessions)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_detect.py -v`
Expected: PASS (all tests in the file: the existing entity-resolution tests plus the new period ones)

- [ ] **Step 5: Run the full suite and lint, then commit**

```bash
cd backend
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/api/detect.py tests/test_detect.py
git add src/api/detect.py tests/test_detect.py
git commit -m "feat: add the Anthropic-backed period detector"
```

---

### Task 4: Hand-labeled period case set and accuracy scoring

**Files:**
- Create: `backend/evals/fiscal_period_cases.yaml`
- Create: `backend/evals/fiscal_period_handling.py`
- Modify: `backend/tests/fakes.py`
- Create: `backend/tests/test_fiscal_period_handling.py`

**Interfaces:**
- Consumes: the `PeriodDetector` shape from Task 3 (structural — no import
  needed by the fake).
- Produces: `evals.fiscal_period_handling.PeriodCase` (dataclass: `id: str`,
  `question: str`, `ticker: str`, `expected_accessions: list[str]`),
  `evals.fiscal_period_handling.load_cases(path=CASES_PATH) -> list[PeriodCase]`,
  `evals.fiscal_period_handling.run_period_resolution_eval(detector, filings_by_ticker: dict[str, list[dict]], cases: list[PeriodCase]) -> dict`
  (keys: `cases`, `correct`, `accuracy`, `mismatches`),
  `tests.fakes.StubPeriodDetector`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/fakes.py  (append)
class StubPeriodDetector:
    """Returns a canned accession list per exact question text, for testing
    consumers of PeriodDetector without hitting the live API."""

    def __init__(self, answers: dict[str, list[str]] | None = None):
        self.answers = answers or {}
        self.calls: list[str] = []

    def detect(self, question, ticker, filings):
        self.calls.append(question)
        return self.answers.get(question, [])
```

```python
# backend/tests/test_fiscal_period_handling.py
import pytest

from evals.fiscal_period_handling import (
    PeriodCase,
    load_cases,
    run_period_resolution_eval,
)
from tests.fakes import StubPeriodDetector


def test_load_cases_validates_fields(tmp_path):
    good = tmp_path / "cases.yaml"
    good.write_text(
        "- id: p001\n"
        "  question: What did Apple report for fiscal 2024?\n"
        "  ticker: AAPL\n"
        "  expected_accessions: [\"0000320193-24-000123\"]\n",
        encoding="utf-8",
    )
    cases = load_cases(good)
    assert cases[0].id == "p001"
    assert cases[0].ticker == "AAPL"
    assert cases[0].expected_accessions == ["0000320193-24-000123"]

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "- id: p002\n  question: Missing ticker and expected_accessions\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="p002"):
        load_cases(bad)


def test_run_period_resolution_eval_scores_exact_set_match():
    cases = [
        PeriodCase("p001", "Q2 FY2024 gross margin?", "AAPL", ["ACC-A"]),
        PeriodCase("p002", "What is Apple's business model?", "AAPL", []),
    ]
    detector = StubPeriodDetector(
        {
            "Q2 FY2024 gross margin?": ["ACC-A"],
            "What is Apple's business model?": ["ACC-WRONG"],
        }
    )
    metrics = run_period_resolution_eval(
        detector, {"AAPL": [{"accession": "ACC-A"}]}, cases
    )
    assert metrics["cases"] == 2
    assert metrics["correct"] == 1
    assert metrics["accuracy"] == 0.5
    assert metrics["mismatches"] == [
        {"id": "p002", "expected": [], "actual": ["ACC-WRONG"]}
    ]


def test_run_period_resolution_eval_passes_each_case_its_own_ticker_filings():
    cases = [
        PeriodCase("p001", "AAPL question", "AAPL", ["ACC-A"]),
        PeriodCase("p002", "MSFT question", "MSFT", ["ACC-M"]),
    ]
    detector = StubPeriodDetector({"AAPL question": ["ACC-A"], "MSFT question": ["ACC-M"]})
    filings_by_ticker = {
        "AAPL": [{"accession": "ACC-A"}],
        "MSFT": [{"accession": "ACC-M"}],
    }
    metrics = run_period_resolution_eval(detector, filings_by_ticker, cases)
    assert metrics["accuracy"] == 1.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_fiscal_period_handling.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.fiscal_period_handling'`

- [ ] **Step 3: Write the minimal implementation**

```python
# backend/evals/fiscal_period_handling.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CASES_PATH = Path(__file__).parent / "fiscal_period_cases.yaml"
_REQUIRED = ("id", "question", "ticker", "expected_accessions")


@dataclass(frozen=True)
class PeriodCase:
    id: str
    question: str
    ticker: str
    expected_accessions: list[str]


def load_cases(path: Path = CASES_PATH) -> list[PeriodCase]:
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    cases = []
    for entry in entries:
        entry_id = entry.get("id", "<missing id>")
        for field in _REQUIRED:
            if field not in entry:
                raise ValueError(f"case {entry_id}: missing field {field!r}")
        if not isinstance(entry["expected_accessions"], list):
            raise ValueError(f"case {entry_id}: expected_accessions must be a list")
        cases.append(PeriodCase(**{f: entry[f] for f in _REQUIRED}))
    return cases


def run_period_resolution_eval(
    detector, filings_by_ticker: dict[str, list[dict]], cases: list[PeriodCase]
) -> dict:
    """Score a PeriodDetector against hand-labeled cases.

    Like entity_resolution.run_entity_resolution_eval, no retrieval is
    involved -- this judges the detector's raw output directly, so order
    never matters.
    """
    correct = 0
    mismatches = []
    for case in cases:
        filings = filings_by_ticker[case.ticker]
        actual = detector.detect(case.question, case.ticker, filings)
        if set(actual) == set(case.expected_accessions):
            correct += 1
        else:
            mismatches.append(
                {"id": case.id, "expected": case.expected_accessions, "actual": actual}
            )
    n = len(cases)
    return {
        "cases": n,
        "correct": correct,
        "accuracy": round(correct / n, 4) if n else 0.0,
        "mismatches": mismatches,
    }
```

```yaml
# backend/evals/fiscal_period_cases.yaml
# Hand-labeled cases for fiscal period handling (spec
# docs/superpowers/specs/2026-08-29-fiscal-period-handling-design.md).
# Scored by `python -m evals periods` directly against the live detector and
# the real AAPL filing list -- not retrieval-scored, so this does not append
# to results.jsonl. p001 and p002 are q004 and q009 from golden.yaml: the two
# questions CLAUDE.md documents as failing today because retrieval cannot
# separate AAPL's near-identical quarterly boilerplate by period. Re-run more
# than once before trusting a single score (see entity_resolution_cases.yaml
# for why).

- id: p001
  question: How many RSUs were excluded from Apple's diluted earnings per share
    computation for fiscal 2023?
  ticker: AAPL
  expected_accessions: ["0000320193-24-000123"]
- id: p002
  question: What were Apple's total gross margin percentages for Products and
    Services in the second quarter of fiscal 2024?
  ticker: AAPL
  expected_accessions: ["0000320193-24-000069"]
- id: p003
  question: What were Apple's total net sales in fiscal 2025?
  ticker: AAPL
  expected_accessions: ["0000320193-25-000079"]
- id: p004
  question: What is Apple's core business model?
  ticker: AAPL
  expected_accessions: []
- id: p005
  question: How did Apple's Q2 FY2024 gross margin compare to its Q2 FY2025
    gross margin?
  ticker: AAPL
  expected_accessions: ["0000320193-24-000069", "0000320193-25-000057"]
- id: p006
  question: What did Apple report for its fiscal Q3 2026?
  ticker: AAPL
  expected_accessions: ["0000320193-26-000020"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_fiscal_period_handling.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full suite and lint, then commit**

```bash
cd backend
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check evals/fiscal_period_handling.py tests/fakes.py tests/test_fiscal_period_handling.py
git add evals/fiscal_period_handling.py evals/fiscal_period_cases.yaml tests/fakes.py tests/test_fiscal_period_handling.py
git commit -m "feat: score fiscal period resolution against a hand-labeled case set"
```

---

### Task 5: Wire the `periods` eval subcommand and verify against the real corpus

**Files:**
- Modify: `backend/evals/__main__.py`

**Interfaces:**
- Consumes: `api.detect.AnthropicPeriodDetector` (Task 3),
  `api.queries.load_filings_for_ticker` (Task 2),
  `evals.fiscal_period_handling.load_cases`,
  `evals.fiscal_period_handling.run_period_resolution_eval` (Task 4),
  `api.retrieval.retrieve` (Task 1, for the manual verification step only).
- Produces: `python -m evals periods` CLI command.

- [ ] **Step 1: Add the subcommand function**

```python
# backend/evals/__main__.py  (add near cmd_entities)
def cmd_periods(args) -> None:
    from api import queries
    from api.detect import AnthropicPeriodDetector

    from . import fiscal_period_handling

    cases = fiscal_period_handling.load_cases()
    tickers = {case.ticker for case in cases}
    with db.connect() as conn:
        filings_by_ticker = {
            ticker: queries.load_filings_for_ticker(conn, ticker) for ticker in tickers
        }
    metrics = fiscal_period_handling.run_period_resolution_eval(
        AnthropicPeriodDetector(), filings_by_ticker, cases
    )
    for mismatch in metrics["mismatches"]:
        print(
            f"FAIL {mismatch['id']}: expected {mismatch['expected']},"
            f" got {mismatch['actual']}"
        )
    print(
        f"\n{metrics['correct']}/{metrics['cases']} correct"
        f" ({metrics['accuracy']:.2%})"
    )
```

- [ ] **Step 2: Register the subparser and dispatch branch**

In `main()`, add the subparser next to `entities`:

```python
    sub.add_parser(
        "periods", help="score fiscal-period-resolution detection against hand-labeled cases"
    )
```

And add the explicit dispatch branch (same fallthrough hazard as `entities` —
an undispatched `args.cmd` value falls through to `cmd_verify`, since it's
the last `elif`'s `else`):

```python
    if args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "repin":
        cmd_repin(args)
    elif args.cmd == "entities":
        cmd_entities(args)
    elif args.cmd == "periods":
        cmd_periods(args)
    else:
        cmd_verify(args)
```

- [ ] **Step 3: Run the case-set eval manually against the live API, twice**

Run: `cd backend && .venv/Scripts/python.exe -m evals periods` (twice, not
once — this repo's own documented LLM run-to-run variance means one run
isn't trustworthy on its own).

Expected: prints any mismatches, then a line like `5/6 correct (83.33%)`,
for each of the two runs. Record both full outputs in your report. If a
mismatch looks like a genuine detector error rather than a mis-specified
case, you may adjust `DETECTION_PERIOD_SYSTEM_PROMPT` (Task 3) or the case
file and re-run — but do not force a particular score by weakening a case's
correctness.

- [ ] **Step 4: Verify retrieval itself now surfaces q004/q009's gold sentences**

This is spec §5's second, separate check: not a new eval-harness arm (that's
the query-decomposition plan's job), just confirming the Task 1 retrieval
change actually fixes the two originally-failing questions when given their
already-known-correct accessions from `evals/golden.yaml`. Run this
one-off verification script (it needs `DATABASE_URL`, already loaded from
`.env`, and the real corpus — not `TEST_DATABASE_URL`):

```bash
cd backend && .venv/Scripts/python.exe -c "
from pipeline.env import load_env
load_env()
from pipeline.db import connect
from pipeline.embed import Embedder
from api.retrieval import retrieve

conn = connect()
embedder = Embedder()

chunks = retrieve(
    conn, embedder,
    \"How many RSUs were excluded from Apple's diluted earnings per share computation for fiscal 2023?\",
    ticker='AAPL', accessions=['0000320193-24-000123'], k_final=10,
)
found = any(c.sid_start <= 1131 <= c.sid_end for c in chunks)
print('q004 gold sid 1131 found:', found)
assert found

chunks = retrieve(
    conn, embedder,
    \"What were Apple's total gross margin percentages for Products and Services in the second quarter of fiscal 2024?\",
    ticker='AAPL', accessions=['0000320193-24-000069'], k_final=10,
)
found = any(c.sid_start <= 536 <= c.sid_end for c in chunks)
print('q009 gold sid 536 found:', found)
assert found
print('Both previously-missing gold sids now surface when accession-pinned.')
"
```

Expected output: both `found: True` lines and the final confirmation line,
with no `AssertionError`. Paste the full output in your report. If either
assertion fails, that is a real problem with Task 1's implementation or
with the accession values above — stop and report BLOCKED with the actual
output rather than adjusting the assertions to pass.

- [ ] **Step 5: Run the full suite, lint, and commit**

```bash
cd backend
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check evals/__main__.py
git add evals/__main__.py
git commit -m "feat: add the periods eval subcommand"
```

## Self-Review Notes

- **Spec coverage:** §3.1 (why accession-pinning) is design rationale
  reflected in `DETECTION_PERIOD_SYSTEM_PROMPT` and this plan's intro, no
  separate task needed. §3.2 (detection) → Task 3. §3.3 (retrieval) →
  Task 1. §4 (no migration) → confirmed, no task adds one. §5 (testing) →
  Task 4 (hand-labeled case set, including q004/q009 by name) and Task 5
  Step 4 (the direct retrieval verification). §6 (risks: wrong pin worse
  than no pin; hallucinated accession) → Task 3's `parse_detected_periods`
  validates every accession against the ticker's own filing list, dropping
  anything that doesn't match, exactly like `parse_detected_companies`
  does for tickers.
- **Placeholder scan:** none found; every step has runnable code.
- **Type consistency:** `PeriodDetector.detect(question: str, ticker: str, filings: list[dict]) -> list[str]`
  is the same signature across the Protocol (Task 3), `AnthropicPeriodDetector`
  (Task 3), and `StubPeriodDetector` (Task 4). `parse_detected_periods(raw: str, known_accessions: set[str]) -> list[str]`
  is defined once (Task 3) and consumed once (Task 3's own
  `AnthropicPeriodDetector.detect`), no signature drift. `queries.load_filings_for_ticker`'s
  return shape (`{accession, form_type, filing_date, period_end}`) matches
  exactly what `build_period_prompt` (Task 3) and the YAML case format
  (Task 4) both expect.
- **Cross-task ordering check:** Task 5 is the only task that imports from
  all of Tasks 1-4; Tasks 1-4 have no dependencies on each other's *code*
  (only on the shared shapes documented in each task's Interfaces block),
  so they could be reordered freely except that Task 5 must come last.
