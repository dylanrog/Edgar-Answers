"""What a visitor is told when an answer can't be produced (design §10)."""

import anthropic
import httpx
import pytest

from api.errors import (
    BUSY,
    CREDITS_EXHAUSTED,
    DEFAULT_CREDITS_MESSAGE,
    FAILED,
    visitor_error,
)

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def api_error(cls, status: int, error_type: str, message: str):
    """A real SDK exception, built the way the SDK builds it from a response."""
    body = {"type": "error", "error": {"type": error_type, "message": message}}
    response = httpx.Response(status, request=REQUEST, json=body)
    return cls(f"Error code: {status} - {body}", response=response, body=body)


USAGE_LIMIT = api_error(
    anthropic.BadRequestError,
    400,
    "invalid_request_error",
    "You have reached your specified API usage limits. You will regain access on "
    "2026-12-01 at 00:00 UTC.",
)
LOW_CREDIT = api_error(
    anthropic.BadRequestError,
    400,
    "invalid_request_error",
    "Your credit balance is too low to access the Anthropic API. Please go to Plans & "
    "Billing to upgrade or purchase credits.",
)
BILLING = api_error(anthropic.APIStatusError, 402, "billing_error", "Billing problem.")


@pytest.mark.parametrize("exc", [USAGE_LIMIT, LOW_CREDIT, BILLING], ids=["limit", "credit", "402"])
def test_running_out_of_credits_gets_its_own_message(exc, monkeypatch):
    monkeypatch.delenv("CREDITS_EXHAUSTED_MESSAGE", raising=False)
    assert visitor_error(exc) == (CREDITS_EXHAUSTED, DEFAULT_CREDITS_MESSAGE)


def test_the_credits_message_can_be_set_without_a_code_change(monkeypatch):
    monkeypatch.setenv("CREDITS_EXHAUSTED_MESSAGE", "Back tomorrow!")
    assert visitor_error(USAGE_LIMIT) == (CREDITS_EXHAUSTED, "Back tomorrow!")


def test_a_blank_override_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("CREDITS_EXHAUSTED_MESSAGE", "   ")
    assert visitor_error(USAGE_LIMIT) == (CREDITS_EXHAUSTED, DEFAULT_CREDITS_MESSAGE)


@pytest.mark.parametrize(
    "exc",
    [
        api_error(anthropic.RateLimitError, 429, "rate_limit_error", "Rate limited."),
        api_error(anthropic.InternalServerError, 529, "overloaded_error", "Overloaded"),
        api_error(anthropic.InternalServerError, 500, "api_error", "Internal error"),
        anthropic.APIConnectionError(request=REQUEST),
        anthropic.APITimeoutError(request=REQUEST),
    ],
    ids=["429", "529", "500", "connection", "timeout"],
)
def test_a_temporary_outage_says_try_again_soon(exc):
    code, message = visitor_error(exc)
    assert code == BUSY
    assert "try again" in message.lower()


@pytest.mark.parametrize(
    "exc",
    [
        api_error(anthropic.BadRequestError, 400, "invalid_request_error", "max_tokens: too big"),
        api_error(anthropic.AuthenticationError, 401, "authentication_error", "invalid x-api-key"),
        RuntimeError("database is down"),
    ],
    ids=["bad-request", "auth", "non-api"],
)
def test_anything_else_is_a_plain_failure(exc):
    assert visitor_error(exc)[0] == FAILED


@pytest.mark.parametrize("exc", [USAGE_LIMIT, LOW_CREDIT, RuntimeError("secret detail at db:5432")])
def test_the_visitor_never_sees_the_raw_error(exc):
    _, message = visitor_error(exc)
    for leak in ("Error code", "invalid_request_error", "{", "db:5432"):
        assert leak not in message
