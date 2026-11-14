import zlib

from pipeline.embed import DIMENSIONS


class FakeEmbedder:
    """Deterministic bag-of-words vectors: same text -> same vector, shared
    tokens -> nonzero cosine. embed_query applies no prefix, so a query equal
    to a stored chunk's text produces the identical vector (distance 0)."""

    def embed_texts(self, texts):
        return [self._vector(text) for text in texts]

    def embed_query(self, question):
        return self._vector(question)

    def _vector(self, text):
        vector = [0.0] * DIMENSIONS
        for token in text.lower().split():
            vector[zlib.crc32(token.encode()) % DIMENSIONS] += 1.0
        norm = sum(x * x for x in vector) ** 0.5 or 1.0
        return [x / norm for x in vector]


class StubGenerator:
    """Replays a canned response in small deltas -- exercises the streaming
    split without an API key or a cent of spend (design §9). Successive calls
    advance through `responses`, then repeat the last one."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def stream(self, system, user):
        self.calls += 1
        index = min(self.calls - 1, len(self.responses) - 1)
        text = self.responses[index]
        for start in range(0, len(text), 7):
            yield text[start : start + 7]


class StubCompanyDetector:
    """Returns a canned ticker list per exact question text, for testing
    consumers of CompanyDetector without hitting the live API."""

    def __init__(self, answers: dict[str, list[str]] | None = None):
        self.answers = answers or {}
        self.calls: list[str] = []

    def detect(self, question, companies):
        self.calls.append(question)
        return self.answers.get(question, [])


class StubPeriodDetector:
    """Returns a canned accession list per exact question text, for testing
    consumers of PeriodDetector without hitting the live API."""

    def __init__(self, answers: dict[str, list[str]] | None = None):
        self.answers = answers or {}
        self.calls: list[tuple[str, str, list[dict]]] = []

    def detect(self, question, ticker, filings):
        self.calls.append((question, ticker, filings))
        return self.answers.get(question, [])


class StubQueryRewriter:
    """Returns a canned rewritten query per (question, ticker), for testing
    consumers of QueryRewriter without hitting the live API."""

    def __init__(self, answers: dict[tuple[str, str], str] | None = None):
        self.answers = answers or {}
        self.calls: list[tuple[str, str, str]] = []

    def rewrite(self, question, ticker, company_name):
        self.calls.append((question, ticker, company_name))
        return self.answers.get((question, ticker), question)


class StubConversationRewriter:
    """Returns a canned standalone question per follow-up text, for testing
    consumers of ConversationRewriter without hitting the live API. Unknown
    follow-ups echo back unchanged, matching a no-op rewrite."""

    def __init__(self, answers: dict[str, str] | None = None):
        self.answers = answers or {}
        self.calls: list[tuple[str, tuple]] = []

    def resolve(self, question, history):
        self.calls.append((question, tuple(history)))
        return self.answers.get(question, question)


class StubQuoteRepairer:
    """Returns a canned repaired quote (or None, or raises), recording every
    (claim, quote, excerpt) it was asked -- for testing the citation-repair
    step without hitting the live API. `answer` may be a callable of the
    failed quote."""

    def __init__(self, answer=None, *, error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.calls: list[tuple[str, str, str]] = []
        self.figure_free: list[bool] = []  # the flag each call was made with

    def repair(self, claim, quote, excerpt, figure_free=False):
        self.calls.append((claim, quote, excerpt))
        self.figure_free.append(figure_free)
        if self.error is not None:
            raise self.error
        return self.answer(quote) if callable(self.answer) else self.answer
