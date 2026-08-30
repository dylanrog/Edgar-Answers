# Per-Target Query Rewriting — Design

**Date:** 2026-08-30
**Status:** proposed
**Part of:** follow-on to the entity-resolution / fiscal-period-handling /
query-decomposition series (specs dated 2026-08-29, all merged to main).
Fixes a documented limitation of that series rather than opening new scope.
**Depends on:** query decomposition (`backend/src/api/targets.py`, merged
via PR #19). This spec's whole surface is `resolve_targets` /
`retrieve_for_targets`.

## 1. Problem

Query decomposition fixed the company-crowding bug: a comparison question
like "Who achieved more growth from 23 to 24? Microsoft or Amazon?" now
retrieves from both MSFT and AMZN instead of just whichever one's chunks
scored highest. But the golden-set rows added for exactly this case
(`qc001`/`qc002`) still miss under every eval arm, including the new
`targeted_*` arm — documented in `golden.yaml` as a retrieval-ranking
limitation, not a decomposition defect, and left un-re-pinned deliberately.

Root cause, confirmed by reading `retrieve_for_targets`
(`backend/src/api/targets.py:19-57`): every per-target `retrieve()` call is
passed the exact same, unmodified `question` string:

```python
for target in targets:
    chunks.extend(
        retrieve(
            conn, embedder, question,   # same raw text, every target
            ...
            ticker=target.ticker,
            accessions=target.accessions,
            ...
        )
    )
```

Microsoft's scoped retrieval is searched with a query that also contains
"Amazon" — a term with zero relevance inside a Microsoft-only filing — and
Amazon's retrieval carries "Microsoft" the same way. Both arms feel this:
the vector arm embeds a sentence half of which describes the wrong company,
and the lexical arm (`plainto_tsquery`, which ORs every term — see
`retrieval.py`'s `_OR_TSQUERY` comment) ranks chunks up for matching a term
that can never appear in that company's own filings. This is why the
specific pinned dollar-figure sentence doesn't rank into the top 8 for
either company on a comparison-phrased question, even though both
companies' real content is present in the corpus and gets retrieved for
*some* chunks (confirmed directly in the query-decomposition series: both
companies' content reaches the model, citations verify, but not the
specific pinned sids).

## 2. Scope

**In scope:** rewriting each target's search query to a standalone,
single-company version of the question before that target's `retrieve()`
call runs; a new `QueryRewriter` detector following the same
Protocol + Anthropic-implementation + defensive-parse shape as
`CompanyDetector`/`PeriodDetector`; wiring into `resolve_targets` /
`retrieve_for_targets`; an eval bracket proving `targeted_recall@10`
improves on `qc001`/`qc002` without regressing the 16 pre-existing
questions.

**Out of scope:** reranking / cross-encoder (`design.md` §14 backlog — a
heavier, separate lever; this spec is the cheaper thing to try first);
changing `retrieve()`'s RRF fusion logic itself; conversation memory
(separate spec, `2026-08-30-conversation-memory-design.md`).

## 3. Design

### 3.1 Only fires when it can help

Rewriting only helps when a question names more than one company — a
single-target question has no other company's terms to strip. `resolve_targets`
invokes the rewriter only when `len(tickers) > 1`, so the common
single-company case (the majority of `golden.yaml`, and of real traffic)
pays no extra latency or cost.

### 3.2 `QueryRewriter` (new module `backend/src/api/rewrite.py`)

Same shape as `detect.py`'s two detectors — a `Protocol`, a defensive
parser that degrades to a safe fallback instead of raising, and an
`Anthropic*` implementation sharing the `MODEL` constant from `generate.py`:

```python
class QueryRewriter(Protocol):
    def rewrite(self, question: str, ticker: str, company_name: str) -> str:
        """Return a standalone, single-company version of `question`."""
        ...
```

```python
def parse_rewritten_query(raw: str, fallback: str) -> str:
    """Defensive parse. Malformed JSON, no JSON object, or a blank/missing
    `query` field all degrade to `fallback` (the original question) -- a
    rewrite failure must never make retrieval worse than not rewriting."""
    ...
```

Prompt shape (mirrors `DETECTION_PERIOD_SYSTEM_PROMPT`'s style):

```
You rewrite a question about SEC filings into a standalone version scoped
to one company, for use as a search query.

You will be given the original question and one company it discusses.
Rewrite the question to be entirely about that company: keep the financial
concepts, metrics, and time periods; drop every other named company.

Respond with strict JSON and nothing else:
{"query": "Microsoft revenue growth fiscal 2023 to fiscal 2024"}
```

`AnthropicQueryRewriter` follows `AnthropicPeriodDetector`'s exact shape:
`model: str = MODEL`, `max_tokens: int = 256`, `api_key: str | None = None`,
`temperature=0`.

### 3.3 Wiring into `Target` and `resolve_targets`

`Target` gains a field defaulting to preserve today's behavior exactly when
rewriting isn't wired or doesn't fire:

```python
@dataclass(frozen=True)
class Target:
    ticker: str
    accessions: list[str] | None
    search_query: str | None = None  # None => use the original question, unchanged
```

`resolve_targets` gains an optional `query_rewriter: QueryRewriter | None =
None` parameter (same optionality pattern as `period_detector`),
populating `search_query` only when `len(tickers) > 1` and the rewriter is
present. A rewriter exception degrades to `search_query=None` (original
question), matching the existing "a detector failure never breaks the
request" contract already documented on `resolve_targets`.

`retrieve_for_targets` uses `target.search_query or question` as the
string passed into `retrieve()` for that target, in place of the bare
`question`.

### 3.4 Threading through `answer_stream` and the API

`answer_stream` gains a `query_rewriter: QueryRewriter | None = None`
parameter, threaded straight through to `resolve_targets` — mirroring
exactly how `period_detector` was added in the query-decomposition series.
`app.py` gains `get_query_rewriter()` (same DI shape as
`get_company_detector`/`get_period_detector`) and passes it to
`answer_stream`. `evals/__main__.py`'s `cmd_run` builds one `query_rewriter`
alongside the existing two detectors, threaded into `run_retrieval_eval`.

## 4. Eval harness

No new `golden.yaml` rows needed — `qc001`/`qc002` already exist and
already fail for exactly this reason. `harness.py`'s existing
`_score_targeted` gains a `query_rewriter` parameter, passed through to
`resolve_targets`/`retrieve_for_targets` the same way `period_detector`
already is.

## 5. Rollout

Run `evals run --retrieval-only` before and after wiring the rewriter into
the default `/ask` path — the same measure-before-switching discipline the
query-decomposition spec used for `targeted_*` vs `unfiltered_*`.
Acceptance: `qc001`/`qc002`'s `targeted_recall@10` improves without
regressing the 16 pre-existing questions' `targeted_recall@10`. If
`qc001`/`qc002` still miss after this change, that is real evidence the gap
needs reranking (§14 backlog) rather than query rewriting — either way this
becomes a measured question, not a debugging guess.

## 6. Risks

| risk | mitigation |
| --- | --- |
| one more sequential Haiku call per detected company on a multi-company question (`design.md` §6 already documents "up to ~5 sequential calls"; this adds up to 4 more in the worst case) | scoped to fire only when `len(tickers) > 1` — the common case is unaffected; if latency proves unacceptable in practice, folding the rewrite into the existing period-detector call (one prompt, two output fields) is the documented fallback, traded against mixing two concerns in one prompt |
| rewriter degrades retrieval instead of improving it (e.g., drops a metric term along with the other company's name) | the eval bracket in §5 catches this directly before the switch is made default; `search_query=None` fallback means a bad rewrite is opt-out, not silently permanent |
| rewriter invents a company-specific detail not present in the original question | prompt explicitly says "keep... drop every other named company," not "add information"; not enforced by the parser today — worth a targeted eval row if this shows up in practice |

## 7. Deferred

- Reranking (cross-encoder) — `design.md` §14 backlog, independent of this fix.
- Section-targeted retrieval — same backlog entry, untouched.
