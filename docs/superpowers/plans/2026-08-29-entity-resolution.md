# Entity Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect which of the corpus's 10 known companies a free-text `/ask`
question refers to, so a later plan (query decomposition) can run retrieval
per company instead of one whole-corpus query that lets one filer's chunks
crowd out another's.

**Architecture:** A new `api/detect.py` module exposes a narrow
`CompanyDetector` protocol and an `AnthropicCompanyDetector` implementation
that makes one non-streaming Haiku call per question, constrained to strict
JSON and validated defensively against the known ticker set. A parallel
`evals/entity_resolution.py` scores the detector's accuracy against a
hand-labeled case set, independent of retrieval.

**Tech Stack:** Python 3.13, `anthropic` SDK (already a dependency),
`pytest`, `pyyaml`.

**Spec:** `docs/superpowers/specs/2026-08-29-entity-resolution-design.md`

## Global Constraints

- Python 3.13 everywhere; no new dependencies (`anthropic>=0.40` is already
  in `backend/pyproject.toml`).
- Model is `claude-haiku-4-5` (import `MODEL` from `api.generate`, don't
  redefine the string) at `temperature=0`, per design.md §2's low-cost bar.
- `ruff==0.16.1` is pinned exactly; run `ruff check .` before each commit.
- Detector output is capped at 4 tickers (`MAX_COMPANIES` in `api/detect.py`).
- No database migration is needed — this plan only reads
  `companies.ticker`/`companies.name` via the existing `queries.load_companies`.
- Commit messages: no AI attribution of any kind (no Co-Authored-By, no
  session trailers, no tool names) — this is a hard project rule.
- Unit tests never call the live Anthropic API — `AnthropicCompanyDetector`
  itself is exercised only by the manual `python -m evals entities` command
  in Task 4, matching the existing precedent that `api.generate.AnthropicGenerator`
  has no pytest coverage either.

---

### Task 1: Detection protocol and defensive JSON parsing

**Files:**
- Create: `backend/src/api/detect.py`
- Test: `backend/tests/test_detect.py`

**Interfaces:**
- Produces: `api.detect.CompanyDetector` (Protocol; `detect(self, question: str, companies: list[dict]) -> list[str]`), `api.detect.MAX_COMPANIES: int` (= 4), `api.detect.parse_detected_companies(raw: str, known_tickers: set[str]) -> list[str]`.
- Consumes: nothing new (stdlib `json`, `re` only).

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_detect.py
from api.detect import MAX_COMPANIES, parse_detected_companies

KNOWN = {"AAPL", "AMZN", "GOOGL", "JNJ", "JPM", "META", "MSFT", "NVDA", "TSLA", "WMT"}


def test_parses_bare_json():
    raw = '{"tickers": ["MSFT", "AMZN"]}'
    assert parse_detected_companies(raw, KNOWN) == ["MSFT", "AMZN"]


def test_parses_fenced_json():
    raw = '```json\n{"tickers": ["AAPL"]}\n```'
    assert parse_detected_companies(raw, KNOWN) == ["AAPL"]


def test_empty_tickers_list_is_valid():
    assert parse_detected_companies('{"tickers": []}', KNOWN) == []


def test_malformed_json_returns_empty():
    assert parse_detected_companies('{"tickers": [oops}', KNOWN) == []


def test_no_json_object_returns_empty():
    assert parse_detected_companies("I'm not sure.", KNOWN) == []


def test_non_list_tickers_field_returns_empty():
    assert parse_detected_companies('{"tickers": "MSFT"}', KNOWN) == []


def test_unknown_ticker_is_dropped():
    raw = '{"tickers": ["MSFT", "ZZZZ"]}'
    assert parse_detected_companies(raw, KNOWN) == ["MSFT"]


def test_duplicate_tickers_are_deduped():
    raw = '{"tickers": ["MSFT", "MSFT", "AMZN"]}'
    assert parse_detected_companies(raw, KNOWN) == ["MSFT", "AMZN"]


