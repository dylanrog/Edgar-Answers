# Query Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/ask` actually answer multi-company questions correctly by
turning entity resolution's detected companies (and, when confident, fiscal
period handling's detected filings) into one retrieval call per target and
merging the results — fixing the reproduced bug where "Who achieved more
growth from 23 to 24? Microsoft or Amazon?" retrieved 8/8 Microsoft chunks
and 0 Amazon.

**Architecture:** A new `api/targets.py` holds the `Target` shape and two
functions: `resolve_targets` (decide which companies/filings a question
means — explicit filter, else the entity-resolution detector, optionally
refined by the fiscal-period detector) and `retrieve_for_targets` (run
`retrieve()` once per target and concatenate — never re-fuse, since re-fusing
is exactly what caused the original bug). `answer_stream` swaps its single
`retrieve()` call for this pair; `app.py` gains a `tickers` filter and
detector dependency injection; the frontend's ticker `<select>` becomes a
multi-select; `evals/golden.yaml` gains sibling comparison rows and
`harness.py` gains a third `targeted_*` scoring arm that exercises the whole
pipeline end to end, including the two previously-failing period-confusion
questions (q004, q009) from the sibling fiscal-period-handling branch.

**Tech Stack:** Python 3.13, TypeScript/Next.js, `anthropic` (already a
dependency), `pytest`, `pyyaml`, `vitest`.

**Spec:** `docs/superpowers/specs/2026-08-29-query-decomposition-design.md`

## Global Constraints

- Python 3.13 everywhere; no new dependencies.
- Model is `claude-haiku-4-5` (already the default in `AnthropicCompanyDetector`/
  `AnthropicPeriodDetector`) at `temperature=0` — this plan does not touch
  either detector's internals, only composes them.
- `ruff==0.16.1` pinned exactly; `anthropic>=0.120.2,<1` in `pyproject.toml`
  — neither touched by this plan.
- No database migration needed.
- Commit messages: no AI attribution of any kind — hard project rule.
- Detector calls in `resolve_targets` must degrade safely on any exception
  (network error, missing API key, rate limit) to "no targets" / "no
  accessions" rather than raising — this is the guarantee both sibling specs
  explicitly deferred to this plan (see entity-resolution spec §3.4 and
  fiscal-period-handling spec §3.2). A broken detector must never turn a
  working `/ask` request into a 500 or an `error` SSE event.
- **Explicit ticker filters are normalized (uppercased, deduped, capped at
  `MAX_COMPANIES` from `api.detect`) but never dropped for being unknown.**
  This is a deliberate correction to a literal reading of the spec's
  "validated against the known ticker set" language: `backend/tests/test_app.py
  ::test_ask_passes_filters_through` already pins that an unknown explicit
  ticker (`"NOPE"`) must retrieve **zero** chunks, not silently fall back to
  every company. Only LLM-detected tickers are validated against the known
  company list (inside `parse_detected_companies`, already built) — only
  they carry a real hallucination risk. Do not "fix" this by making
  `resolve_targets` drop unknown explicit tickers; that would break the
  pinned test and silently widen scope for a typo'd filter.
- Concatenation, never RRF re-fusion, when merging per-target results —
  re-fusing would reproduce the exact crowding bug this plan fixes.

---

### Task 1: `Target` and `retrieve_for_targets`

**Files:**
- Create: `backend/src/api/targets.py`
- Create: `backend/tests/test_targets.py`

**Interfaces:**
- Produces: `api.targets.Target` (frozen dataclass: `ticker: str`,
  `accessions: list[str] | None`), `api.targets.retrieve_for_targets(conn, embedder, question, targets: list[Target], *, k_final: int = 8, form_type: str | None = None) -> list[RetrievedChunk]`.
- Consumes: `api.retrieval.retrieve` (existing).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_targets.py
from api.targets import Target, retrieve_for_targets


def fake_retrieve_calls(monkeypatch, chunks_by_ticker):
    calls = []

    def fake_retrieve(
        conn, embedder, question, *, k_final, ticker=None, accessions=None, form_type=None
    ):
        calls.append((ticker, accessions))
        return chunks_by_ticker.get(ticker, [])

    monkeypatch.setattr("api.targets.retrieve", fake_retrieve)
    return calls


def test_no_targets_falls_back_to_a_single_unscoped_retrieve(monkeypatch):
    calls = fake_retrieve_calls(monkeypatch, {None: ["unscoped-chunk"]})
    chunks = retrieve_for_targets(None, None, "q", [], k_final=8)
    assert chunks == ["unscoped-chunk"]
    assert calls == [(None, None)]


def test_each_target_gets_its_own_full_k_final_and_results_are_concatenated(monkeypatch):
    calls = fake_retrieve_calls(monkeypatch, {"MSFT": ["msft-chunk"], "AMZN": ["amzn-chunk"]})
    targets = [Target("MSFT", None), Target("AMZN", ["ACC-A"])]
    chunks = retrieve_for_targets(None, None, "q", targets, k_final=8)
    assert chunks == ["msft-chunk", "amzn-chunk"]
    assert calls == [("MSFT", None), ("AMZN", ["ACC-A"])]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_targets.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.targets'`

- [ ] **Step 3: Write the minimal implementation**

