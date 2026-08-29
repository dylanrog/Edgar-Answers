# Query Decomposition — Design

**Date:** 2026-08-29
**Status:** approved, not yet implemented
**Part of:** spec 4 of the 4-spec series started by `2026-08-12-tables-first-class-design.md`.
**Depends on:** entity resolution (spec 2, required) and, if it has landed,
fiscal period handling (spec 3, optional — this spec degrades gracefully to
ticker-only decomposition if spec 3 isn't built yet).

## 1. Problem

`design.md` §14 already names this gap:

> One embedding of "how do Apple and Microsoft describe supply chain risk"
> retrieves whichever filer's boilerplate scores highest rather than both...
> Splitting such a question into per-company retrievals is the v2 work.

Confirmed directly for this series' motivating question (§1 of the entity
resolution spec): 8/8 retrieved chunks were MSFT, 0 Amazon. Specs 2 and 3
make it *possible* to know which companies and filings a question means;
this spec is what actually changes retrieval's behavior once that
information exists.

## 2. Scope

**In scope:** turning resolved companies (and, if available, resolved
filings) into one-or-more `retrieve()` calls and merging the results; the
`/ask` request contract for an explicit multi-ticker filter; the frontend
multi-select UI; the golden-set eval arm that measures the whole pipeline
end-to-end.

**Out of scope:** the resolution mechanisms themselves (specs 2 and 3 own
those); section-targeted retrieval and reranking — still `design.md` §14
backlog, not attempted here; on-demand ticker ingestion — still a
`design.md` §2 non-goal.

## 3. Design

### 3.1 Target

The shape shared across this series:

```python
@dataclass(frozen=True)
class Target:
    ticker: str
    accessions: list[str] | None  # None if spec 3 isn't available, or abstained
```

### 3.2 Resolution and retrieval

```python
def resolve_targets(
    explicit_tickers: list[str] | None,
    question: str,
    detector: CompanyDetector,
    companies: list[dict],
) -> list[Target]:
    ...
```

Explicit filter wins if given (validated against the known ticker set,
capped at 4); otherwise the entity-resolution detector runs on the question
text; otherwise `[]` (today's fully-unscoped behavior, unchanged). When
fiscal period handling (spec 3) is available, each resolved ticker is passed
through `detect_periods` to fill in `accessions`; if spec 3 hasn't landed
yet, every `Target.accessions` is simply `None` and this spec still fixes
the company-crowding bug on its own.

```python
def retrieve_for_targets(
    conn, embedder, question, targets: list[Target], *, k_final: int, form_type=None,
) -> list[RetrievedChunk]:
    if not targets:
        return retrieve(conn, embedder, question, k_final=k_final, form_type=form_type)
    chunks = []
    for t in targets:
        chunks.extend(retrieve(
            conn, embedder, question,
            k_final=k_final, ticker=t.ticker, accessions=t.accessions, form_type=form_type,
        ))
    return chunks
```

**Concatenation, not RRF re-fusion.** Fusing all targets' results back
together with one shared ranking would reproduce the exact bug this spec
fixes — whichever company's chunks score higher would still crowd out the
other. Concatenating guarantees every named target is represented,
independent of how the arms compare to each other.

0-1 targets is exactly today's single `retrieve()` call — zero regression
risk for the 14 currently-passing single-ticker golden questions. 2+ targets
is the new path, each getting its own full `k_final` budget (so a two-company
question sees up to 16 chunks, not 8 split two ways) — chosen over an equal
split because thinning coverage as company count grows would just shift the
crowding problem down a level.

### 3.3 Orchestration and API contract

`answer_stream` (`api/answer.py`) swaps its single `retrieve()` call for
`resolve_targets` + `retrieve_for_targets`. Nothing downstream changes —
`build_user_message`, generation, and verification already treat
`chunks: list[RetrievedChunk]` generically and already label each chunk's
ticker inline, so a merged multi-company chunk list needs no prompt changes.

`AskRequest.filters` gains `tickers: list[str] | None` alongside the
existing `ticker` (kept for backward compatibility with the current
single-select behavior).

### 3.4 Frontend

`frontend/lib/api.ts`'s `AskFilters` gains `tickers?: string[]`.
`components/ask-form.tsx`'s single `<select>` becomes a multi-select capped
at 4 selections. No UI for periods — that stays entirely internal to
detection (spec 3).

## 4. Eval harness

`golden.yaml` gains an optional `group` field, defaulting to the entry's own
`id` — every existing row is unaffected, and `repin.py` needs no changes
since each row's shape (`ticker`, `accession`, `section`, `gold_sids`) is
untouched. New comparison rows are added as sibling entries sharing a
`group` and identical `question` text, each pinning one company's own
`ticker`/`accession`/`gold_sids` (MSFT-vs-AMZN-style cases).

`harness.py` gains a third scoring arm, `targeted_*`, alongside the existing
`scoped` and `unfiltered`: grouped by `group` (deduping the repeated question
text within a group), run the real `resolve_targets` (zero explicit
filter — all 10 companies as candidates) + `retrieve_for_targets` pipeline,
then score recall@k per row the same way `_score` already does. This one arm
does double duty: it validates the new comparison rows, and it re-scores
q004/q009 — already in the golden set, already failing today — under the new
pipeline, without touching how `scoped`/`unfiltered` are computed.

## 5. Rollout

Implement and report `targeted_*` alongside the existing two arms without
switching `/ask`'s default retrieval path yet. Only make `resolve_targets` +
`retrieve_for_targets` the default once `targeted_*` beats `unfiltered_*` and
does not regress the 14 currently-passing single-ticker questions — the same
measure-before-switching discipline `CLAUDE.md` already applies to chunking
and prompt changes.

## 6. Risks

| risk | mitigation |
| --- | --- |
| added latency/cost on every `/ask` call (detection, §1 of spec 2) | already an accepted tradeoff from spec 2; this is where it's actually paid |
| worst-case prompt size: 4 targets × 8 chunks × ~450 tokens | well within Haiku's context; noted as a bound, not treated as a problem |
| a detector failure cascading into a broken answer | already handled by specs 2/3's fallback-to-`[]`/`None` contracts; this spec's own job is confirming `retrieve_for_targets([])` reproduces today's exact behavior byte-for-byte (regression test) |

## 7. Deferred

- Section-targeted retrieval and reranking — `design.md` §14 backlog,
  untouched by this series.
- On-demand ticker ingestion — `design.md` §2 non-goal, untouched.