def test_output_is_capped_at_max_companies_in_mention_order():
    raw = '{"tickers": ["AAPL", "AMZN", "GOOGL", "JPM", "META", "MSFT"]}'
    result = parse_detected_companies(raw, KNOWN)
    assert result == ["AAPL", "AMZN", "GOOGL", "JPM"]
    assert len(result) == MAX_COMPANIES
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_detect.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.detect'`

- [ ] **Step 3: Write the minimal implementation**

```python
# backend/src/api/detect.py
from __future__ import annotations

import json
import re
from typing import Protocol

# design.md §14 / spec: at most this many companies decompose into separate
# retrievals downstream. Defined here since this module is the producer.
MAX_COMPANIES = 4

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class CompanyDetector(Protocol):
    """The LLM, narrowed to the one thing entity resolution needs."""

    def detect(self, question: str, companies: list[dict]) -> list[str]:
        """Return the tickers (from `companies`) the question discusses."""
        ...


def parse_detected_companies(raw: str, known_tickers: set[str]) -> list[str]:
    """Defensive parse of a detector's raw response into a ticker list.

    Never raises. A hallucinated ticker, malformed JSON, or a response with
    no JSON object at all all degrade to `[]` -- "no company detected" is a
    safe result every consumer already has to handle for genuinely
    company-less questions.
    """
    text = _FENCE.sub("", raw.strip())
    match = _OBJECT.search(text)
    if match is None:
        return []
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    tickers = payload.get("tickers")
    if not isinstance(tickers, list):
        return []
    seen: list[str] = []
    for ticker in tickers:
        if isinstance(ticker, str) and ticker in known_tickers and ticker not in seen:
            seen.append(ticker)
        if len(seen) == MAX_COMPANIES:
            break
    return seen
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_detect.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Lint and commit**

```bash
cd backend && ruff check src/api/detect.py tests/test_detect.py
git add src/api/detect.py tests/test_detect.py
git commit -m "feat: parse and validate LLM company-detection output"
```

---

### Task 2: Real detector — prompt construction and the Anthropic call

**Files:**
- Modify: `backend/src/api/detect.py`
- Test: `backend/tests/test_detect.py`

**Interfaces:**
- Consumes: `api.detect.parse_detected_companies` (Task 1), `api.generate.MODEL`.
- Produces: `api.detect.DETECTION_SYSTEM_PROMPT: str`, `api.detect.build_detection_prompt(question: str, companies: list[dict]) -> str`, `api.detect.AnthropicCompanyDetector` (dataclass; fields `model: str`, `max_tokens: int`, `api_key: str | None`; implements `CompanyDetector`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_detect.py  (append)
from api.detect import build_detection_prompt


def test_detection_prompt_lists_each_company_and_the_question():
    companies = [
        {"cik": 320193, "ticker": "AAPL", "name": "Apple Inc.", "filings": 13},
        {"cik": 1018724, "ticker": "AMZN", "name": "Amazon.com, Inc.", "filings": 12},
    ]
    prompt = build_detection_prompt("Compare Apple and Amazon's margins.", companies)
    assert "AAPL: Apple Inc." in prompt
    assert "AMZN: Amazon.com, Inc." in prompt
    assert "Compare Apple and Amazon's margins." in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/test_detect.py::test_detection_prompt_lists_each_company_and_the_question -v`
Expected: FAIL with `ImportError: cannot import name 'build_detection_prompt'`

- [ ] **Step 3: Write the minimal implementation**

