from api.generate import SYSTEM_PROMPT
from api.repair import (
    MAX_QUOTE_CHARS,
    REPAIR_SYSTEM_PROMPT,
    build_repair_prompt,
    parse_repaired_quote,
)


def test_a_plain_reply_is_the_quote():
    assert parse_repaired_quote("Data Center $ 115,186\n") == "Data Center $ 115,186"


def test_none_in_any_case_or_punctuation_means_no_quote():
    for raw in ("NONE", "none", "None.", "  NONE  \n"):
        assert parse_repaired_quote(raw) is None


def test_a_reply_opening_with_the_sentinel_means_no_quote():
    assert parse_repaired_quote("NONE - the excerpt gives 391.0, not 412.7") is None
    assert parse_repaired_quote("NONE: not supported") is None


def test_a_quote_that_happens_to_start_with_none_is_still_a_quote():
    assert parse_repaired_quote("None of our segments") == "None of our segments"


def test_a_blank_reply_means_no_quote():
    assert parse_repaired_quote("   ") is None


def test_wrapping_quotation_marks_are_dropped():
    assert parse_repaired_quote('"Data Center $ 115,186"') == "Data Center $ 115,186"
    assert parse_repaired_quote("“Data Center”") == "Data Center"


def test_a_code_fence_is_dropped():
    assert parse_repaired_quote("```\nData Center $ 115,186\n```") == "Data Center $ 115,186"


def test_a_reply_over_the_quote_cap_is_rejected():
    assert parse_repaired_quote("x" * MAX_QUOTE_CHARS) == "x" * MAX_QUOTE_CHARS
    assert parse_repaired_quote("x" * (MAX_QUOTE_CHARS + 1)) is None


def test_the_prompt_carries_the_claim_the_failed_quote_and_the_excerpt():
    prompt = build_repair_prompt("Revenue was $115.2 billion", "Data Center 115,186", "EXCERPT")
    assert "Revenue was $115.2 billion" in prompt
    assert "Data Center 115,186" in prompt
    assert prompt.endswith("Excerpt:\nEXCERPT")


def test_the_repair_prompt_is_strict_and_separate_from_the_answer_prompt():
    assert "character-for-character" in REPAIR_SYSTEM_PROMPT
    assert "NONE" in REPAIR_SYSTEM_PROMPT
    assert "different figure" in REPAIR_SYSTEM_PROMPT
    assert REPAIR_SYSTEM_PROMPT != SYSTEM_PROMPT


def test_the_anthropic_client_is_built_once_per_process(monkeypatch):
    import anthropic
    import httpx

    from api import repair

    built = []

    class FakeClient:
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    repair._client.cache_clear()
    try:
        first = repair.AnthropicQuoteRepairer(api_key="k")
        second = repair.AnthropicQuoteRepairer(api_key="k")  # e.g. the next request
        assert first._anthropic() is second._anthropic()
        assert len(built) == 1
        assert built[0]["max_retries"] == 0
        assert isinstance(built[0]["timeout"], httpx.Timeout)
        assert built[0]["timeout"].connect == repair.CONNECT_TIMEOUT_SECONDS
    finally:
        repair._client.cache_clear()


def test_a_repair_call_is_bounded_by_wall_clock(monkeypatch):
    import concurrent.futures
    import threading
    import time

    import pytest

    from api.repair import AnthropicQuoteRepairer

    release = threading.Event()
    repairer = AnthropicQuoteRepairer(api_key="k", timeout=0.05)
    monkeypatch.setattr(repairer, "_call", lambda *args: release.wait(5) and "late")
    started = time.monotonic()
    try:
        with pytest.raises(concurrent.futures.TimeoutError):
            repairer.repair("claim", "quote", "excerpt")
    finally:
        release.set()
    assert time.monotonic() - started < 1.0
