from __future__ import annotations

import dataclasses
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

import psycopg

from pipeline.canonicalize import Sentence
from pipeline.tables import Cell

from . import queries
from .conversation import ConversationRewriter, load_recent_turns, save_turn
from .detect import CompanyDetector, PeriodDetector
from .figures import claim_sides, narrow_cells, quote_states, significant_figures
from .generate import (
    SYSTEM_PROMPT,
    AnswerSplitter,
    Generator,
    PossessiveDeduper,
    build_user_message,
    parse_citations,
)
from .repair import MAX_QUOTE_CHARS, MAX_REPAIRS, REPAIR_BUDGET_SECONDS, QuoteRepairer
from .retrieval import RetrievedChunk
from .rewrite import QueryRewriter
from .targets import resolve_targets, retrieve_for_targets
from .verify import Citation, VerifiedCitation, verify_citation

logger = logging.getLogger(__name__)

# Patched in tests to simulate slow repairs without sleeping.
_clock = time.monotonic
_MARKER = re.compile(r"\[\d+\]")


@dataclass(frozen=True)
class AnswerEvent:
    name: str  # token | citation | done | error | resolved | progress
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
    query_rewriter: QueryRewriter | None = None,
    conversation_id: str | None = None,
    conversation_rewriter: ConversationRewriter | None = None,
    quote_repairer: QuoteRepairer | None = None,
) -> Iterator[AnswerEvent]:
    """The query path (design §6): [resolve follow-up] -> resolve targets ->
    retrieve -> generate -> verify [-> repair a misquote] -> stream."""
    try:
        history = load_recent_turns(conn, conversation_id) if conversation_id else []
        standalone_question = question
        if history and conversation_rewriter is not None:
            try:
                standalone_question = conversation_rewriter.resolve(question, history)
            except Exception as exc:  # noqa: BLE001 -- degrades to the raw follow-up
                logger.warning(
                    "conversation_rewriter.resolve failed, using the raw question: %s",
                    exc,
                )
        if standalone_question != question:
            yield AnswerEvent("resolved", {"standalone_question": standalone_question})

        targets = resolve_targets(
            conn,
            standalone_question,
            explicit_tickers=tickers,
            company_detector=company_detector,
            period_detector=period_detector,
            query_rewriter=query_rewriter,
        )
        narrowed = [a for t in targets if t.accessions is not None for a in t.accessions]
        yield AnswerEvent(
            "progress",
            {
                "stage": "targets",
                "tickers": [t.ticker for t in targets],
                "filings": queries.load_filing_summaries(conn, narrowed),
            },
        )
        chunks = retrieve_for_targets(
            conn, embedder, standalone_question, targets, k_final=k_final, form_type=form_type
        )
        per_filing: dict[str, dict] = {}
        for chunk in chunks:
            entry = per_filing.setdefault(
                chunk.accession,
                {
                    "accession": chunk.accession,
                    "ticker": chunk.ticker,
                    "form_type": chunk.form_type,
                    "filing_date": chunk.filing_date.isoformat(),
                    "passages": 0,
                },
            )
            entry["passages"] += 1
        yield AnswerEvent(
            "progress",
            {"stage": "retrieved", "passages": len(chunks), "filings": list(per_filing.values())},
        )
        user_message = build_user_message(standalone_question, chunks)

        # Design §6.2: one retry if the trailing block does not parse, then
        # render the answer with an "unverified answer" notice rather than
        # failing the request outright. The retry's prose is discarded -- the
        # first attempt's answer has already streamed to the client, and
        # replacing it mid-stream would be worse than keeping it. We are
        # re-rolling only for the citation block.
        answer_parts: list[str] = []
        splitter = AnswerSplitter()
        citations = None
        for attempt in range(2):
            splitter = AnswerSplitter()
            # Prose only (never the raw buffer, so quotes and the citation
            # block are untouched). answer_parts is what the client saw, so
            # marker positions in `prose` match the streamed text.
            deduper = PossessiveDeduper()
            for delta in generator.stream(SYSTEM_PROMPT, user_message):
                text = deduper.feed(splitter.feed(delta))
                if text and attempt == 0:
                    answer_parts.append(text)
                    yield AnswerEvent("token", {"text": text})
            tail = deduper.feed(splitter.finish()) + deduper.finish()
            if tail and attempt == 0:
                answer_parts.append(tail)
                yield AnswerEvent("token", {"text": tail})
            citations = parse_citations(splitter.raw)
            if citations is not None:
                break

        yield AnswerEvent("progress", {"stage": "verifying", "quotes": len(citations or [])})

        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        prose = "".join(answer_parts)
        repairs = 0
        repair_deadline: float | None = None
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
            cells = queries.load_chunk_cells(
                conn, chunk.filing_id, chunk.sid_start, chunk.sid_end
            )
            result = verify_citation(citation, chunk, sentences, cells)
            # Repair (design §6.3 step 5): only a first-attempt citation, whose
            # marker sits in prose the visitor saw, and only for a claim that
            # states a figure the repaired quote must then state too.
            claim = (
                _claim(prose, citation.marker)
                if not result.verified
                and quote_repairer is not None
                and attempt == 0
                and repairs < MAX_REPAIRS
                else None
            )
            if claim is not None:
                now = _clock()
                if repair_deadline is None:
                    repair_deadline = now + REPAIR_BUDGET_SECONDS
                if now < repair_deadline:
                    repairs += 1
                    scales = queries.load_table_scales(conn, chunk.filing_id)
                    result = _repair(
                        quote_repairer, claim, citation, chunk, sentences, cells, scales
                    ) or result
            # Emphasis only: a whole multi-year row verifies, but the viewer
            # should mark just the figures the answer states (design §6.3).
            # Not after a retry: the retry's markers belong to prose the client
            # never saw, so the shown prose can't say which figure they cite.
            if attempt == 0:
                result = narrow_cells(result, cells, prose)
            verified.append(result)

        for citation in verified:
            payload = {
                "marker": citation.marker,
                "verified": citation.verified,
                "accession": citation.accession,
                "ticker": citation.ticker,
                "form_type": citation.form_type,
                "filing_date": citation.filing_date,
                "sids": citation.sids,
                "quote": citation.quote,
                "cells": [{"sid": sid, "cell": cell} for sid, cell in citation.cells],
            }
            # Present only when true, so a first-pass citation's payload is
            # exactly what it was before repair existed (design §6.4).
            if citation.repaired:
                payload["repaired"] = True
            yield AnswerEvent("citation", payload)

        if conversation_id is not None:
            save_turn(
                conn,
                conversation_id,
                question=question,
                standalone_question=standalone_question,
                answer_text=prose,
                tickers=[target.ticker for target in targets],
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
        # raising here would truncate the stream with no explanation. The 200 is
        # already in the access log too, so this line is the only server-side
        # trace of the failure (`fly logs`).
        logger.exception("answer_stream failed")
        yield AnswerEvent("error", {"message": f"{type(exc).__name__}: {exc}"})


def _claim(prose: str, marker: int) -> str | None:
    """The words the answer says around `[marker]` (both sides, as narrowing
    reads them, markers removed). None when the marker is not in the prose,
    or the claim states no significant figure: then nothing could confirm
    that a repaired quote supports the claim rather than merely sharing its
    topic."""
    sides = claim_sides(prose, marker)
    if sides is None:
        return None
    text = " ".join(_MARKER.sub(" ", " ".join(sides)).split())
    return text if significant_figures(text) else None


def _repair(
    repairer: QuoteRepairer,
    claim: str,
    citation: Citation,
    chunk: RetrievedChunk,
    sentences: list[Sentence],
    cells: list[Cell],
    scales: dict[int, Decimal | None],
) -> VerifiedCitation | None:
    """One re-quote of a failed citation against the same chunk (design §6.3
    step 5), checked by the same verify_citation. None -- keep the original
    unverified citation -- on any failure: an exception, no quote, an
    over-long quote, a quote that still does not match, or one that does not
    state every one of the claim's figures in its unit, table scale, period
    and sign (figures.quote_states) -- anything less could launder an
    invented figure into a verified citation."""
    try:
        quote = repairer.repair(claim, citation.quote, chunk.text)
    except Exception as exc:  # noqa: BLE001 -- degrades to the unverified original
        logger.warning("quote repair failed for marker %s: %s", citation.marker, exc)
        return None
    if not quote or len(quote) > MAX_QUOTE_CHARS:
        return None
    repaired = verify_citation(
        Citation(marker=citation.marker, chunk_id=citation.chunk_id, quote=quote),
        chunk,
        sentences,
        cells,
    )
    if not repaired.verified:
        logger.info("quote repair for marker %s did not verify", citation.marker)
        return None
    by_key = {(cell.sid, cell.cell_index): cell for cell in cells}
    quote_cells = [by_key[key] for key in repaired.cells if key in by_key]
    if not quote_states(repaired.quote, quote_cells, claim, scales):
        logger.info(
            "quote repair for marker %s verified but does not state every claim figure",
            citation.marker,
        )
        return None
    logger.info("quote repair verified marker %s", citation.marker)
    return dataclasses.replace(repaired, repaired=True)
