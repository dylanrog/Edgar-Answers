"""One second chance for a misquoted citation (design §6.3 step 5).

The model sometimes states a true claim but misquotes its support -- it joins
two table rows, drops a word, reformats a figure. Verification stays an exact
normalized substring match; this only asks the model, once, to copy a passage
from the *same* excerpt, and the answer path then runs the same check on it.
Nothing here decides whether a citation is verified.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Protocol

from .generate import MODEL

# The same cap SYSTEM_PROMPT puts on every quote.
MAX_QUOTE_CHARS = 300
# The most repair calls one answer may make: each is a sequential round-trip
# after the prose has streamed, so a badly misquoting answer can't stall.
MAX_REPAIRS = 3
# Seconds before a repair call gives up and the original stays unverified.
TIMEOUT_SECONDS = 10.0

_FENCE = re.compile(r"^```[a-z]*\s*|\s*```$")
_NONE = re.compile(r"^none\W*$", re.IGNORECASE)
_WRAPPING = {'"': '"', "“": "”", "'": "'"}


class QuoteRepairer(Protocol):
    """The LLM, narrowed to re-quoting one failed citation."""

    def repair(self, claim: str, quote: str, excerpt: str) -> str | None:
        """A passage copied from `excerpt` that supports `claim`, or None."""
        ...


REPAIR_SYSTEM_PROMPT = """You correct a citation quote that does not appear in its source excerpt.

You are given a claim from an answer, the quote that was meant to support it,
and the excerpt the quote was meant to come from.

Reply with ONLY one contiguous passage copied character-for-character from the
excerpt that supports the claim: 300 characters or fewer, no quotation marks,
no ellipsis, no commentary. When the claim cites a figure from a table row,
copy from the start of that row through the figure and stop there. If no
single contiguous passage of the excerpt supports the claim, reply with
exactly: NONE"""


def build_repair_prompt(claim: str, quote: str, excerpt: str) -> str:
    return (
        f"Claim: {claim}\n\n"
        f"Quote that does not appear in the excerpt: {quote}\n\n"
        f"Excerpt:\n{excerpt}"
    )


def parse_repaired_quote(raw: str) -> str | None:
    """The quote in a repairer's raw reply, or None for NONE, an empty reply,
    or one over MAX_QUOTE_CHARS. A pair of wrapping quotation marks is
    dropped: the passage inside is still a substring if the whole was."""
    text = _FENCE.sub("", raw.strip()).strip()
    if len(text) >= 2 and _WRAPPING.get(text[0]) == text[-1]:
        text = text[1:-1].strip()
    if not text or _NONE.match(text) or len(text) > MAX_QUOTE_CHARS:
        return None
    return text


@dataclass
class AnthropicQuoteRepairer:
    model: str = MODEL
    max_tokens: int = 256
    timeout: float = TIMEOUT_SECONDS
    api_key: str | None = None

    def repair(self, claim: str, quote: str, excerpt: str) -> str | None:
        import anthropic

        client = anthropic.Anthropic(
            api_key=self.api_key or os.environ["ANTHROPIC_API_KEY"],
            timeout=self.timeout,
            max_retries=0,
        )
        message = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0,
            system=REPAIR_SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": build_repair_prompt(claim, quote, excerpt)}
            ],
        )
        raw = "".join(block.text for block in message.content if block.type == "text")
        return parse_repaired_quote(raw)