```python
# backend/src/api/targets.py
from __future__ import annotations

from dataclasses import dataclass

from .retrieval import RetrievedChunk, retrieve


@dataclass(frozen=True)
class Target:
    ticker: str
    accessions: list[str] | None  # None if fiscal period handling abstained or isn't wired in


def retrieve_for_targets(
    conn,
    embedder,
    question: str,
    targets: list[Target],
    *,
    k_final: int = 8,
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
        return retrieve(conn, embedder, question, k_final=k_final, form_type=form_type)
    chunks: list[RetrievedChunk] = []
    for target in targets:
        chunks.extend(
            retrieve(
                conn,
                embedder,
                question,
                k_final=k_final,
                ticker=target.ticker,
                accessions=target.accessions,
                form_type=form_type,
            )
        )
    return chunks
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_targets.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite and lint, then commit**

```bash
cd backend
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/api/targets.py tests/test_targets.py
git add src/api/targets.py tests/test_targets.py
git commit -m "feat: add Target and retrieve_for_targets"
```

---

### Task 2: `resolve_targets`

**Files:**
- Modify: `backend/src/api/targets.py`
- Modify: `backend/tests/test_targets.py`

**Interfaces:**
- Consumes: `api.detect.CompanyDetector`, `api.detect.PeriodDetector`,
  `api.detect.MAX_COMPANIES`, `api.queries.load_companies`,
  `api.queries.load_filings_for_ticker` (all existing).
- Produces: `api.targets.resolve_targets(conn, question: str, *, explicit_tickers: list[str] | None, company_detector: CompanyDetector, period_detector: PeriodDetector | None = None) -> list[Target]`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_targets.py  (append)
from api.targets import resolve_targets
from tests.fakes import StubCompanyDetector, StubPeriodDetector


def test_explicit_tickers_are_normalized_but_never_dropped_for_being_unknown():
    targets = resolve_targets(
        None,
        "irrelevant",
        explicit_tickers=["msft", "NOPE", "msft"],
        company_detector=StubCompanyDetector(),
    )
    assert targets == [Target("MSFT", None), Target("NOPE", None)]


def test_explicit_tickers_are_capped_at_max_companies():
    targets = resolve_targets(
        None,
        "irrelevant",
        explicit_tickers=["A", "B", "C", "D", "E"],
        company_detector=StubCompanyDetector(),
    )
    assert [t.ticker for t in targets] == ["A", "B", "C", "D"]


def test_no_explicit_tickers_runs_the_company_detector(monkeypatch):
    monkeypatch.setattr(
        "api.targets.queries.load_companies", lambda conn: [{"ticker": "MSFT"}]
    )
    detector = StubCompanyDetector({"how did msft do": ["MSFT"]})
    targets = resolve_targets(
        None, "how did msft do", explicit_tickers=None, company_detector=detector
    )
    assert targets == [Target("MSFT", None)]
    assert detector.calls == ["how did msft do"]


def test_company_detector_failure_degrades_to_no_targets(monkeypatch):
    monkeypatch.setattr("api.targets.queries.load_companies", lambda conn: [])

    class BoomDetector:
        def detect(self, question, companies):
            raise RuntimeError("network down")

    targets = resolve_targets(
        None, "q", explicit_tickers=None, company_detector=BoomDetector()
    )
    assert targets == []


def test_period_detector_fills_in_accessions_per_ticker(monkeypatch):
    monkeypatch.setattr(
        "api.targets.queries.load_filings_for_ticker",
        lambda conn, ticker: [{"accession": f"ACC-{ticker}"}],
    )
    period_detector = StubPeriodDetector({"q": ["ACC-MSFT"]})
    targets = resolve_targets(
        None,
        "q",
        explicit_tickers=["MSFT"],
        company_detector=StubCompanyDetector(),
        period_detector=period_detector,
    )
    assert targets == [Target("MSFT", ["ACC-MSFT"])]


def test_period_detector_failure_degrades_to_no_accessions(monkeypatch):
    monkeypatch.setattr(
        "api.targets.queries.load_filings_for_ticker",
        lambda conn, ticker: [{"accession": "ACC-1"}],
    )

    class BoomPeriodDetector:
        def detect(self, question, ticker, filings):
            raise RuntimeError("rate limited")

    targets = resolve_targets(
        None,
        "q",
        explicit_tickers=["MSFT"],
        company_detector=StubCompanyDetector(),
        period_detector=BoomPeriodDetector(),
    )
    assert targets == [Target("MSFT", None)]


def test_no_period_detector_leaves_accessions_none():
    targets = resolve_targets(
        None, "q", explicit_tickers=["MSFT"], company_detector=StubCompanyDetector()
    )
    assert targets == [Target("MSFT", None)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_targets.py -k resolve_targets -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_targets'`

- [ ] **Step 3: Write the minimal implementation**

