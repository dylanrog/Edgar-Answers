import pytest
from fastapi.testclient import TestClient
from tests.fakes import (
    FakeEmbedder,
    StubCompanyDetector,
    StubConversationRewriter,
    StubGenerator,
    StubPeriodDetector,
    StubQueryRewriter,
)

from api import app as app_module
from api.app import app
from api.limits import AskLimiter, RateLimited

ORIGIN = "http://localhost:3000"


@pytest.fixture()
def client_with():
    """TestClient whose /ask uses the given limiter and stub LLM deps (no
    Anthropic key needed, so this runs in CI)."""

    def make(limiter: AskLimiter) -> TestClient:
        app.dependency_overrides[app_module.get_limiter] = lambda: limiter
        app.dependency_overrides[app_module.get_embedder] = FakeEmbedder
        # Zero-arg lambdas, not bare classes -- see the note in test_app.py.
        app.dependency_overrides[app_module.get_generator] = lambda: StubGenerator("")
        app.dependency_overrides[app_module.get_company_detector] = (
            lambda: StubCompanyDetector()  # noqa: PLW0108
        )
        app.dependency_overrides[app_module.get_period_detector] = (
            lambda: StubPeriodDetector()  # noqa: PLW0108
        )
        app.dependency_overrides[app_module.get_query_rewriter] = (
            lambda: StubQueryRewriter()  # noqa: PLW0108
        )
        app.dependency_overrides[app_module.get_conversation_rewriter] = (
            lambda: StubConversationRewriter()  # noqa: PLW0108
        )
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def test_ask_over_the_daily_cap_is_429_with_reason_and_cors(client_with):
    with client_with(AskLimiter(per_ip_per_hour=5, daily_cap=0)) as client:
        response = client.post(
            "/ask", json={"question": "What was revenue?"}, headers={"Origin": ORIGIN}
        )
    assert response.status_code == 429
    assert "today" in response.json()["detail"]
    assert int(response.headers["retry-after"]) > 0
    # Without this the browser hides the body and the UI can only say "failed".
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_invalid_questions_do_not_spend_quota(client_with):
    limiter = AskLimiter(per_ip_per_hour=1, daily_cap=100)
    with client_with(limiter) as client:
        assert client.post("/ask", json={"question": "   "}).status_code == 422
        assert client.post("/ask", json={"question": "x" * 1001}).status_code == 422
    # TestClient's peer host is "testclient"; its one slot is still free.
    limiter.check("testclient")
    with pytest.raises(RateLimited):
        limiter.check("testclient")
