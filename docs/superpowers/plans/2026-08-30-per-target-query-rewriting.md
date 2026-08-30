# Per-Target Query Rewriting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the diluted-retrieval-signal bug in multi-company questions by
rewriting each target company's search query to drop the other named
company's terms before that target's `retrieve()` call runs.

**Architecture:** A third detector, `QueryRewriter`, joins the existing
`CompanyDetector`/`PeriodDetector` pair in the detection layer. It follows
the exact same Protocol + defensive-parse + `Anthropic*` shape. It is wired
into `resolve_targets` (producing a per-`Target` `search_query`) and
consumed by `retrieve_for_targets` (using `search_query` in place of the
raw question for that target's retrieval call). Nothing about verification,
generation, or the chunk/sentence data model changes.

**Tech Stack:** Python 3.13, FastAPI, psycopg, pytest, `anthropic` SDK
(`>=0.120.2,<1`, already pinned in `pyproject.toml` — no change needed).

**Spec:** `docs/superpowers/specs/2026-08-30-per-target-query-rewriting-design.md`

## Global Constraints

- Python 3.13 only; no version-matrix changes.
- `ruff==0.16.1` is pinned exactly; `ruff check .` must stay green.
- The `anthropic` SDK is pinned `>=0.120.2,<1` in `pyproject.toml` — do not
  loosen or change this pin.
- Commit messages must contain **no AI attribution of any kind** — no
  Co-Authored-By, no Claude-Session trailer, no tool names.
- `MODEL` (from `api/generate.py`, currently `"claude-haiku-4-5"`) is the
  single source of truth for which model every detector uses — never
  hardcode a model string anywhere else.
- Every detector-style call that can fail (network, rate limit, malformed
  output) must degrade to a safe fallback rather than raising — wrap only
  the `.rewrite()` / `.detect()` call itself in `try/except Exception`,
  never the DB queries around it (a DB bug must surface as a real error,
  not be swallowed as "no result"). This project has twice previously
  shipped an over-broad `try` that wrapped a DB call by mistake — keep the
  `try` block to the single line that calls the detector.
- Any FastAPI dependency override in a test must be a **zero-arg lambda**
  (`lambda: StubX()`), never the bare class — a bare class with an optional
  constructor parameter breaks every `/ask` call with a 422, because
  FastAPI re-introspects the override's own signature. See the existing
  comment in `tests/test_app.py`'s `stubbed_client` fixture.
- Report full pytest output in every task report — a summarized-only
  "N passed" line is not sufficient evidence.
- `TEST_DATABASE_URL` must be set for any `@pytest.mark.db` test to run;
  those tests auto-skip otherwise. Do not treat a skip as a pass.

---

### Task 1: `QueryRewriter` module

**Files:**
- Create: `backend/src/api/rewrite.py`
- Test: `backend/tests/test_rewrite.py`

**Interfaces:**
- Consumes: `MODEL` from `backend/src/api/generate.py` (already exists,
  currently `"claude-haiku-4-5"`).
- Produces: `QueryRewriter` (Protocol), `parse_rewritten_query(raw: str,
  fallback: str) -> str`, `build_rewrite_prompt(question: str, ticker: str,
  company_name: str) -> str`, `REWRITE_SYSTEM_PROMPT: str`,
  `AnthropicQueryRewriter` (dataclass with `.rewrite(question, ticker,
  company_name) -> str`). Task 2 imports `QueryRewriter` from this module;
  Task 3 imports `AnthropicQueryRewriter`.

This task has no DB dependency and no dependency on any other task — it is
a self-contained module, following `backend/src/api/detect.py`'s existing
shape line for line. Read `detect.py` first to match its structure exactly
(it defines `CompanyDetector`/`PeriodDetector` the same way this task
defines `QueryRewriter`).

- [ ] **Step 1: Write the failing tests for `parse_rewritten_query`**

```python
# backend/tests/test_rewrite.py
from api.rewrite import build_rewrite_prompt, parse_rewritten_query

FALLBACK = "Who achieved more growth, Microsoft or Amazon?"


def test_parses_bare_json():
    raw = '{"query": "Microsoft revenue growth fiscal 2023 to fiscal 2024"}'
    assert (
        parse_rewritten_query(raw, FALLBACK)
        == "Microsoft revenue growth fiscal 2023 to fiscal 2024"
    )


def test_parses_fenced_json():
    raw = '```json\n{"query": "Amazon revenue growth fiscal 2023 to fiscal 2024"}\n```'
    assert (
        parse_rewritten_query(raw, FALLBACK)
        == "Amazon revenue growth fiscal 2023 to fiscal 2024"
    )


def test_malformed_json_falls_back():
    assert parse_rewritten_query('{"query": [oops}', FALLBACK) == FALLBACK


def test_no_json_object_falls_back():
    assert parse_rewritten_query("I'm not sure.", FALLBACK) == FALLBACK


def test_missing_query_field_falls_back():
    assert parse_rewritten_query('{"other": "x"}', FALLBACK) == FALLBACK


def test_non_string_query_field_falls_back():
    assert parse_rewritten_query('{"query": 123}', FALLBACK) == FALLBACK


def test_blank_query_falls_back():
    assert parse_rewritten_query('{"query": "   "}', FALLBACK) == FALLBACK


def test_query_is_stripped():
    assert (
        parse_rewritten_query('{"query": "  Microsoft revenue  "}', FALLBACK)
        == "Microsoft revenue"
    )


def test_rewrite_prompt_names_the_company_and_the_question():
    prompt = build_rewrite_prompt(
        "Who achieved more growth, Microsoft or Amazon?",
        "MSFT",
        "Microsoft Corporation",
    )
    assert "MSFT: Microsoft Corporation" in prompt
    assert "Who achieved more growth, Microsoft or Amazon?" in prompt
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_rewrite.py -v` (from `backend/`)
Expected: FAIL with `ModuleNotFoundError: No module named 'api.rewrite'`

- [ ] **Step 3: Write `backend/src/api/rewrite.py`**

```python
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

from .generate import MODEL

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class QueryRewriter(Protocol):
    """The LLM, narrowed to the one thing per-target query rewriting needs."""

    def rewrite(self, question: str, ticker: str, company_name: str) -> str:
        """Return a standalone, single-company version of `question`."""
        ...


def parse_rewritten_query(raw: str, fallback: str) -> str:
    """Defensive parse of a rewriter's raw response into a search query.

    Malformed JSON, no JSON object, a missing/non-string `query` field, or a
    blank query all degrade to `fallback` (the original question) -- a
    rewrite failure must never make retrieval worse than not rewriting at
    all.
    """
    text = _FENCE.sub("", raw.strip())
    match = _OBJECT.search(text)
    if match is None:
        return fallback
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return fallback
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        return fallback
    return query.strip()


REWRITE_SYSTEM_PROMPT = (
    """You rewrite a question about SEC filings into a standalone version
scoped to one company, for use as a search query.

You will be given one company (as "TICKER: Name") and the original
question, which may discuss more than one company. Rewrite the question to
be entirely about the given company: keep the financial concepts, metrics,
and time periods; drop every other named company.

Respond with strict JSON and nothing else:

{"query": "Microsoft revenue growth fiscal 2023 to fiscal 2024"}

Rules:
1. Keep the rewritten query focused on the financial concepts, metrics, and
   time periods from the original question.
2. Remove every company name or reference other than the one given.
3. Do not add any fact, number, or company not present in the original
   question.
"""
)


def build_rewrite_prompt(question: str, ticker: str, company_name: str) -> str:
    return f"Company: {ticker}: {company_name}\n\nQuestion: {question}"


@dataclass
class AnthropicQueryRewriter:
    model: str = MODEL
    max_tokens: int = 256
    api_key: str | None = None

    def rewrite(self, question: str, ticker: str, company_name: str) -> str:
        import anthropic

        client = anthropic.Anthropic(
            api_key=self.api_key or os.environ["ANTHROPIC_API_KEY"]
        )
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0,
            system=REWRITE_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": build_rewrite_prompt(question, ticker, company_name),
                }
            ],
        )
        raw = "".join(block.text for block in message.content if block.type == "text")
        return parse_rewritten_query(raw, question)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_rewrite.py -v`
Expected: PASS, all 9 tests. Paste the full `-v` output (not a summary) in
your report.

- [ ] **Step 5: Lint**

Run: `ruff check .` (from `backend/`)
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/rewrite.py backend/tests/test_rewrite.py
git commit -m "feat: add the per-target query rewriter"
```

---

### Task 2: Wire `QueryRewriter` into `resolve_targets` / `retrieve_for_targets`

**Files:**
- Modify: `backend/src/api/targets.py`
- Modify: `backend/tests/fakes.py`
- Modify: `backend/tests/test_targets.py`

**Interfaces:**
- Consumes: `QueryRewriter` (Protocol) from `backend/src/api/rewrite.py`
  (Task 1).
- Produces: `Target.search_query: str | None = None` field;
  `resolve_targets(..., query_rewriter: QueryRewriter | None = None)`;
  `retrieve_for_targets` now searches on `target.search_query or question`.
  Task 3 threads `query_rewriter` through `answer_stream`/`app.py` using
  this exact parameter name and default.

Read `backend/src/api/targets.py` and `backend/tests/test_targets.py` in
full before starting — this task edits both, and several existing
assertions change shape (see Step 3).

- [ ] **Step 1: Add `StubQueryRewriter` to `backend/tests/fakes.py`**

Append this class, matching `StubPeriodDetector`'s shape immediately above it:

```python
class StubQueryRewriter:
    """Returns a canned rewritten query per (question, ticker), for testing
    consumers of QueryRewriter without hitting the live API."""

    def __init__(self, answers: dict[tuple[str, str], str] | None = None):
        self.answers = answers or {}
        self.calls: list[tuple[str, str, str]] = []

    def rewrite(self, question, ticker, company_name):
        self.calls.append((question, ticker, company_name))
        return self.answers.get((question, ticker), question)
```

- [ ] **Step 2: Write the failing tests in `backend/tests/test_targets.py`**

First, update `fake_retrieve_calls`'s signature and every call site that
asserts on `calls` — the helper currently drops the `question` argument on
the floor, and this task needs to see it. Replace:

```python
def fake_retrieve_calls(monkeypatch, chunks_by_ticker):
    calls = []

    def fake_retrieve(
        conn, embedder, question, *, k_final, k_each=20, ticker=None, accessions=None,
        form_type=None,
    ):
        calls.append((ticker, accessions))
        return chunks_by_ticker.get(ticker, [])

    monkeypatch.setattr("api.targets.retrieve", fake_retrieve)
    return calls
```

with:

```python
def fake_retrieve_calls(monkeypatch, chunks_by_ticker):
    calls = []

    def fake_retrieve(
        conn, embedder, question, *, k_final, k_each=20, ticker=None, accessions=None,
        form_type=None,
    ):
        calls.append((question, ticker, accessions))
        return chunks_by_ticker.get(ticker, [])

    monkeypatch.setattr("api.targets.retrieve", fake_retrieve)
    return calls
```

Then update the two existing tests that assert on `calls` (both currently
expect 2-tuples; they now get 3-tuples with the question first):

```python
def test_no_targets_falls_back_to_a_single_unscoped_retrieve(monkeypatch):
    calls = fake_retrieve_calls(monkeypatch, {None: ["unscoped-chunk"]})
    chunks = retrieve_for_targets(None, None, "q", [], k_final=8)
    assert chunks == ["unscoped-chunk"]
    assert calls == [("q", None, None)]


def test_each_target_gets_its_own_full_k_final_and_results_are_concatenated(monkeypatch):
    calls = fake_retrieve_calls(monkeypatch, {"MSFT": ["msft-chunk"], "AMZN": ["amzn-chunk"]})
    targets = [Target("MSFT", None), Target("AMZN", ["ACC-A"])]
    chunks = retrieve_for_targets(None, None, "q", targets, k_final=8)
    assert chunks == ["msft-chunk", "amzn-chunk"]
    assert calls == [("q", "MSFT", None), ("q", "AMZN", ["ACC-A"])]
```

Now add the new tests. Add this import at the top of the file:

```python
from tests.fakes import StubCompanyDetector, StubPeriodDetector, StubQueryRewriter
```

And append these tests:

```python
def test_retrieve_for_targets_uses_a_targets_search_query_when_set(monkeypatch):
    calls = fake_retrieve_calls(monkeypatch, {"MSFT": ["msft-chunk"]})
    targets = [Target("MSFT", None, search_query="Microsoft revenue growth")]
    retrieve_for_targets(None, None, "original question", targets, k_final=8)
    assert calls == [("Microsoft revenue growth", "MSFT", None)]


def test_retrieve_for_targets_falls_back_to_the_question_when_search_query_is_none(
    monkeypatch,
):
    calls = fake_retrieve_calls(monkeypatch, {"MSFT": ["msft-chunk"]})
    targets = [Target("MSFT", None)]
    retrieve_for_targets(None, None, "original question", targets, k_final=8)
    assert calls == [("original question", "MSFT", None)]


def test_query_rewriter_fires_only_when_multiple_targets_are_resolved(monkeypatch):
    monkeypatch.setattr(
        "api.targets.queries.load_companies",
        lambda conn: [
            {"ticker": "MSFT", "name": "Microsoft Corporation"},
            {"ticker": "AMZN", "name": "Amazon.com, Inc."},
        ],
    )
    rewriter = StubQueryRewriter(
        {
            ("q", "MSFT"): "Microsoft revenue growth",
            ("q", "AMZN"): "Amazon revenue growth",
        }
    )
    targets = resolve_targets(
        None,
        "q",
        explicit_tickers=["MSFT", "AMZN"],
        company_detector=StubCompanyDetector(),
        query_rewriter=rewriter,
    )
    assert targets == [
        Target("MSFT", None, search_query="Microsoft revenue growth"),
        Target("AMZN", None, search_query="Amazon revenue growth"),
    ]
    assert set(rewriter.calls) == {
        ("q", "MSFT", "Microsoft Corporation"),
        ("q", "AMZN", "Amazon.com, Inc."),
    }


def test_query_rewriter_does_not_fire_for_a_single_target():
    rewriter = StubQueryRewriter()
    targets = resolve_targets(
        None,
        "q",
        explicit_tickers=["MSFT"],
        company_detector=StubCompanyDetector(),
        query_rewriter=rewriter,
    )
    assert targets == [Target("MSFT", None)]
    assert rewriter.calls == []


def test_query_rewriter_failure_degrades_to_no_search_query(monkeypatch):
    monkeypatch.setattr(
        "api.targets.queries.load_companies",
        lambda conn: [
            {"ticker": "MSFT", "name": "Microsoft Corporation"},
            {"ticker": "AMZN", "name": "Amazon.com, Inc."},
        ],
    )

    class BoomQueryRewriter:
        def rewrite(self, question, ticker, company_name):
            raise RuntimeError("rate limited")

    targets = resolve_targets(
        None,
        "q",
        explicit_tickers=["MSFT", "AMZN"],
        company_detector=StubCompanyDetector(),
        query_rewriter=BoomQueryRewriter(),
    )
    assert targets == [Target("MSFT", None), Target("AMZN", None)]


def test_no_query_rewriter_leaves_search_query_none():
    targets = resolve_targets(
        None,
        "q",
        explicit_tickers=["MSFT", "AMZN"],
        company_detector=StubCompanyDetector(),
    )
    assert targets == [Target("MSFT", None), Target("AMZN", None)]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_targets.py -v` (from `backend/`)
Expected: FAIL — `Target(...)` does not accept `search_query`,
`resolve_targets()` does not accept `query_rewriter`, and the two updated
assertions fail against the current 2-tuple `calls` shape.

- [ ] **Step 4: Modify `backend/src/api/targets.py`**

Add the import:

```python
from .rewrite import QueryRewriter
```

Replace the `Target` dataclass:

```python
@dataclass(frozen=True)
class Target:
    ticker: str
    accessions: list[str] | None  # None if fiscal period handling abstained or isn't wired in
    search_query: str | None = None  # None => use the original question, unchanged
```

Replace `retrieve_for_targets`'s body so the per-target call uses
`target.search_query or question` instead of the bare `question`:

```python
def retrieve_for_targets(
    conn,
    embedder,
    question: str,
    targets: list[Target],
    *,
    k_final: int = 8,
    k_each: int = 20,
    form_type: str | None = None,
) -> list[RetrievedChunk]:
    """One retrieve() call per target, concatenated -- never re-fused.

    Re-fusing all targets' results back into one shared ranking would
    reproduce the exact bug this module exists to fix: whichever company's
    chunks score higher would still crowd out the other. Concatenating
    guarantees every named target is represented, independent of how the
    arms compare to each other. 0 targets is exactly today's single
    unscoped retrieve() call -- zero regression risk when nothing was
    detected.
    """
    if not targets:
        return retrieve(
            conn, embedder, question, k_final=k_final, k_each=k_each, form_type=form_type
        )
    chunks: list[RetrievedChunk] = []
    for target in targets:
        chunks.extend(
            retrieve(
                conn,
                embedder,
                target.search_query or question,
                k_final=k_final,
                k_each=k_each,
                ticker=target.ticker,
                accessions=target.accessions,
                form_type=form_type,
            )
        )
    return chunks
```

Replace `resolve_targets`:

```python
def resolve_targets(
    conn,
    question: str,
    *,
    explicit_tickers: list[str] | None,
    company_detector: CompanyDetector,
    period_detector: PeriodDetector | None = None,
    query_rewriter: QueryRewriter | None = None,
) -> list[Target]:
    """Decide which companies (and, if available, which of their filings) a
    question means.

    Explicit tickers are normalized (uppercased, deduped, capped) but never
    dropped for being unknown -- an explicit filter for a ticker that
    doesn't exist must retrieve nothing, matching the ticker filter's
    existing behavior, not silently fall back to every company. Only
    LLM-detected tickers are validated against the known company list
    (inside parse_detected_companies), since only they carry a real
    hallucination risk.

    A detector failure (network error, missing key, rate limit) degrades to
    "no targets" / "no accessions" / "no search_query" rather than
    propagating -- this is the guarantee entity resolution, fiscal period
    handling, and per-target query rewriting all defer to this function; a
    broken detector must never turn a working /ask request into a failure.
    """
    if explicit_tickers:
        tickers: list[str] = []
        for ticker in explicit_tickers:
            upper = ticker.strip().upper()
            if not upper:
                continue
            if upper not in tickers:
                tickers.append(upper)
            if len(tickers) == MAX_COMPANIES:
                break
    else:
        companies = queries.load_companies(conn)
        try:
            tickers = company_detector.detect(question, companies)
        except Exception as exc:  # noqa: BLE001 -- detector failure degrades to no targets
            logger.warning("company_detector.detect failed, degrading to no targets: %s", exc)
            tickers = []
        tickers = tickers[:MAX_COMPANIES]

    # A rewritten query only helps a comparison-phrased question -- a single
    # target has no other company's terms to strip, so this never fires for
    # the common single-company case.
    name_by_ticker: dict[str, str] = {}
    if query_rewriter is not None and len(tickers) > 1:
        name_by_ticker = {c["ticker"]: c["name"] for c in queries.load_companies(conn)}

    targets = []
    for ticker in tickers:
        accessions = None
        if period_detector is not None:
            filings = queries.load_filings_for_ticker(conn, ticker)
            if filings:
                try:
                    accessions = period_detector.detect(question, ticker, filings) or None
                except Exception as exc:  # noqa: BLE001 -- same degrade-safely rationale
                    logger.warning(
                        "period_detector.detect failed for %s, degrading to no accessions: %s",
                        ticker, exc,
                    )
                    accessions = None
        search_query = None
        if query_rewriter is not None and len(tickers) > 1:
            company_name = name_by_ticker.get(ticker, ticker)
            try:
                search_query = query_rewriter.rewrite(question, ticker, company_name)
            except Exception as exc:  # noqa: BLE001 -- same degrade-safely rationale
                logger.warning(
                    "query_rewriter.rewrite failed for %s, using the original question: %s",
                    ticker, exc,
                )
                search_query = None
        targets.append(
            Target(ticker=ticker, accessions=accessions, search_query=search_query)
        )
    return targets
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_targets.py -v`
Expected: PASS, all tests (existing + new). Paste the full `-v` output in
your report.

- [ ] **Step 6: Run the full backend test suite**

Run: `pytest -v` (from `backend/`, with `TEST_DATABASE_URL` set)
Expected: PASS, no regressions elsewhere. Paste the full output.

- [ ] **Step 7: Lint**

Run: `ruff check .`
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add backend/src/api/targets.py backend/tests/fakes.py backend/tests/test_targets.py
git commit -m "feat: wire per-target query rewriting into resolve_targets"
```

---

### Task 3: Thread `query_rewriter` through `answer_stream` and `/ask`

**Files:**
- Modify: `backend/src/api/answer.py`
- Modify: `backend/src/api/app.py`
- Modify: `backend/tests/test_app.py`

**Interfaces:**
- Consumes: `QueryRewriter`/`AnthropicQueryRewriter` from
  `backend/src/api/rewrite.py` (Task 1); `resolve_targets(...,
  query_rewriter=...)` from `backend/src/api/targets.py` (Task 2).
- Produces: `answer_stream(..., query_rewriter: QueryRewriter | None =
  None)`; `app.get_query_rewriter()` DI provider.

- [ ] **Step 1: Modify `backend/src/api/answer.py`**

Add the import:

```python
from .rewrite import QueryRewriter
```

Update `answer_stream`'s signature and its `resolve_targets` call:

```python
def answer_stream(
    conn: psycopg.Connection,
    embedder,
    generator: Generator,
    company_detector: CompanyDetector,
    question: str,
    *,
    tickers: list[str] | None = None,
    form_type: str | None = None,
    k_final: int = 8,
    period_detector: PeriodDetector | None = None,
    query_rewriter: QueryRewriter | None = None,
) -> Iterator[AnswerEvent]:
    """The query path (design §6): resolve targets -> retrieve -> generate -> verify -> stream."""
    try:
        targets = resolve_targets(
            conn,
            question,
            explicit_tickers=tickers,
            company_detector=company_detector,
            period_detector=period_detector,
            query_rewriter=query_rewriter,
        )
```

(The rest of the function is unchanged — leave everything from `chunks =
retrieve_for_targets(...)` onward exactly as it is.)

- [ ] **Step 2: Modify `backend/src/api/app.py`**

Add the import:

```python
from .rewrite import AnthropicQueryRewriter, QueryRewriter
```

Add the DI provider, next to `get_period_detector`:

```python
def get_query_rewriter() -> QueryRewriter:
    return AnthropicQueryRewriter()
```

Update `ask()`'s signature and its `answer_stream` call:

```python
@app.post("/ask")
def ask(
    request: AskRequest,
    embedder: Embedder = Depends(get_embedder),
    generator: Generator = Depends(get_generator),
    company_detector: CompanyDetector = Depends(get_company_detector),
    period_detector: PeriodDetector = Depends(get_period_detector),
    query_rewriter: QueryRewriter = Depends(get_query_rewriter),
) -> StreamingResponse:
    # The plural filter wins; a lone legacy singular `ticker` is wrapped into
    # a one-element list so existing single-ticker API callers keep working.
    tickers = request.filters.tickers or (
        [request.filters.ticker] if request.filters.ticker else None
    )

    def events() -> Iterator[str]:
        # A connection per request; pooling is a Phase 5 concern.
        with db.connect() as conn:
            for event in answer_stream(
                conn,
                embedder,
                generator,
                company_detector,
                request.question,
                tickers=tickers,
                form_type=request.filters.form_type,
                period_detector=period_detector,
                query_rewriter=query_rewriter,
            ):
                yield sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 3: Modify `backend/tests/test_app.py`**

Update the import line:

```python
from tests.fakes import (
    FakeEmbedder,
    StubCompanyDetector,
    StubGenerator,
    StubPeriodDetector,
    StubQueryRewriter,
)
```

In the `stubbed_client` fixture, add the override alongside the existing
two detector overrides (same zero-arg-lambda reasoning already documented
in the comment directly above them — do not remove or shorten that
comment, it explains why a bare class is unsafe here):

```python
    app.dependency_overrides[app_module.get_query_rewriter] = (
        lambda: StubQueryRewriter()  # noqa: PLW0108
    )
```

- [ ] **Step 4: Run the full backend test suite**

Run: `pytest -v` (from `backend/`, with `TEST_DATABASE_URL` set)
Expected: PASS, no regressions. Paste the full output.

- [ ] **Step 5: Lint**

Run: `ruff check .`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/src/api/answer.py backend/src/api/app.py backend/tests/test_app.py
git commit -m "feat: thread the query rewriter through answer_stream and /ask"
```

---

### Task 4: Wire `query_rewriter` into the eval harness

**Files:**
- Modify: `backend/evals/harness.py`
- Modify: `backend/evals/__main__.py`

**Interfaces:**
- Consumes: `AnthropicQueryRewriter` from `backend/src/api/rewrite.py`
  (Task 1); `resolve_targets(..., query_rewriter=...)` from
  `backend/src/api/targets.py` (Task 2).
- Produces: `run_retrieval_eval(..., query_rewriter=None)`.

There is no existing pytest file for `harness.py` or `__main__.py` (they
are exercised by actually running the eval CLI, not by unit tests — check
`backend/evals/` for yourself; this is consistent with how every prior
change to this file was verified in this project). This task's own
verification step is Step 3 below: an actual `evals run` invocation, not a
pytest run.

- [ ] **Step 1: Modify `backend/evals/harness.py`**

Update `_score_targeted`'s signature and its `resolve_targets` call:

```python
def _score_targeted(
    conn, embedder, questions, *, ks, k_each: int = 20,
    company_detector, period_detector=None, query_rewriter=None,
) -> dict:
    """Recall over the real resolve_targets + retrieve_for_targets pipeline.

    Grouped by `group` so a comparison question's sibling rows share one
    resolution + retrieval call (matching what /ask actually does for one
    incoming question) rather than re-resolving per row.
    """
    top_k = max(ks)
    hits_at = {k: 0 for k in ks}
    misses_at_top: list[str] = []
    chunks_by_group: dict[str, list[RetrievedChunk]] = {}
    for question in questions:
        if question.group not in chunks_by_group:
            targets = resolve_targets(
                conn,
                question.question,
                explicit_tickers=None,
                company_detector=company_detector,
                period_detector=period_detector,
                query_rewriter=query_rewriter,
            )
            chunks_by_group[question.group] = retrieve_for_targets(
                conn, embedder, question.question, targets, k_final=top_k, k_each=k_each,
            )
        chunks = chunks_by_group[question.group]
        for k in ks:
            if hit(chunks[:k], question.accession, question.gold_sids):
                hits_at[k] += 1
        if not hit(chunks, question.accession, question.gold_sids):
            misses_at_top.append(question.id)
    n = len(questions)
    scores: dict = {
        f"targeted_recall@{k}": round(hits_at[k] / n, 4) if n else 0.0 for k in ks
    }
    scores[f"targeted_misses@{top_k}"] = misses_at_top
    return scores
```

Update `run_retrieval_eval`'s signature and its `_score_targeted` call:

```python
def run_retrieval_eval(
    conn,
    embedder,
    questions,
    *,
    ks=(5, 10),
    k_each: int = 20,
    company_detector=None,
    period_detector=None,
    query_rewriter=None,
) -> dict:
    """Score retrieval on up to three arms: scoped, unfiltered, and targeted.

    The scoped arm measures the retriever itself, and is what the unprefixed
    keys have always meant -- on the single-company corpus these numbers were
    first taken against, the two arms were identical by construction. The
    unfiltered arm measures what a user gets when they leave the ticker filter
    empty on /ask, where every other filer's boilerplate competes for the same
    ten slots. The targeted arm (only computed when `company_detector` is
    given, since it makes a live LLM call) measures the real query-decomposition
    pipeline: does resolve_targets + retrieve_for_targets actually fix what
    unfiltered gets wrong. `query_rewriter` is optional and only changes
    what happens inside that targeted arm.
    """
    metrics: dict = {"questions": len(questions), "k_each": k_each}
    metrics |= _score(conn, embedder, questions, ks=ks, k_each=k_each, scoped=True)
    unfiltered = _score(conn, embedder, questions, ks=ks, k_each=k_each, scoped=False)
    metrics |= {f"unfiltered_{key}": value for key, value in unfiltered.items()}
    if company_detector is not None:
        metrics |= _score_targeted(
            conn,
            embedder,
            questions,
            ks=ks,
            k_each=k_each,
            company_detector=company_detector,
            period_detector=period_detector,
            query_rewriter=query_rewriter,
        )
    return metrics
```

- [ ] **Step 2: Modify `backend/evals/__main__.py`**

In `cmd_run`, update the import and construction of the detectors, and the
`run_retrieval_eval` call:

```python
        from api.detect import AnthropicCompanyDetector, AnthropicPeriodDetector
        from api.rewrite import AnthropicQueryRewriter

        company_detector = AnthropicCompanyDetector()
        period_detector = AnthropicPeriodDetector()
        query_rewriter = AnthropicQueryRewriter()
        metrics = harness.run_retrieval_eval(
            conn,
            embedder,
            questions,
            company_detector=company_detector,
            period_detector=period_detector,
            query_rewriter=query_rewriter,
        )
```

Leave the rest of `cmd_run` (the faithfulness-eval branch below it)
unchanged — `run_faithfulness_eval` always scopes each question to its own
single ticker, so `resolve_targets` never sees more than one target there
and `query_rewriter` would never fire; there is no reason to thread it
through a call path where it can have no effect.

- [ ] **Step 3: Run the eval CLI once to confirm the new wiring executes**

Run (from `backend/`, with `TEST_DATABASE_URL`/`DATABASE_URL` and
`ANTHROPIC_API_KEY` set — this makes real, low-volume LLM calls, matching
how every eval run in this project already works):

```bash
python -m evals run --retrieval-only
```

Expected: completes without a traceback and prints the usual metric lines,
including `targeted_recall@5`/`targeted_recall@10`. Paste the full output
in your report. Do **not** treat the specific numbers as a pass/fail gate
for this task — comparing them against a pre-change baseline is the
controller's job at final review (per the spec's §5 Rollout), not this
task's.

