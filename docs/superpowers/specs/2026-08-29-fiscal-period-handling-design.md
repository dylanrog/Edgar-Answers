# Fiscal Period Handling — Design

**Date:** 2026-08-29
**Status:** approved, not yet implemented
**Part of:** spec 3 of the 4-spec series started by `2026-08-12-tables-first-class-design.md`.
**Depends on:** entity resolution (`2026-08-29-entity-resolution-design.md`,
spec 2) to know which company's filings to consider when there is no explicit
ticker filter.

## 1. Problem

`design.md` §6 deliberately deferred a "year" filter on `/ask`:

> Filtering by fiscal year needs a decision about whether "year" means
> `filing_date` or `period_end`, which differ for every 10-K; shipping the
> ambiguity would be worse than not shipping the filter.

Separately, `CLAUDE.md` documents the resulting gap as the corpus's real
weakness: q009 asks for Q2 FY2024 gross margin; the model reports receiving
Q1 2024, Q3 2024, Q2 2025, Q1 2025, Q1 2026 and Q3 2026 — every quarter but
the one asked for — and refuses, even though the pinned sid is confirmed in
the DB. q004 fails the same way on a fiscal-2023 RSU figure. AAPL files
near-identical MD&A tables every quarter with only the numbers changing, so
vector and lexical similarity alone cannot separate periods — the wrong
quarter's boilerplate reads as similar as the right one.

## 2. Scope

**In scope:** resolving a question, given a known company, to the specific
filing(s) it refers to (by accession number), so retrieval can be narrowed to
exactly those filings before ranking runs.

**Out of scope:** identifying the company itself (spec 2); running retrieval
across multiple companies and merging (spec 4); any general fiscal-calendar
modeling (Apple's fiscal year ends in September, most others in December) —
this spec sidesteps that problem rather than solving it, per §3.1.

## 3. Design

### 3.1 Why accession-pinning, not a fiscal year

A coarse `fiscal_year: 2024` field would reopen exactly the ambiguity
design.md already ruled out — is "2024" the filing's `filing_date` year or
its `period_end` year? They differ for every 10-K. Accession-pinning
sidesteps this entirely: instead of asking the model to compute a year, give
it the company's actual filing list — `{accession, form_type, filing_date,
period_end}`, all pre-existing columns on `filings` — and have it pick the
specific row(s) the question refers to. This is a much easier task for an
LLM (select from an enumerated, disambiguated list) than date arithmetic, and
it produces a value (`accession`) that retrieval can filter on exactly.

### 3.2 Detection

A second, separate call from entity resolution's (kept as its own function so
each spec in this series is independently understandable and testable):

```python
def detect_periods(question: str, ticker: str, filings: list[dict]) -> list[str]:
    ...  # returns accession numbers
```

`filings` is the resolved company's rows from the `filings` table. Output is
strict JSON (`{"accessions": [...]}`), validated the same defensive way as
spec 2's detector: an accession that doesn't actually belong to the given
ticker's filing list is dropped. No confident pin → `[]`, meaning "scope to
the ticker only" — today's behavior, unharmed. A future optimization could
merge this into entity resolution's single call; deferred here to keep this
spec's surface area independently reviewable.

### 3.3 Retrieval

`_filters()` in `retrieval.py` gains an `accessions: list[str] | None` clause,
the same shape as the existing `ticker` and `form_type` clauses:

```python
if accessions:
    clauses.append("f.accession = ANY(%s)")
    params.append(accessions)
```

`retrieve()`, `vector_search()`, and `lexical_search()` all gain the
corresponding parameter, threaded through exactly like `ticker` is today.

## 4. Data model

No migration. `filings.accession`, `.form_type`, `.filing_date`, and
`.period_end` already exist; this spec only reads them.

## 5. Testing and evaluation

A hand-labeled case set, structured like spec 2's, pairing period-referencing
questions with their expected accession(s) — starting with q004 and q009
themselves, since their correct accessions are already known (`evals verify`
already confirms their pinned sids exist in the DB; what's missing is
retrieval actually surfacing them).

A narrow, targeted retrieval check (not a new eval-harness arm — that's
spec 4's job, covering the full pipeline): re-run q004 and q009 through
`retrieve(ticker=..., accessions=<resolved>)` directly and confirm the
previously-missing gold sid now appears in the top-k.

## 6. Risks

| risk | mitigation |
| --- | --- |
| a wrong pin actively excludes the correct filing, which is worse than no pin | detector must abstain (return `[]`) when uncertain; §5's targeted check confirms no accession-pinned question regresses versus its ticker-only baseline before this ships |
| an accession is hallucinated or belongs to a different company | validated against the resolved ticker's own filing list; dropped if it doesn't match |
| exact-set-equality scoring can't express "multiple filings are equally correct" (e.g. a comparative figure repeated verbatim in two consecutive 10-Ks) | recorded per-case in fiscal_period_cases.yaml (see p001); do not treat this as a reason to tune the prompt toward preferring later comparative filings |

## 7. Deferred

- Merging this detection call with entity resolution's into one round-trip.
- Multi-target merge across companies (spec 4).
- General fiscal-calendar normalization beyond reading filing metadata
  directly.
- Any user-facing period filter — never proposed; this is auto-detect only.
