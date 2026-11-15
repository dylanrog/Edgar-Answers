"""What a visitor is told when an answer can't be produced (design §10).

The raw exception goes to the server log only: an SDK error's text is a JSON
dump with request ids and account details, and a non-API error can carry
anything. The visitor gets one of three plain messages, with a `code` the
frontend can act on (an exhausted account makes "Try again" pointless).
"""

from __future__ import annotations

import os

import anthropic

CREDITS_EXHAUSTED = "credits_exhausted"
BUSY = "busy"
FAILED = "failed"

# Overridable with CREDITS_EXHAUSTED_MESSAGE, so the wording can change
# without a deploy of new code.
DEFAULT_CREDITS_MESSAGE = (
    "This demo has used up its AI budget for now, so it can't answer questions. "
    "Please check back later."
)
BUSY_MESSAGE = "The AI service is busy right now. Please try again in a minute."
FAILED_MESSAGE = "Something went wrong while answering that question. Please try again."

# An account's spend limit and an empty credit balance both come back as a
# plain 400 invalid_request_error; only the message tells them apart from a
# malformed request. A 402 billing_error needs no text match.
_CREDIT_PHRASES = ("usage limit", "credit balance")
_BUSY_TYPES = {"rate_limit_error", "overloaded_error", "api_error"}


def _api_message(exc: anthropic.APIStatusError) -> str:
    body = exc.body
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict) and isinstance(inner.get("message"), str):
            return inner["message"]
    return str(exc)


def visitor_error(exc: BaseException) -> tuple[str, str]:
    """(code, message) to show a visitor for an exception from the query path."""
    if isinstance(exc, anthropic.APIStatusError):
        error_type = getattr(exc, "type", None)
        if error_type == "billing_error" or exc.status_code == 402 or (
            isinstance(exc, anthropic.BadRequestError)
            and any(phrase in _api_message(exc).lower() for phrase in _CREDIT_PHRASES)
        ):
            message = os.environ.get("CREDITS_EXHAUSTED_MESSAGE", "").strip()
            return CREDITS_EXHAUSTED, message or DEFAULT_CREDITS_MESSAGE
        if error_type in _BUSY_TYPES or isinstance(
            exc, (anthropic.RateLimitError, anthropic.InternalServerError)
        ):
            return BUSY, BUSY_MESSAGE
        return FAILED, FAILED_MESSAGE
    if isinstance(exc, anthropic.APIConnectionError):  # includes APITimeoutError
        return BUSY, BUSY_MESSAGE
    return FAILED, FAILED_MESSAGE
