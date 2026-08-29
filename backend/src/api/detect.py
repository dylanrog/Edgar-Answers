from __future__ import annotations

import json
import re
from typing import Protocol

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