```python
# backend/src/api/detect.py  (append)
import os
from dataclasses import dataclass

from .generate import MODEL

DETECTION_SYSTEM_PROMPT = """You identify which companies, if any, a question about SEC filings discusses.

You will be given a roster of companies, each as "TICKER: Name". Decide which
roster companies the question is about -- by name, by ticker, or by an
indirect reference (a nickname, a well-known product, a description of the
business).

Respond with strict JSON and nothing else:

{"tickers": ["MSFT", "AMZN"]}

Rules:
1. Only use tickers that appear in the roster you were given. Never invent one.
2. If the question is not about any roster company, respond with {"tickers": []}.
3. List at most 4 tickers, in the order the question refers to them.
"""


def build_detection_prompt(question: str, companies: list[dict]) -> str:
    roster = "\n".join(f"{c['ticker']}: {c['name']}" for c in companies)
    return f"Companies:\n{roster}\n\nQuestion: {question}"


@dataclass
class AnthropicCompanyDetector:
    model: str = MODEL
    max_tokens: int = 100
    api_key: str | None = None

    def detect(self, question: str, companies: list[dict]) -> list[str]:
        import anthropic

        client = anthropic.Anthropic(
            api_key=self.api_key or os.environ["ANTHROPIC_API_KEY"]
        )
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0,
            system=DETECTION_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": build_detection_prompt(question, companies)}
            ],
        )
        raw = "".join(block.text for block in message.content if block.type == "text")
        known_tickers = {c["ticker"] for c in companies}
        return parse_detected_companies(raw, known_tickers)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_detect.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Lint and commit**

```bash
cd backend && ruff check src/api/detect.py tests/test_detect.py
git add src/api/detect.py tests/test_detect.py
git commit -m "feat: add the Anthropic-backed company detector"
```

---

### Task 3: Hand-labeled case set and accuracy scoring

**Files:**
- Create: `backend/evals/entity_resolution_cases.yaml`
- Create: `backend/evals/entity_resolution.py`
- Modify: `backend/tests/fakes.py`
- Test: `backend/tests/test_evals.py`

**Interfaces:**
- Consumes: the `CompanyDetector` shape from Task 1 (structural — no import needed by the fake).
- Produces: `evals.entity_resolution.EntityResolutionCase` (dataclass: `id: str`, `question: str`, `expected_tickers: list[str]`), `evals.entity_resolution.load_cases(path=CASES_PATH) -> list[EntityResolutionCase]`, `evals.entity_resolution.run_entity_resolution_eval(detector, companies: list[dict], cases: list[EntityResolutionCase]) -> dict` (keys: `cases`, `correct`, `accuracy`, `mismatches`), `tests.fakes.StubCompanyDetector`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_evals.py  (append)
from evals.entity_resolution import (
    EntityResolutionCase,
    load_cases,
    run_entity_resolution_eval,
)
from tests.fakes import StubCompanyDetector


def test_load_cases_validates_fields(tmp_path):
    good = tmp_path / "cases.yaml"
    good.write_text(
        "- id: e001\n"
        "  question: Compare Microsoft and Amazon's growth.\n"
        "  expected_tickers: [MSFT, AMZN]\n",
        encoding="utf-8",
    )
    cases = load_cases(good)
    assert cases[0].id == "e001"
    assert cases[0].expected_tickers == ["MSFT", "AMZN"]

    bad = tmp_path / "bad.yaml"
    bad.write_text("- id: e002\n  question: Missing expected_tickers\n", encoding="utf-8")
    with pytest.raises(ValueError, match="e002"):
        load_cases(bad)


def test_run_entity_resolution_eval_scores_exact_set_match():
    cases = [
        EntityResolutionCase("e001", "Compare MSFT and AMZN.", ["MSFT", "AMZN"]),
        EntityResolutionCase("e002", "What is a 10-K?", []),
    ]
    detector = StubCompanyDetector(
        {
            "Compare MSFT and AMZN.": ["MSFT", "AMZN"],
            "What is a 10-K?": ["AAPL"],
        }
    )
    metrics = run_entity_resolution_eval(detector, [], cases)
    assert metrics["cases"] == 2
    assert metrics["correct"] == 1
    assert metrics["accuracy"] == 0.5
    assert metrics["mismatches"] == [
        {"id": "e002", "expected": [], "actual": ["AAPL"]}
    ]


def test_run_entity_resolution_eval_ignores_ticker_order():
    cases = [EntityResolutionCase("e001", "Compare A and B.", ["AAPL", "AMZN"])]
    detector = StubCompanyDetector({"Compare A and B.": ["AMZN", "AAPL"]})
    metrics = run_entity_resolution_eval(detector, [], cases)
    assert metrics["accuracy"] == 1.0
```

