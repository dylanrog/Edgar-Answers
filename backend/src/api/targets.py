from __future__ import annotations

import logging
from dataclasses import dataclass

from . import queries
from .detect import MAX_COMPANIES, CompanyDetector, PeriodDetector
from .retrieval import RetrievedChunk, retrieve

logger = logging.getLogger(__name__)


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
                question,
                k_final=k_final,
                k_each=k_each,
                ticker=target.ticker,
                accessions=target.accessions,
                form_type=form_type,
            )
        )
    return chunks


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
        targets.append(Target(ticker=ticker, accessions=accessions))
    return targets