- [ ] **Step 4: Lint**

Run: `ruff check .`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add backend/evals/harness.py backend/evals/__main__.py
git commit -m "feat: thread the query rewriter into the retrieval eval harness"
```

---

## Final Rollout Verification (controller, after all tasks pass final review)

Per the spec's §5 Rollout, before considering this branch done:

1. Update `docs/design.md`: §6's latency note ("up to ~5 sequential Haiku
   calls") should reflect the new worst case (up to ~9: 1 company-detect
   call, up to 4 period-detect calls, up to 4 query-rewrite calls), and
   §14's query-decomposition entry should note that this branch closes the
   `qc001`/`qc002` retrieval-ranking gap it left open, linking
   `docs/superpowers/specs/2026-08-30-per-target-query-rewriting-design.md`.
   Commit this doc-only change separately from the eval-results commit
   below.
2. Check out this branch fresh (or use the worktree it was built in) and
   run `python -m evals run --retrieval-only` once to get an "after"
   reading.
3. Compare `qc001`/`qc002`'s presence in `targeted_misses@10` against the
   most recent pre-this-branch row in `backend/evals/results.jsonl` (the
   "before" reading). Because `gold_sid_hit_rate`-style eval metrics are
   already documented to vary run-to-run in this project, do not rely on a
   single run either side — run it twice per side if the first comparison
   is ambiguous.
4. Acceptance: `qc001`/`qc002` improve (ideally leave `targeted_misses@10`)
   without the 16 pre-existing questions' `targeted_recall@10` regressing.
5. Append the result to `backend/evals/results.jsonl` via the harness's own
   `append_results` (this happens automatically as part of `evals run` —
   just don't discard the run).
6. If `qc001`/`qc002` still miss after this change: that is real evidence
   for the spec's documented fallback (reranking, `design.md` §14 backlog)
   rather than a sign this branch did something wrong — say so plainly
   rather than re-pinning the golden set to manufacture a pass, matching
   this project's established practice of not chasing eval scores by
   moving the target.