`pytest` must already be imported at the top of `test_evals.py` — check before
appending; it is (`import pytest` at line 6).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && pytest tests/test_evals.py -k entity_resolution -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evals.entity_resolution'`

- [ ] **Step 3: Write the minimal implementation**

```python
# backend/tests/fakes.py  (append)
class StubCompanyDetector:
    """Returns a canned ticker list per exact question text, for testing
    consumers of CompanyDetector without hitting the live API."""

    def __init__(self, answers: dict[str, list[str]] | None = None):
        self.answers = answers or {}
        self.calls: list[str] = []

    def detect(self, question, companies):
        self.calls.append(question)
        return self.answers.get(question, [])
```

```python
# backend/evals/entity_resolution.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

CASES_PATH = Path(__file__).parent / "entity_resolution_cases.yaml"
_REQUIRED = ("id", "question", "expected_tickers")


@dataclass(frozen=True)
class EntityResolutionCase:
    id: str
    question: str
    expected_tickers: list[str]


def load_cases(path: Path = CASES_PATH) -> list[EntityResolutionCase]:
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    cases = []
    for entry in entries:
        entry_id = entry.get("id", "<missing id>")
        for field in _REQUIRED:
            if field not in entry:
                raise ValueError(f"case {entry_id}: missing field {field!r}")
        cases.append(EntityResolutionCase(*(entry[f] for f in _REQUIRED)))
    return cases


def run_entity_resolution_eval(detector, companies: list[dict], cases) -> dict:
    """Score a CompanyDetector against hand-labeled cases.

    Unlike golden.yaml/harness.py, no retrieval is involved -- this judges
    the detector's raw output directly, so order never matters (a question
    naming two companies in either order is equally correct).
    """
    correct = 0
    mismatches = []
    for case in cases:
        actual = detector.detect(case.question, companies)
        if set(actual) == set(case.expected_tickers):
            correct += 1
        else:
            mismatches.append(
                {"id": case.id, "expected": case.expected_tickers, "actual": actual}
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
# backend/evals/entity_resolution_cases.yaml
# Hand-labeled cases for entity resolution (spec
# docs/superpowers/specs/2026-08-29-entity-resolution-design.md). Scored by
# `python -m evals entities` directly against the live detector -- this is
# not retrieval-scored, so it does not append to results.jsonl. Given this
# repo's own observed LLM run-to-run variance (see CLAUDE.md on
# gold_sid_hit_rate), re-run more than once before trusting a single score.

- id: e001
  question: Who achieved more revenue growth from fiscal 2023 to fiscal 2024,
    Microsoft or Amazon?
  expected_tickers: [MSFT, AMZN]
- id: e002
  question: Compare Apple's and Google's research and development spending.
  expected_tickers: [AAPL, GOOGL]
- id: e003
  question: What were Apple's total net sales in fiscal 2024?
  expected_tickers: [AAPL]
- id: e004
  question: How much did the iPhone maker spend on research and development?
  expected_tickers: [AAPL]
- id: e005
  question: What risks does the Seattle-based online retailer disclose in its
    filings?
  expected_tickers: [AMZN]
- id: e006
  question: How did Microsft's cloud revenue grow last year?
  expected_tickers: [MSFT]
- id: e007
  question: What is the capital of France?
  expected_tickers: []
- id: e008
  question: What is a 10-K filing and who has to file one?
  expected_tickers: []
- id: e009
  question: Compare the risk factors disclosed by JPMorgan, Walmart, and Tesla.
  expected_tickers: [JPM, WMT, TSLA]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_evals.py -k entity_resolution -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Lint and commit**

```bash
cd backend && ruff check evals/entity_resolution.py tests/fakes.py tests/test_evals.py
git add evals/entity_resolution.py evals/entity_resolution_cases.yaml tests/fakes.py tests/test_evals.py
git commit -m "feat: score entity resolution against a hand-labeled case set"
```

---

### Task 4: Wire the `entities` eval subcommand

**Files:**
- Modify: `backend/evals/__main__.py`

**Interfaces:**
- Consumes: `api.detect.AnthropicCompanyDetector` (Task 2), `api.queries.load_companies`, `evals.entity_resolution.load_cases`, `evals.entity_resolution.run_entity_resolution_eval` (Task 3).
- Produces: `python -m evals entities` CLI command.

- [ ] **Step 1: Add the subcommand function**

```python
# backend/evals/__main__.py  (add near the other cmd_ functions)
def cmd_entities(args) -> None:
    from api import queries
    from api.detect import AnthropicCompanyDetector

    from . import entity_resolution

    cases = entity_resolution.load_cases()
    with db.connect() as conn:
        companies = queries.load_companies(conn)
    metrics = entity_resolution.run_entity_resolution_eval(
        AnthropicCompanyDetector(), companies, cases
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

In `main()`, add the subparser next to the existing ones:

```python
    sub.add_parser(
        "entities", help="score entity-resolution detection against hand-labeled cases"
    )
```

And add an explicit dispatch branch — the existing `else: cmd_verify(args)` only
happens to be correct today because `verify` is the last defined subcommand;
adding a fourth command without an explicit branch would silently run
`cmd_verify` instead:

```python
    if args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "repin":
        cmd_repin(args)
    elif args.cmd == "entities":
        cmd_entities(args)
    else:
        cmd_verify(args)
```

- [ ] **Step 3: Run it manually against the live API**

Run: `cd backend && python -m evals entities`
Expected: prints any mismatches, then a line like `8/9 correct (88.89%)`. This
hits the real `ANTHROPIC_API_KEY` and DB — matching how `cmd_run`/`cmd_verify`
are exercised (no pytest coverage for `__main__.py`'s dispatch, by existing
project convention). Investigate and adjust `DETECTION_SYSTEM_PROMPT` (Task 2)
if any mismatch looks like a genuine detector error rather than a
mis-specified expectation in the case file.

- [ ] **Step 4: Lint and commit**

```bash
cd backend && ruff check evals/__main__.py
git add evals/__main__.py
git commit -m "feat: add the entities eval subcommand"
```

---

## Self-Review Notes

- **Spec coverage:** §3.1 (why LLM-based) is documentation/rationale, not
  code — captured in `DETECTION_SYSTEM_PROMPT`'s design and this plan's
  intro, no separate task needed. §3.2 (the detector) → Tasks 1–2. §3.3
  (validation) → Task 1. §3.4 (failure handling) → Task 1's parser returning
  `[]` on any malformed input, plus `AnthropicCompanyDetector.detect` letting
  a network exception propagate to its caller (the eval CLI, or later the
  answer-stream integration in the query-decomposition plan) to catch —
  this plan does not add its own try/except in `detect()` because query
  decomposition (the only caller before this ships) is where that
  degrade-to-empty behavior actually needs to live and be tested end-to-end.
  §4 (testing/eval) → Tasks 3–4.
- **Placeholder scan:** none found; every step has runnable code.
- **Type consistency:** `CompanyDetector.detect(question: str, companies: list[dict]) -> list[str]` is the same signature in the Protocol (Task 1), `AnthropicCompanyDetector` (Task 2), and `StubCompanyDetector` (Task 3). `parse_detected_companies(raw: str, known_tickers: set[str]) -> list[str]` is defined once (Task 1) and consumed once (Task 2) with no signature drift.