```python
# backend/src/api/targets.py  (append)
from . import queries
from .detect import MAX_COMPANIES, CompanyDetector, PeriodDetector


def resolve_targets(
    conn,
    question: str,
    *,
    explicit_tickers: list[str] | None,
    company_detector: CompanyDetector,
    period_detector: PeriodDetector | None = None,
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
    "no targets" / "no accessions" rather than propagating -- this is the
    guarantee entity resolution and fiscal period handling both deferred to
    this function; a broken detector must never turn a working /ask request
    into a failure.
    """
    if explicit_tickers:
        tickers: list[str] = []
        for ticker in explicit_tickers:
            upper = ticker.upper()
            if upper not in tickers:
                tickers.append(upper)
            if len(tickers) == MAX_COMPANIES:
                break
    else:
        try:
            tickers = company_detector.detect(question, queries.load_companies(conn))
        except Exception:  # noqa: BLE001 -- detector failure degrades to no targets
            tickers = []

    targets = []
    for ticker in tickers:
        accessions = None
        if period_detector is not None:
            try:
                filings = queries.load_filings_for_ticker(conn, ticker)
                accessions = period_detector.detect(question, ticker, filings) or None
            except Exception:  # noqa: BLE001 -- same degrade-safely rationale
                accessions = None
        targets.append(Target(ticker=ticker, accessions=accessions))
    return targets
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_targets.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the full suite and lint, then commit**

```bash
cd backend
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/api/targets.py tests/test_targets.py
git add src/api/targets.py tests/test_targets.py
git commit -m "feat: add resolve_targets"
```

---

### Task 3: Wire `answer_stream`

**Files:**
- Modify: `backend/src/api/answer.py`
- Modify: `backend/tests/test_answer.py`

**Interfaces:**
- Consumes: `api.targets.Target`, `api.targets.resolve_targets`,
  `api.targets.retrieve_for_targets` (Tasks 1-2), `api.detect.CompanyDetector`,
  `api.detect.PeriodDetector` (existing).
- Produces: `answer_stream`'s new signature —
  `answer_stream(conn, embedder, generator: Generator, company_detector: CompanyDetector, question: str, *, tickers: list[str] | None = None, form_type: str | None = None, k_final: int = 8, period_detector: PeriodDetector | None = None) -> Iterator[AnswerEvent]`.
  Note `ticker` (singular) is replaced by `tickers` (plural, `list[str] | None`)
  and `company_detector` becomes a new required positional parameter, inserted
  after `generator` and before `question` — every existing call site must add
  an argument, not just adjust a keyword.

**⚠️ Breaking signature change — read before starting:** this task changes
`answer_stream`'s parameter list in a way that breaks every existing call
site (7 of them, all in `test_answer.py`). Update every one in this same
task; do not leave any red.

- [ ] **Step 1: Update the existing tests for the new signature (still red until Step 3)**

```python
# backend/tests/test_answer.py
# Change the import line near the top from:
#   from tests.fakes import FakeEmbedder, StubGenerator
# to:
from tests.fakes import FakeEmbedder, StubCompanyDetector, StubGenerator

# Change collect() to:
def collect(conn, *responses):
    generator = StubGenerator(*responses)
    events = list(
        answer_stream(
            conn,
            FakeEmbedder(),
            generator,
            StubCompanyDetector(),
            "What were total net sales?",
        )
    )
    return events, generator

# Change test_generator_failure_becomes_an_error_event's call from:
#   answer_stream(seeded_conn, FakeEmbedder(), Boom(), "What were net sales?")
# to:
    events = list(
        answer_stream(
            seeded_conn,
            FakeEmbedder(),
            Boom(),
            StubCompanyDetector(),
            "What were net sales?",
        )
    )
```

Every other test in the file calls `collect(...)`, not `answer_stream(...)`
directly, so only these two call sites need editing.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_answer.py -v`
Expected: FAIL — `answer_stream()` still has the old signature, so passing
`StubCompanyDetector()` as a positional argument either raises a `TypeError`
(too many positional args) or is silently absorbed by the wrong parameter.
Either way, this step's tests must fail before Step 3.

- [ ] **Step 3: Rewrite `answer_stream`**

```python
# backend/src/api/answer.py
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import psycopg

from . import queries
from .detect import CompanyDetector, PeriodDetector
from .generate import (
    SYSTEM_PROMPT,
    AnswerSplitter,
    Generator,
    build_user_message,
    parse_citations,
)
from .targets import resolve_targets, retrieve_for_targets
from .verify import VerifiedCitation, verify_citation


@dataclass(frozen=True)
class AnswerEvent:
    name: str  # token | citation | done | error
    data: dict


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
) -> Iterator[AnswerEvent]:
    """The query path (design §6): resolve targets -> retrieve -> generate -> verify -> stream."""
    try:
        targets = resolve_targets(
            conn,
            question,
            explicit_tickers=tickers,
            company_detector=company_detector,
            period_detector=period_detector,
        )
        chunks = retrieve_for_targets(
            conn, embedder, question, targets, k_final=k_final, form_type=form_type
        )
        user_message = build_user_message(question, chunks)

        # Design §6.2: one retry if the trailing block does not parse, then
        # render the answer with an "unverified answer" notice rather than
        # failing the request outright. The retry's prose is discarded -- the
        # first attempt's answer has already streamed to the client, and
        # replacing it mid-stream would be worse than keeping it. We are
        # re-rolling only for the citation block.
        splitter = AnswerSplitter()
        citations = None
        for attempt in range(2):
            splitter = AnswerSplitter()
            for delta in generator.stream(SYSTEM_PROMPT, user_message):
                text = splitter.feed(delta)
                if text and attempt == 0:
                    yield AnswerEvent("token", {"text": text})
            tail = splitter.finish()
            if tail and attempt == 0:
                yield AnswerEvent("token", {"text": tail})
            citations = parse_citations(splitter.raw)
            if citations is not None:
                break

        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        verified: list[VerifiedCitation] = []
        for citation in citations or []:
            chunk = by_id.get(citation.chunk_id)
            if chunk is None:
                # The model cited a chunk it was never shown. Unverifiable by
                # construction -- surface it rather than dropping it.
                verified.append(
                    VerifiedCitation(
                        marker=citation.marker,
                        chunk_id=citation.chunk_id,
                        quote=citation.quote,
                        verified=False,
                        accession="",
                        sids=[],
                    )
                )
                continue
            sentences = queries.load_chunk_sentences(
                conn, chunk.filing_id, chunk.sid_start, chunk.sid_end
            )
            verified.append(verify_citation(citation, chunk, sentences))

        for citation in verified:
            yield AnswerEvent(
                "citation",
                {
                    "marker": citation.marker,
                    "verified": citation.verified,
                    "accession": citation.accession,
                    "ticker": citation.ticker,
                    "form_type": citation.form_type,
                    "filing_date": citation.filing_date,
                    "sids": citation.sids,
                    "quote": citation.quote,
                },
            )

        yield AnswerEvent(
            "done",
            {
                "chunks_retrieved": len(chunks),
                "citations_total": len(verified),
                "citations_verified": sum(c.verified for c in verified),
                "unverified_answer": citations is None,
            },
        )
    except Exception as exc:  # noqa: BLE001 — deliberately broad, see below
        # Design §10: an LLM outage becomes an `error` event, not a 500. By the
        # time generation starts the response headers are already sent, so
        # raising here would truncate the stream with no explanation.
        yield AnswerEvent("error", {"message": f"{type(exc).__name__}: {exc}"})
```

