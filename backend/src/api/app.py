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
from .rewrite import AnthropicQueryRewriter, QueryRewriter

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


def get_query_rewriter() -> QueryRewriter:
    return AnthropicQueryRewriter()


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
