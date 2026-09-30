from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg

from . import queries
from .conversation import ConversationRewriter, load_recent_turns, save_turn
from .detect import CompanyDetector, PeriodDetector
from .generate import (
    SYSTEM_PROMPT,
    AnswerSplitter,
    Generator,
    build_user_message,
    parse_citations,
)
from .rewrite import QueryRewriter
from .targets import resolve_targets, retrieve_for_targets
from .verify import VerifiedCitation, verify_citation

logger = logging.getLogger(__name__)


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
    query_rewriter: QueryRewriter | None = None,
    conversation_id: str | None = None,
    conversation_rewriter: ConversationRewriter | None = None,
) -> Iterator[AnswerEvent]:
    """The query path (design §6): [resolve follow-up] -> resolve targets ->
    retrieve -> generate -> verify -> stream."""
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
        chunks = retrieve_for_targets(
            conn, embedder, standalone_question, targets, k_final=k_final, form_type=form_type
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
            for delta in generator.stream(SYSTEM_PROMPT, user_message):
                text = splitter.feed(delta)
                if text and attempt == 0:
                    answer_parts.append(text)
                    yield AnswerEvent("token", {"text": text})
            tail = splitter.finish()
            if tail and attempt == 0:
                answer_parts.append(tail)
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
            cells = queries.load_chunk_cells(
                conn, chunk.filing_id, chunk.sid_start, chunk.sid_end
            )
            verified.append(verify_citation(citation, chunk, sentences, cells))

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
                    "cells": [{"sid": sid, "cell": cell} for sid, cell in citation.cells],
                },
            )

        if conversation_id is not None:
            save_turn(
                conn,
                conversation_id,
                question=question,
                standalone_question=standalone_question,
                answer_text="".join(answer_parts),
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
        # raising here would truncate the stream with no explanation.
        yield AnswerEvent("error", {"message": f"{type(exc).__name__}: {exc}"})