Note what did **not** change: everything from `user_message = build_user_message(...)`
onward is byte-for-byte identical to the current file — `build_user_message`,
generation, verification, and event emission already treat `chunks` generically
and already label each chunk's ticker inline, so a merged multi-company chunk
list needs no changes there.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_answer.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full suite and lint, then commit**

```bash
cd backend
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/api/answer.py tests/test_answer.py
git add src/api/answer.py tests/test_answer.py
git commit -m "feat: wire answer_stream through resolve_targets and retrieve_for_targets"
```

---

### Task 4: Wire `app.py` — the `tickers` filter and detector dependencies

**Files:**
- Modify: `backend/src/api/app.py`
- Modify: `backend/tests/test_app.py`

**Interfaces:**
- Consumes: `answer_stream`'s new signature (Task 3), `api.detect.AnthropicCompanyDetector`,
  `api.detect.AnthropicPeriodDetector`, `api.detect.CompanyDetector`,
  `api.detect.PeriodDetector` (existing).
- Produces: `Filters.tickers: list[str] | None`, `app.get_company_detector() -> CompanyDetector`,
  `app.get_period_detector() -> PeriodDetector`.

**⚠️ Every `stubbed_client`-based test in `test_app.py` will call the real
Anthropic API if you don't override the two new dependencies — read Step 1
before running anything.**

- [ ] **Step 1: Update `test_app.py`'s fixture and add new tests (still red until Step 3)**

```python
# backend/tests/test_app.py
# Change the import line near the top from:
#   from tests.fakes import FakeEmbedder, StubGenerator
# to:
from tests.fakes import FakeEmbedder, StubCompanyDetector, StubGenerator, StubPeriodDetector

# Change stubbed_client to also override the two new dependencies:
@pytest.fixture()
def stubbed_client(seeded_conn, monkeypatch):  # noqa: F811
    quote = "Total net sales were 391.0 billion dollars"
    generator = StubGenerator(response_with(quote, chunk_id_of(seeded_conn)))
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    app.dependency_overrides[app_module.get_generator] = lambda: generator
    app.dependency_overrides[app_module.get_embedder] = FakeEmbedder
    app.dependency_overrides[app_module.get_company_detector] = StubCompanyDetector
    app.dependency_overrides[app_module.get_period_detector] = StubPeriodDetector
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
```

Add one new test, right after `test_ask_passes_filters_through` — this is
the regression pin for the Global Constraints rule about explicit tickers
never being silently dropped:

```python
@pytest.mark.db
def test_ask_passes_the_new_tickers_filter_through(stubbed_client):
    response = stubbed_client.post(
        "/ask",
        json={"question": "What were net sales?", "filters": {"tickers": ["NOPE"]}},
    )
    done = next(data for name, data in parse_sse(response.text) if name == "done")
    assert done["chunks_retrieved"] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_app.py -v`
Expected: FAIL — `app_module.get_company_detector` / `get_period_detector`
don't exist yet, so the fixture itself raises an `AttributeError` before any
test body runs.

- [ ] **Step 3: Update `app.py`**

```python
# backend/src/api/app.py
from __future__ import annotations

import json
import os
from collections.abc import Iterator
from functools import lru_cache

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from pipeline import db
from pipeline.embed import Embedder
from pipeline.env import load_env

from . import queries
from .answer import AnswerEvent, answer_stream
from .detect import (
    AnthropicCompanyDetector,
    AnthropicPeriodDetector,
    CompanyDetector,
    PeriodDetector,
)
from .generate import AnthropicGenerator, Generator

# Before anything reads os.environ below. This module is the process entry
# point under uvicorn, so loading here is the equivalent of a main().
load_env()

app = FastAPI(title="EDGAR Answers", version="0.1.0")

# The browser preflights a JSON POST from another origin. Phase 5 sets
# FRONTEND_ORIGIN to the deployed domain; there is no wildcard here because a
# wildcard plus credentials is rejected by browsers and we may want credentials
# later.
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
)


class Filters(BaseModel):
    ticker: str | None = None
    tickers: list[str] | None = None
    form_type: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    filters: Filters = Field(default_factory=Filters)

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()


@lru_cache(maxsize=1)
def _embedder() -> Embedder:
    """One process-wide embedder: loading the ONNX weights per request would
    dominate latency."""
    return Embedder()


def get_embedder() -> Embedder:
    return _embedder()


def get_generator() -> Generator:
    return AnthropicGenerator()


def get_company_detector() -> CompanyDetector:
    return AnthropicCompanyDetector()


def get_period_detector() -> PeriodDetector:
    return AnthropicPeriodDetector()


def sse(event: AnswerEvent) -> str:
    payload = json.dumps(event.data, separators=(",", ":"))
    return f"event: {event.name}\ndata: {payload}\n\n"


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ask")
def ask(
    request: AskRequest,
    embedder: Embedder = Depends(get_embedder),
    generator: Generator = Depends(get_generator),
    company_detector: CompanyDetector = Depends(get_company_detector),
    period_detector: PeriodDetector = Depends(get_period_detector),
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
            ):
                yield sse(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/filings/{accession}")
def get_filing(accession: str) -> dict:
    with db.connect() as conn:
        filing = queries.load_filing(conn, accession)
    if filing is None:
        raise HTTPException(status_code=404, detail="filing not found")
    return filing


@app.get("/companies")
def get_companies() -> list[dict]:
    with db.connect() as conn:
        return queries.load_companies(conn)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_app.py -v`
