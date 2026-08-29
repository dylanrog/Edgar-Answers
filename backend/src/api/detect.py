from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

from .generate import MODEL

# entity-resolution spec §3.3: at most this many companies decompose into
# separate retrievals downstream (query decomposition). Defined here since
# this module is the producer.
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


DETECTION_SYSTEM_PROMPT = (
    """You identify which companies, if any, a question about SEC filings discusses.

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
)


def build_detection_prompt(question: str, companies: list[dict]) -> str:
    roster = "\n".join(f"{c['ticker']}: {c['name']}" for c in companies)
    return f"Companies:\n{roster}\n\nQuestion: {question}"


@dataclass
class AnthropicCompanyDetector:
    model: str = MODEL
    max_tokens: int = 256
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


class PeriodDetector(Protocol):
    """The LLM, narrowed to the one thing fiscal period handling needs."""

    def detect(self, question: str, ticker: str, filings: list[dict]) -> list[str]:
        """Return the accessions (from `filings`) the question refers to."""
        ...


def parse_detected_periods(raw: str, known_accessions: set[str]) -> list[str]:
    """Defensive parse of a period detector's raw response into an accession list.

    Mirrors parse_detected_companies's contract: a hallucinated or
    wrong-company accession, malformed JSON, or a response with no JSON
    object at all all degrade to `[]` -- "no period pin" means "scope to
    the ticker only," which is always a safe fallback.
    """
    text = _FENCE.sub("", raw.strip())
    match = _OBJECT.search(text)
    if match is None:
        return []
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    accessions = payload.get("accessions")
    if not isinstance(accessions, list):
        return []
    seen: list[str] = []
    for accession in accessions:
        if (
            isinstance(accession, str)
            and accession in known_accessions
            and accession not in seen
        ):
            seen.append(accession)
    return seen


DETECTION_PERIOD_SYSTEM_PROMPT = (
    """You identify which specific SEC filing(s) of one company a question refers to.

You will be given the company's filings, each shown as its accession number,
form type, filing date, and period-end date. Decide which filing(s), if any,
the question is asking about, based on the fiscal year, quarter, or other
period language in the question.

Respond with strict JSON and nothing else:

{"accessions": ["0000320193-24-000123"]}

Rules:
1. Only use accession numbers that appear in the filing list you were given.
   Never invent one.
2. If the question does not reference a specific period, or you are not
   confident which filing(s) it means, respond with {"accessions": []}.
   Do not guess.
3. A question comparing two periods may name two filings.
"""
)


def build_period_prompt(question: str, ticker: str, filings: list[dict]) -> str:
    roster = "\n".join(
        f"{f['accession']} | {f['form_type']} | filed {f['filing_date']}"
        f" | period_end {f['period_end'] or 'unknown'}"
        for f in filings
    )
    return f"Company: {ticker}\nFilings:\n{roster}\n\nQuestion: {question}"


@dataclass
class AnthropicPeriodDetector:
    model: str = MODEL
    max_tokens: int = 256
    api_key: str | None = None

    def detect(self, question: str, ticker: str, filings: list[dict]) -> list[str]:
        import anthropic

        client = anthropic.Anthropic(
            api_key=self.api_key or os.environ["ANTHROPIC_API_KEY"]
        )
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0,
            system=DETECTION_PERIOD_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": build_period_prompt(question, ticker, filings),
                }
            ],
        )
        raw = "".join(block.text for block in message.content if block.type == "text")
        known_accessions = {f["accession"] for f in filings}
        return parse_detected_periods(raw, known_accessions)
