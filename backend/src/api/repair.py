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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from .generate import MODEL

# The same cap SYSTEM_PROMPT puts on every quote.
MAX_QUOTE_CHARS = 300
# The most repair calls one answer may make: each is a sequential round-trip
# after the prose has streamed, so a badly misquoting answer can't stall.
MAX_REPAIRS = 3
# Seconds before a repair call gives up and the original stays unverified.
# A wall-clock bound on the whole call (see AnthropicQuoteRepairer.repair),
# not just on each socket read.
TIMEOUT_SECONDS = 10.0
CONNECT_TIMEOUT_SECONDS = 3.0
# Per answer: no new repair call starts once this long has passed since the
# first one started. With TIMEOUT_SECONDS that bounds the added latency at just
# under BUDGET + TIMEOUT (20 s) however many citations misquote.
REPAIR_BUDGET_SECONDS = 10.0

_FENCE = re.compile(r"^```[a-z]*\s*|\s*```$")
# "NONE", "none.", or any reply that opens with the upper-case sentinel
# ("NONE - the excerpt gives 391.0"). A quote that starts "None of our ..."
# is still a quote.
_NONE = re.compile(r"^(?:(?i:none)\W*$|NONE\b)")
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
no ellipsis, no commentary. A passage supports the claim only if it states the
claim's facts and figures itself. When the claim cites a figure from a table
row, copy from the start of that row through the figure and stop there.

If the excerpt only discusses the same topic, or gives a different figure than
the claim, the claim is not supported: reply with exactly NONE. Never pick a
nearby passage just because it is close."""


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


# Repair calls run here so the answer path can stop waiting at a wall-clock
# deadline. A call abandoned at the deadline finishes (or hits its httpx
# timeouts) in the background; its result is discarded.
_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="quote-repair")


@lru_cache(maxsize=4)
def _client(api_key: str, timeout: float):
    """One Anthropic client per process (per key and timeout): built on the
    first repair, then reused by every request's repairer."""
    import anthropic
    import httpx

    return anthropic.Anthropic(
        api_key=api_key,
        timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT_SECONDS),
        max_retries=0,
    )


@dataclass
class AnthropicQuoteRepairer:
    model: str = MODEL
    max_tokens: int = 256
    timeout: float = TIMEOUT_SECONDS
    api_key: str | None = None

    def _anthropic(self):
        return _client(self.api_key or os.environ["ANTHROPIC_API_KEY"], self.timeout)

    def repair(self, claim: str, quote: str, excerpt: str) -> str | None:
        # httpx's timeouts bound each phase (connect 3 s; each read, write and
        # pool wait 10 s), not the call as a whole. future.result bounds the
        # whole call at `timeout` seconds of wall clock: past it, this raises
        # TimeoutError and the answer path keeps the unverified original. With
        # REPAIR_BUDGET_SECONDS that bounds repair's added latency per answer
        # at under BUDGET + TIMEOUT (20 s).
        future = _EXECUTOR.submit(self._call, claim, quote, excerpt)
        return future.result(timeout=self.timeout)

    def _call(self, claim: str, quote: str, excerpt: str) -> str | None:
        message = self._anthropic().messages.create(
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