Expected: PASS (all tests in the file, including the new one and the
pre-existing `test_ask_passes_filters_through` — confirm both the legacy
singular-`ticker` and the new plural-`tickers` paths return `chunks_retrieved: 0`
for an unknown ticker).

- [ ] **Step 5: Run the full suite and lint, then commit**

```bash
cd backend
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check src/api/app.py tests/test_app.py
git add src/api/app.py tests/test_app.py
git commit -m "feat: add the tickers filter and detector dependencies to /ask"
```

---

### Task 5: Frontend multi-select

**Files:**
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/components/ask-form.tsx`

**Interfaces:**
- Produces: `AskFilters.tickers?: string[]` (alongside the existing `ticker?: string`,
  kept for type compatibility but no longer set by this component).

- [ ] **Step 1: Widen `AskFilters`**

```typescript
// frontend/lib/api.ts
// Change line 6 from:
//   export type AskFilters = { ticker?: string; form_type?: string };
// to:
export type AskFilters = { ticker?: string; tickers?: string[]; form_type?: string };
```

Nothing else in `api.ts` changes — `askStream` already just
`JSON.stringify({ question, filters })`, so a `tickers` key flows through
automatically.

- [ ] **Step 2: Replace the single-select with a capped multi-select**

```tsx
// frontend/components/ask-form.tsx
"use client";

import { useEffect, useState } from "react";

import { fetchCompanies } from "@/lib/api";
import type { AskFilters } from "@/lib/api";
import type { Company } from "@/lib/types";

// Matches MAX_COMPANIES in backend/src/api/detect.py.
const MAX_TICKERS = 4;

export function AskForm({
  disabled,
  onSubmit,
}: {
  disabled: boolean;
  onSubmit: (question: string, filters: AskFilters) => void;
}) {
  const [question, setQuestion] = useState("");
  const [tickers, setTickers] = useState<string[]>([]);
  const [formType, setFormType] = useState("");
  const [companies, setCompanies] = useState<Company[]>([]);

  useEffect(() => {
    // A failed company list only costs the filter dropdown, so it must not
    // block asking questions.
    fetchCompanies()
      .then(setCompanies)
      .catch(() => setCompanies([]));
  }, []);

  function handleTickersChange(event: React.ChangeEvent<HTMLSelectElement>) {
    const selected = Array.from(event.target.selectedOptions, (option) => option.value);
    setTickers(selected.slice(0, MAX_TICKERS));
  }

  return (
    <form
      className="mb-6 flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (!question.trim()) return;
        onSubmit(question.trim(), {
          tickers: tickers.length > 0 ? tickers : undefined,
          form_type: formType || undefined,
        });
      }}
    >
      <input
        aria-label="Question"
        className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-blue-600 focus:outline-none"
        placeholder="What were Apple's total net sales in fiscal 2024?"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
      />
      <div className="flex gap-2">
        <select
          aria-label="Companies"
          multiple
          size={4}
          className="rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-xs text-slate-300"
          value={tickers}
          onChange={handleTickersChange}
        >
          {companies.map((company) => (
            <option key={company.cik} value={company.ticker}>
              {company.ticker}
            </option>
          ))}
        </select>
        <select
          aria-label="Form type"
          className="rounded border border-slate-700 bg-slate-900 px-2 py-1 font-mono text-xs text-slate-300"
          value={formType}
          onChange={(event) => setFormType(event.target.value)}
        >
          <option value="">All forms</option>
          <option value="10-K">10-K</option>
          <option value="10-Q">10-Q</option>
        </select>
        <button
          type="submit"
          disabled={disabled}
          className="rounded bg-blue-700 px-4 py-1 text-sm text-white hover:bg-blue-600 disabled:bg-slate-800 disabled:text-slate-500"
        >
          {disabled ? "Asking…" : "Ask"}
        </button>
      </div>
    </form>
  );
}
```

Note what's removed: the single `ticker`/`setTicker` state and its `<option value="">All companies</option>` placeholder — an empty multi-select
selection already means "no filter," so no placeholder option is needed.

- [ ] **Step 3: Verify — no existing frontend test covers this component or `api.ts`'s `AskFilters` type**

Confirm this claim before treating it as license to skip testing: run
`find frontend -iname "*ask-form*" -o -iname "*api.test*"` from the repo
root and confirm no matches other than `ask-form.tsx` itself. This project's
architecture (`CLAUDE.md`) deliberately keeps `components/` as thin
renderers with no dedicated component tests — logic lives in `lib/`, which
this change doesn't touch beyond the one-line type widening in Step 1. Do
not add a new test file for this component; that would be inventing test
infrastructure the project has deliberately not adopted.

Run the existing suite and type/lint checks to confirm nothing broke:

```bash
cd frontend
npm test
npm run lint
npx tsc --noEmit
```

(On Windows, run these from PowerShell — node is not on the git-bash PATH.)

Expected: all pass, since no other file imports `ticker`/`setTicker` from
this component and the `AskFilters` widening is additive.

- [ ] **Step 4: Manual verification**

Start the dev server (`npm run dev` from `frontend/`, PowerShell) and the
backend (`uvicorn api.app:app --reload --port 8000` from `backend/`,
with `.venv/Scripts/python.exe -m uvicorn ...`) against the real corpus.
Open `http://localhost:3000/ask`, select 2+ companies in the multi-select,
type a question, and submit. Confirm: the request's network payload
contains a `tickers` array with the selected values (not `ticker`), and the
page still renders an answer. If a real browser isn't available in this
environment, state that explicitly in the report rather than claiming this
step passed — Task 7 covers a full backend-only proof of the actual bug fix
without needing the browser.

