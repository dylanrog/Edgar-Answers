# Entity Resolution — Design

**Date:** 2026-08-29
**Status:** approved, not yet implemented
**Part of:** spec 2 of the 4-spec series started by `2026-08-12-tables-first-class-design.md`
(spec 1). Followed by fiscal period handling (spec 3) and query decomposition
(spec 4); those specs depend on this one.

## 1. Problem

`/ask` retrieval (`api/retrieval.py::retrieve`) runs one embedding and one
lexical query over the whole corpus with no signal about which of the
corpus's companies the question is actually about. Reproduced directly:

```
Q: Who achieved more growth from 23 to 24? Microsoft or Amazon? Why
```

Running `retrieve()` on this question returns 8/8 MSFT chunks, 0 Amazon.
Microsoft's MD&A language scores closer to the question's phrasing on both
the vector and lexical arms, so Amazon is crowded out of the fused top-8
entirely before generation ever runs. The model then correctly refuses to
compare — it was never shown any Amazon text — but the failure is retrieval's,
not generation's.

The existing ticker filter on `/ask` only scopes to one company at a time, so
it cannot help a genuine comparison question, and it does nothing for a
free-text mention like "Microsoft" typed into the question itself — that text
is invisible to retrieval today.

## 2. Scope

**In scope:** detecting which of the corpus's known companies (a fixed list
of 10 — design.md §2 rules out on-demand ticker ingestion) a free-text
question refers to, and validating that output defensively.

**Out of scope:** what happens once companies are known — running retrieval
per company and merging (query decomposition, spec 4); identifying *which
filing* of a company a question means (fiscal period handling, spec 3); any
UI change (the explicit multi-select ticker filter belongs to spec 4, since
that's where multiple tickers actually get consumed).

## 3. Design

### 3.1 Why an LLM call, not string/alias matching

A hardcoded alias table (`"Google" → GOOGL`, `"J&J" → JNJ`, ...) was the
first idea, rejected for one structural reason: it can't distinguish "this
question isn't about any company" from "this question has a typo or an
indirect reference." `"Microsft"`, `"Amazn"`, and `"the Seattle retailer"` all
produce zero matches from an alias table, which is indistinguishable from a
question that genuinely names no company — there is no way to tell "should
have decomposed" from "correctly didn't." Fuzzy string matching
(edit-distance) catches misspellings but not indirect references, since it
has no semantic knowledge. Only something that actually reads the question
can make that call.

### 3.2 The detector

`api/detect.py`:

```python
class CompanyDetector(Protocol):
    def detect(self, question: str, companies: list[dict]) -> list[str]: ...
```

Narrowed to the one thing this consumer needs, the same way `Generator` in
`generate.py` is narrowed to `.stream()`. `companies` is
`queries.load_companies()`'s existing `{cik, ticker, name, filings}` rows.

`AnthropicCompanyDetector`: one non-streaming Haiku call, `temperature=0`, a
system prompt listing the corpus's companies as `{ticker: name}` pairs and
asking which (if any) the question discusses. Output is constrained to
strict JSON: `{"tickers": ["MSFT", "AMZN"]}`. This is a classification call,
not open generation — small `max_tokens`, no streaming.

### 3.3 Validation

`parse_detected_companies(raw: str, known_tickers: set[str]) -> list[str]`
mirrors the defensive shape of `generate.parse_citations`: malformed JSON,
a non-list `tickers` field, or a ticker outside the known set all degrade to
dropping that entry rather than raising. A ticker the model invents that
isn't in `known_tickers` is silently excluded — never trusted just because
the model said so.

Output is capped at **4** tickers, truncated in order of first mention. The
cap is defined here because this is the producer; spec 4 (the consumer)
enforces it defensively too.

### 3.4 Failure handling

Any exception in the detection call (network error, timeout, malformed
response) is caught and treated as `[]` — "no company detected" is a valid,
safe result that every consumer already has to handle for genuinely
company-less questions, so detector failure costs nothing extra. This must
never surface as a user-facing error; it degrades to today's unscoped
retrieval.

This degrade-to-`[]` behavior is the caller's responsibility, not `detect()`'s
own: `AnthropicCompanyDetector.detect()` does not itself catch exceptions (a
missing API key or network failure propagates), because the only caller
before query decomposition lands is the eval CLI, where a loud failure is
more useful than a silent one. The query-decomposition plan's
`resolve_targets()` is where this guarantee must actually be implemented and
tested, since that is the first caller a real user-facing `/ask` request
reaches.

## 4. Testing and evaluation

This is scored differently from `golden.yaml`: that file measures retrieval
recall, and no retrieval is involved in judging a detector's raw output. A
small hand-labeled case set (e.g. `backend/evals/entity_resolution_cases.yaml`)
pairs questions with expected ticker lists:

- Comparison questions naming two companies correctly (MSFT vs AMZN).
- Single-company questions — confirms detection returns exactly the one
  obvious ticker and doesn't over-fire on a non-comparison question.
- Off-topic or company-less questions — expects `[]`.
- Indirect references ("the iPhone maker", "the Seattle retailer") and
  common misspellings.
- A question naming 5+ companies — confirms the cap holds.

Report accuracy against this set directly; it is not appended to
`results.jsonl`, since that file tracks retrieval/generation metrics and a
classifier accuracy number is a different kind of measurement. Given this
repo's own observed run-to-run LLM variance (`gold_sid_hit_rate` swings
0.75/0.8125 at temperature 0 on identical code), run this case set more than
once before trusting a single score.

## 5. Risks

| risk | mitigation |
| --- | --- |
| model hallucinates a ticker outside the known list | `parse_detected_companies` drops anything not in `known_tickers` |
| added latency/cost on every `/ask` call | explicit tradeoff, consistent with design.md §2's "pennies per answer"; spec 4 decides whether detection can be skipped when an explicit single-ticker filter already fully scopes the question |
| detection is non-deterministic run to run despite `temperature=0` | the hand-labeled eval is re-run multiple times, matching this repo's existing precedent for LLM-variance metrics |

## 6. Deferred

- Identifying which specific filing of a resolved company a question means
  (fiscal period handling, spec 3).
- Using the resolved company list to actually change retrieval (query
  decomposition, spec 4).
- Any user-facing filter UI (spec 4).
