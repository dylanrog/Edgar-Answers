from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

from .generate import MODEL

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
