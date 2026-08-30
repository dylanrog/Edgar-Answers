from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Protocol

from .generate import MODEL

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class QueryRewriter(Protocol):
    """The LLM, narrowed to the one thing per-target query rewriting needs."""

    def rewrite(self, question: str, ticker: str, company_name: str) -> str:
        """Return a standalone, single-company version of `question`."""
        ...


def parse_rewritten_query(raw: str, fallback: str) -> str:
    """Defensive parse of a rewriter's raw response into a search query.

    Malformed JSON, no JSON object, a missing/non-string `query` field, or a
    blank query all degrade to `fallback` (the original question) -- a
    rewrite failure must never make retrieval worse than not rewriting at
    all.
    """
    text = _FENCE.sub("", raw.strip())
    match = _OBJECT.search(text)
    if match is None:
        return fallback
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return fallback
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        return fallback
    return query.strip()


REWRITE_SYSTEM_PROMPT = (
    """You rewrite a question about SEC filings into a standalone version
scoped to one company, for use as a search query.

You will be given one company (as "TICKER: Name") and the original
question, which may discuss more than one company. Rewrite the question to
be entirely about the given company: keep the financial concepts, metrics,
and time periods; drop every other named company.

Respond with strict JSON and nothing else:

{"query": "Microsoft revenue growth fiscal 2023 to fiscal 2024"}

Rules:
1. Keep the rewritten query focused on the financial concepts, metrics, and
   time periods from the original question.
2. Remove every company name or reference other than the one given.
3. Do not add any fact, number, or company not present in the original
   question.
"""
)


def build_rewrite_prompt(question: str, ticker: str, company_name: str) -> str:
    return f"Company: {ticker}: {company_name}\n\nQuestion: {question}"


@dataclass
class AnthropicQueryRewriter:
    model: str = MODEL
    max_tokens: int = 256
    api_key: str | None = None

    def rewrite(self, question: str, ticker: str, company_name: str) -> str:
        import anthropic

        client = anthropic.Anthropic(
            api_key=self.api_key or os.environ["ANTHROPIC_API_KEY"]
        )
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0,
            system=REWRITE_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": build_rewrite_prompt(question, ticker, company_name),
                }
            ],
        )
        raw = "".join(block.text for block in message.content if block.type == "text")
        return parse_rewritten_query(raw, question)
