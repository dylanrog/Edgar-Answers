from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from .retrieval import RetrievedChunk
from .verify import Citation

# Design §2: low-volume, pennies per answer. Small local models are unreliable
# at verbatim structured quoting, which is the entire product.
MODEL = "claude-haiku-4-5"
# Design §11: a hard output cap keeps per-answer spend bounded.
MAX_OUTPUT_TOKENS = 1500
FENCE = "```"
# Spec 2026-09-29 §5.5. The label itself tells the model the line is not
# quotable; rule 4 of SYSTEM_PROMPT says so again.
CONTEXT_LABEL = "Table context (for reading columns; not quotable):"

SYSTEM_PROMPT = """You answer questions about SEC filings using only the excerpts provided.

Rules:
1. Answer ONLY from the provided excerpts. If they do not contain the answer, say
   so plainly and stop — do not fall back on general knowledge about the company.
2. Attach an inline marker to every factual claim: [1], [2], and so on, numbered
   from 1 in the order they first appear.
3. When excerpts from more than one filing support the answer, cite each of
   them. Do not collapse several filings into a single citation, and do not
   answer only from whichever excerpt appeared first.
4. Lines labelled "Table context" give a table's caption, scale and column
   headings so you can tell which column a figure sits in. Use them to read
   the table, but never quote them: quotes come only from excerpt text.
5. When you cite a figure from a table row, quote from the start of the row
   through that figure and stop there.
6. After the answer, emit a fenced JSON block and nothing after it:

```json
{"citations": [{"marker": 1, "chunk_id": 8123, "quote": "verbatim text from that chunk"}]}
```

Every quote must be copied character-for-character from the excerpt whose
chunk_id you cite, and must be 300 characters or fewer. Do not paraphrase,
reformat, join sentences with an ellipsis, or fix typography inside a quote —
quotes are checked against the source text and a mismatch is shown to the user
as unverified. Prefer a short exact quote over a long approximate one."""


class Generator(Protocol):
    """The LLM, narrowed to the one thing the query path needs."""

    def stream(self, system: str, user: str) -> Iterator[str]:
        """Yield answer text deltas in order."""
        ...


@dataclass
class AnthropicGenerator:
    model: str = MODEL
    max_tokens: int = MAX_OUTPUT_TOKENS
    api_key: str | None = None

    def stream(self, system: str, user: str) -> Iterator[str]:
        import anthropic

        client = anthropic.Anthropic(
            api_key=self.api_key or os.environ["ANTHROPIC_API_KEY"]
        )
        # No `thinking` (Haiku 4.5 does not do adaptive thinking, and omitting
        # the parameter means none) and no `output_config.effort` (rejected on
        # Haiku 4.5). temperature=0 so the eval is reproducible run to run.
        with client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            yield from stream.text_stream


def build_user_message(question: str, chunks: list[RetrievedChunk]) -> str:
    blocks = []
    for c in chunks:
        header = f"[chunk_id={c.chunk_id}] {c.ticker} {c.form_type} {c.accession} ({c.section})"
        context = "".join(
            f"\n{CONTEXT_LABEL} {line}" for line in c.context.splitlines() if line
        )
        blocks.append(f"{header}{context}\n{c.text}")
    excerpts = "\n\n".join(blocks) if blocks else "(no excerpts were retrieved)"
    return f"Excerpts:\n\n{excerpts}\n\nQuestion: {question}"


class AnswerSplitter:
    """Splits a streamed response into user-visible prose and a trailing block.

    The answer streams token-by-token while the JSON block must not — it is
    machinery, not prose. Until the fence appears we hold back the last
    len(FENCE) - 1 characters so a fence opener can never be emitted one
    backtick at a time; once it appears, emission stops there for good.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._emitted = 0
        self._fence_at: int | None = None

    @property
    def raw(self) -> str:
        return self._buffer

    def feed(self, delta: str) -> str:
        self._buffer += delta
        if self._fence_at is None:
            index = self._buffer.find(FENCE)
            if index != -1:
                self._fence_at = index
        limit = (
            self._fence_at
            if self._fence_at is not None
            else max(0, len(self._buffer) - (len(FENCE) - 1))
        )
        return self._take(limit)

    def finish(self) -> str:
        limit = self._fence_at if self._fence_at is not None else len(self._buffer)
        return self._take(limit)

    def _take(self, limit: int) -> str:
        if limit <= self._emitted:
            return ""
        out = self._buffer[self._emitted : limit]
        self._emitted = limit
        return out


_WORD = r"[\w&-]"
_POSSESSIVE = re.compile(
    rf"(?<!{_WORD})([A-Z]{_WORD}*)['’]s \1(?!{_WORD})"
)


def collapse_possessive(text: str) -> str:
    """`W's W` -> `W` for one capitalized word W (case-sensitive, whole words)."""
    return _POSSESSIVE.sub(r"\1", text)


class PossessiveDeduper:
    """Streaming form of collapse_possessive: the joined output of feed() and
    finish() equals collapse_possessive of the joined input, however chunked.

    Only a trailing fragment that could still become `W's W` is held back
    (e.g. "Microsoft", "Microsoft's", "Microsoft's Micro"); the rest is emitted
    at once. One emitted character is remembered so the word-boundary check
    before W still sees what precedes a held fragment.
    """

    def __init__(self) -> None:
        self._prev = ""
        self._buf = ""

    def feed(self, delta: str) -> str:
        self._buf += delta
        return self._emit(self._hold_from())

    def finish(self) -> str:
        return self._emit(len(self._buf))

    def _hold_from(self) -> int:
        text = self._prev + self._buf
        offset = len(self._prev)
        for i in range(offset, len(text)):
            if not ("A" <= text[i] <= "Z"):
                continue
            if i > 0 and re.fullmatch(_WORD, text[i - 1]):
                continue
            if self._could_match(text[i:]):
                return i - offset
        return len(self._buf)

    @staticmethod
    def _could_match(s: str) -> bool:
        """True when s (starting at a word start) is an unresolved prefix of `W's W`."""
        m = re.match(f"{_WORD}*", s)
        word = m.group()
        rest = s[len(word) :]
        if rest == "":
            return True
        if rest[0] not in "'’":
            return False
        if len(rest) == 1:
            return True
        if rest[1] != "s":
            return False
        if len(rest) == 2:
            return True
        if rest[2] != " ":
            return False
        tail = rest[3:]
        if len(tail) < len(word):
            return word.startswith(tail)
        return len(tail) == len(word) and tail == word

    def _emit(self, upto: int) -> str:
        head, self._buf = self._buf[:upto], self._buf[upto:]
        if not head:
            return ""
        out = collapse_possessive(self._prev + head)[len(self._prev) :]
        self._prev = head[-1]
        return out


_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_citations(raw: str) -> list[Citation] | None:
    """Parse the trailing JSON block. None means unparseable — retry, then degrade."""
    match = _BLOCK.search(raw)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    entries = payload.get("citations")
    if not isinstance(entries, list):
        return None
    citations = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        marker = entry.get("marker")
        chunk_id = entry.get("chunk_id")
        quote = entry.get("quote")
        if (
            isinstance(marker, int)
            and isinstance(chunk_id, int)
            and isinstance(quote, str)
        ):
            citations.append(Citation(marker=marker, chunk_id=chunk_id, quote=quote))
    return citations