- [ ] **Step 5: Commit**

```bash
cd frontend
git add lib/api.ts components/ask-form.tsx
git commit -m "feat: replace the single ticker filter with a capped multi-select"
```

---

### Task 6: Golden-set comparison rows and the `targeted_*` eval arm

**Files:**
- Modify: `backend/evals/golden.yaml`
- Modify: `backend/evals/harness.py`
- Modify: `backend/tests/test_evals.py`
- Modify: `backend/evals/__main__.py`

**Interfaces:**
- Consumes: `api.targets.Target`, `api.targets.resolve_targets`,
  `api.targets.retrieve_for_targets` (Tasks 1-2).
- Produces: `harness.GoldenQuestion.group: str` (new field, defaults to the
  entry's own `id`), `harness.run_retrieval_eval(..., company_detector=None, period_detector=None)`
  (two new optional keyword parameters; `targeted_*` metrics computed only
  when `company_detector` is given).

- [ ] **Step 1: Add the comparison rows to `golden.yaml`**

Both facts below were read directly from the ingested corpus and confirmed
present at the given sid before being committed here — the same discipline
this file's own header comment already documents for q001-q016.

```yaml
# backend/evals/golden.yaml  (append)
- id: qc001a
  group: qc001
  question: Compare Apple's and Microsoft's research and development spending
    in their most recent fiscal year.
  ticker: AAPL
  accession: 0000320193-24-000123
  section: item7
  gold_sids: [767]
- id: qc001b
  group: qc001
  question: Compare Apple's and Microsoft's research and development spending
    in their most recent fiscal year.
  ticker: MSFT
  accession: 0001193125-26-323660
  section: item7
  gold_sids: [1340]
- id: qc002a
  group: qc002
  question: Compare Microsoft's research and development spending to Amazon's
    technology and infrastructure spending in their most recent annual reports.
  ticker: MSFT
  accession: 0001193125-26-323660
  section: item7
  gold_sids: [1340]
- id: qc002b
  group: qc002
  question: Compare Microsoft's research and development spending to Amazon's
    technology and infrastructure spending in their most recent annual reports.
  ticker: AMZN
  accession: 0001018724-26-000004
  section: other
  gold_sids: [798]
```

Before writing code against this, verify these four entries directly against
the live corpus (read-only, no changes):

```bash
cd backend && .venv/Scripts/python.exe -c "
from pipeline.env import load_env
load_env()
from pipeline.db import connect
conn = connect()
with conn.cursor() as cur:
    for ticker, acc, sid in [('AAPL','0000320193-24-000123',767), ('MSFT','0001193125-26-323660',1340), ('AMZN','0001018724-26-000004',798)]:
        cur.execute('''
            SELECT c.ticker, f.accession, s.sid, s.text
            FROM sentences s JOIN filings f ON f.id=s.filing_id JOIN companies c ON c.cik=f.cik
            WHERE c.ticker=%s AND f.accession=%s AND s.sid=%s
        ''', (ticker, acc, sid))
        row = cur.fetchone()
        assert row is not None, f'{ticker} {acc} sid {sid} not found'
        print(row)
"
```

Expected: three rows printed, each showing the sentence text quoted in this
step's rationale (a dollar-figure R&D or technology-and-infrastructure table
row). If any assertion fails, stop and report BLOCKED — do not invent a
different sid to make it pass; the corpus may have changed since this plan
was written.

- [ ] **Step 2: Write the failing tests for `GoldenQuestion.group` and the new eval arm**

```python
# backend/tests/test_evals.py  (append; GOLDEN is already defined earlier in this file)
from api.targets import Target


def test_load_golden_defaults_group_to_the_entrys_own_id(tmp_path):
    path = tmp_path / "golden.yaml"
    path.write_text(
        "- id: q001\n"
        "  question: What were net sales?\n"
        "  ticker: AAPL\n"
        '  accession: "0000320193-24-000123"\n'
        "  section: item7\n"
        "  gold_sids: [612]\n",
        encoding="utf-8",
    )
    questions = harness.load_golden(path)
    assert questions[0].group == "q001"


def test_load_golden_reads_an_explicit_group(tmp_path):
    path = tmp_path / "golden.yaml"
    path.write_text(
        "- id: qc001a\n"
        "  group: qc001\n"
        "  question: Compare X and Y.\n"
        "  ticker: AAPL\n"
        '  accession: "ACC-1"\n'
        "  section: item7\n"
        "  gold_sids: [1]\n",
        encoding="utf-8",
    )
    questions = harness.load_golden(path)
    assert questions[0].group == "qc001"


def test_targeted_arm_dedupes_by_group_and_scores_each_sibling_row(monkeypatch):
    from tests.fakes import StubCompanyDetector

    a = harness.GoldenQuestion("qc001a", "Compare X and Y.", "AAPL", "ACC-A", "item7", [1], "qc001")
    b = harness.GoldenQuestion("qc001b", "Compare X and Y.", "MSFT", "ACC-M", "item7", [2], "qc001")

    resolve_calls = []
    retrieve_calls = []

    def fake_resolve_targets(conn, question, *, explicit_tickers, company_detector, period_detector=None):
        resolve_calls.append(question)
        return [Target("AAPL", None), Target("MSFT", None)]

    def fake_retrieve_for_targets(conn, embedder, question, targets, *, k_final, form_type=None):
        retrieve_calls.append(question)
        return [chunk("ACC-A", 0, 5), chunk("ACC-M", 0, 5)]

    monkeypatch.setattr("evals.harness.resolve_targets", fake_resolve_targets)
    monkeypatch.setattr("evals.harness.retrieve_for_targets", fake_retrieve_for_targets)

    metrics = harness.run_retrieval_eval(
        None, None, [a, b], company_detector=StubCompanyDetector()
    )
    assert resolve_calls == ["Compare X and Y."]  # deduped: one group, one resolve call
    assert retrieve_calls == ["Compare X and Y."]
    assert metrics["targeted_recall@10"] == 1.0
    assert metrics["targeted_misses@10"] == []


def test_targeted_arm_is_skipped_when_no_company_detector_is_given():
    metrics = harness.run_retrieval_eval(None, None, [GOLDEN])
    assert "targeted_recall@10" not in metrics
```

`chunk(...)` is already defined earlier in this file (used by the existing
`_two_arm_retrieve` tests) — reuse it, don't redefine it.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_evals.py -k "group or targeted" -v`
Expected: FAIL — `group` doesn't exist on `GoldenQuestion` yet, and
`run_retrieval_eval` doesn't accept `company_detector`.

- [ ] **Step 4: Update `harness.py`**

```python
# backend/evals/harness.py
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from api.retrieval import RetrievedChunk, retrieve
from api.targets import resolve_targets, retrieve_for_targets

GOLDEN_PATH = Path(__file__).parent / "golden.yaml"
RESULTS_PATH = Path(__file__).parent / "results.jsonl"
_REQUIRED = ("id", "question", "ticker", "accession", "section", "gold_sids")


@dataclass(frozen=True)
class GoldenQuestion:
    id: str
    question: str
    ticker: str
    accession: str
    section: str
    gold_sids: list[int]
    group: str = ""  # set by load_golden to entry.get("group", entry["id"])


def load_golden(path: Path = GOLDEN_PATH) -> list[GoldenQuestion]:
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    questions = []
    for entry in entries:
        entry_id = entry.get("id", "<missing id>")
        for field in _REQUIRED:
            if field not in entry:
                raise ValueError(f"golden entry {entry_id}: missing field {field!r}")
        if not isinstance(entry["gold_sids"], list) or not all(
            isinstance(s, int) for s in entry["gold_sids"]
        ):
            raise ValueError(f"golden entry {entry_id}: gold_sids must be a list of ints")
        group = entry.get("group", entry["id"])
        questions.append(GoldenQuestion(*(entry[f] for f in _REQUIRED), group))
    return questions


def hit(chunks: list[RetrievedChunk], accession: str, gold_sids: list[int]) -> bool:
    return any(
        chunk.accession == accession
        and any(chunk.sid_start <= sid <= chunk.sid_end for sid in gold_sids)
        for chunk in chunks
    )


def _score(conn, embedder, questions, *, ks, k_each: int, scoped: bool) -> dict:
    """Recall over one retrieval arm; `scoped` applies each question's ticker."""
    top_k = max(ks)
    hits_at = {k: 0 for k in ks}
    misses_at_top: list[str] = []
    for question in questions:
        chunks = retrieve(
            conn,
            embedder,
            question.question,
            k_each=k_each,
            k_final=top_k,
            **({"ticker": question.ticker} if scoped else {}),
        )
        for k in ks:
            if hit(chunks[:k], question.accession, question.gold_sids):
                hits_at[k] += 1
        if not hit(chunks, question.accession, question.gold_sids):
            misses_at_top.append(question.id)
    n = len(questions)
    scores: dict = {f"recall@{k}": round(hits_at[k] / n, 4) if n else 0.0 for k in ks}
    scores[f"misses@{top_k}"] = misses_at_top
    return scores


def _score_targeted(
    conn, embedder, questions, *, ks, company_detector, period_detector=None
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
            )
            chunks_by_group[question.group] = retrieve_for_targets(
                conn, embedder, question.question, targets, k_final=top_k
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


def run_retrieval_eval(
    conn,
    embedder,
    questions,
    *,
    ks=(5, 10),
    k_each: int = 20,
    company_detector=None,
    period_detector=None,
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
    unfiltered gets wrong.
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
            company_detector=company_detector,
            period_detector=period_detector,
        )
    return metrics


def _git(*args: str) -> str:
    # check=False is the intent: a failed git call (no repo, detached state)
    # degrades to "" below rather than aborting an eval run.
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def append_results(path: Path, metrics: dict) -> None:
    # git_dirty matters as much as git_sha: an eval run against a working tree
    # with uncommitted changes is not reproducible from its recorded sha, and
    # a results log that can't be replayed is worse than no log. Recording the
    # flag makes that visible in the file instead of inferrable from commit
    # timestamps.
    record = {
        "git_sha": _git("rev-parse", "--short", "HEAD") or "unknown",
        "git_dirty": bool(_git("status", "--porcelain")),
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        **metrics,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_evals.py -v`
Expected: PASS (all tests in the file, old and new)

- [ ] **Step 6: Wire the real detectors into `python -m evals run`**

```python
# backend/evals/__main__.py
# In cmd_run, replace:
#         embedder = Embedder()
#         metrics = harness.run_retrieval_eval(conn, embedder, questions)
# with:
        embedder = Embedder()
        from api.detect import AnthropicCompanyDetector, AnthropicPeriodDetector

        metrics = harness.run_retrieval_eval(
            conn,
            embedder,
            questions,
            company_detector=AnthropicCompanyDetector(),
            period_detector=AnthropicPeriodDetector(),
        )
```

Leave the `--debug` branch (the vector/lexical print loop above this) and
everything below (`if not args.retrieval_only: ...`) untouched.

- [ ] **Step 7: Run the full suite and lint, then commit**

```bash
cd backend
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check evals/harness.py evals/__main__.py tests/test_evals.py
git add evals/golden.yaml evals/harness.py evals/__main__.py tests/test_evals.py
git commit -m "feat: add comparison rows and the targeted eval arm"
```

---

### Task 7: Prove the fix and measure the rollout gate

**Files:** none (verification only — no code changes in this task).

**Interfaces:** none produced. Consumes everything from Tasks 1-6.

This task has no automated test of its own — like the sibling plans'
final CLI-verification tasks, its evidence is the actual command output,
pasted in full into the report.

- [ ] **Step 1: Replay the original motivating bug directly**

This is the concrete proof the whole four-spec series was built for.

```bash
cd backend && .venv/Scripts/python.exe -c "
from pipeline.env import load_env
load_env()
from pipeline.db import connect
from pipeline.embed import Embedder
from api.generate import AnthropicGenerator
from api.detect import AnthropicCompanyDetector, AnthropicPeriodDetector
from api.answer import answer_stream

conn = connect()
events = list(answer_stream(
    conn, Embedder(), AnthropicGenerator(), AnthropicCompanyDetector(),
    'Who achieved more growth from 23 to 24? Microsoft or Amazon? Why',
    period_detector=AnthropicPeriodDetector(),
))
citations = [e for e in events if e.name == 'citation']
tickers = sorted({c.data['ticker'] for c in citations})
done = next(e for e in events if e.name == 'done').data
print('Citing tickers:', tickers)
print('done event:', done)
assert 'MSFT' in tickers and 'AMZN' in tickers, f'Expected both MSFT and AMZN, got {tickers}'
print('PASS: both companies are represented in the answer.')
"
```

Expected: `Citing tickers: ['AMZN', 'MSFT']` (or a superset including both),
and the final assertion passes. This is the direct fix for the bug that
opened this whole series: originally 8/8 retrieved chunks were Microsoft,
0 Amazon. If either ticker is missing, stop and report BLOCKED with the
full output — do not weaken the assertion to make it pass.

- [ ] **Step 2: Run the full eval, twice, and compare all three arms**

```bash
cd backend && .venv/Scripts/python.exe -m evals run --retrieval-only
```

Run this command twice (this repo's documented LLM run-to-run variance
means one run isn't trustworthy alone). Paste both full outputs in the
report. Confirm:
- `recall@10` and `unfiltered_recall@10` (the pre-existing arms) are
  unchanged from their values before this branch, within the variance this
  repo has already documented for repeated runs — this is the "0-1 targets
  is exactly today's behavior" regression guarantee from Task 1, now
  verified against the real corpus rather than just unit-tested.
- `targeted_recall@10` is reported and is **higher than** `unfiltered_recall@10`
  — this is the rollout gate from the spec's §5: don't switch `/ask`'s
  default behavior (already done in Task 4, since `/ask` always resolves
  targets now) without evidence it's actually better. If `targeted_recall@10`
  is *not* higher, do not treat this as this task's failure to fix —
  report the actual numbers and mismatches plainly; investigating a
  regression here is a new task, not a rewrite of this one's assertions.
- `targeted_misses@10` no longer includes `q004` and `q009` (or, if it
  still does on one of the two runs, note that plainly rather than treating
  it as a report-format problem — fiscal period handling's own live eval
  documented one case, `p001`, with an inherent ceiling below 100%, so some
  run-to-run miss noise here is expected and was already flagged in that
  branch's spec).

- [ ] **Step 3: Write the report**

Write the full report to a temporary note in your final reply (this task
has no report file path, since it's the last task in the plan and its
findings belong in the completion summary): both full `evals run` outputs,
the Step 1 script's full output, and a one-paragraph assessment of whether
the rollout gate (Step 2's second bullet) is met.

- [ ] **Step 4: No commit for this task**

This task makes no code changes. If Step 1 or Step 2 reveals a real defect,
report BLOCKED with the evidence rather than silently patching something —
the controller will decide whether that's a fix-now issue or a follow-up.

## Self-Review Notes

- **Spec coverage:** §3.1 (Target) → Task 1. §3.2 (resolve_targets, including
  the "if available" period-detector wiring, which this plan wires
  unconditionally into `/ask` since fiscal period handling has already
  landed) → Task 2. §3.3 (orchestration, API contract) → Tasks 3-4. §3.4
  (frontend) → Task 5. §4 (eval harness) → Task 6. §5 (rollout gate) →
  Task 7. §6 risks (latency/cost, detector failure) → the exception-handling
  design in Task 2 and the explicit note in Global Constraints.
- **Deliberate deviation from a literal spec reading, recorded here rather
  than discovered mid-review:** the spec's "validated against the known
  ticker set" language for explicit tickers, if implemented literally
  (dropping unknown tickers), would silently regress `test_ask_passes_filters_through`
  (a pinned, pre-existing test) from "unknown ticker returns zero chunks" to
  "unknown ticker returns every company's chunks." Task 2 and Task 4 both
  document and test the corrected behavior: normalize and cap explicit
  tickers, but never drop them for being unrelated to a real company.
- **Placeholder scan:** none found; every step has runnable code.
- **Type consistency:** `Target(ticker: str, accessions: list[str] | None)`
  is constructed identically in Task 2's `resolve_targets` and consumed
  identically in Task 1's `retrieve_for_targets`. `resolve_targets`'s and
  `retrieve_for_targets`'s signatures in Task 6's `_score_targeted` match
  exactly what Tasks 1-2 produce (verified by using keyword arguments at
  every call site, matching the source definitions).
- **Cross-task ordering:** Tasks 1-2 (api/targets.py) must land before
  Tasks 3, 4, and 6, which all import from it. Task 3 (answer_stream) must
  land before Task 4 (app.py), which calls it. Task 5 (frontend) only
  depends on Task 4's API contract (the `tickers` field name) and could in
  principle run in parallel with Task 6, but is sequenced after it here
  since Task 7's manual verification benefits from everything else being
  done first.
