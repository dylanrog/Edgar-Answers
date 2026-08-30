import json
import os

import pytest
from fastapi.testclient import TestClient
from tests.fakes import FakeEmbedder, StubCompanyDetector, StubGenerator, StubPeriodDetector
from tests.test_answer import (  # noqa: F401  (seeded_conn is used as a fixture)
    ACCESSION,
    chunk_id_of,
    response_with,
    seeded_conn,
)

from api import app as app_module
from api.app import app


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.fixture()
def stubbed_client(seeded_conn, monkeypatch):  # noqa: F811
    quote = "Total net sales were 391.0 billion dollars"
    generator = StubGenerator(response_with(quote, chunk_id_of(seeded_conn)))
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    app.dependency_overrides[app_module.get_generator] = lambda: generator
    app.dependency_overrides[app_module.get_embedder] = FakeEmbedder
    # Overridden with zero-arg lambdas, not the bare classes: FastAPI
    # re-introspects a dependency override's own signature (it needs to solve
    # *its* sub-dependencies too), and StubCompanyDetector/StubPeriodDetector
    # both take an optional `answers` constructor param with no Depends()
    # marker. FastAPI would treat that as an unembedded body field competing
    # with AskRequest, and it swallows the whole JSON body to validate against
    # `dict[str, list[str]]` -- every /ask call 422s with "Input should be a
    # valid list" on the question/filters fields. Matches why `get_generator`
    # above is already a lambda rather than passing the class directly.
    # Suppressing PLW0108 ("unnecessary lambda") below: inlining these to the
    # bare class is exactly the bug described above, not a style improvement.
    app.dependency_overrides[app_module.get_company_detector] = (
        lambda: StubCompanyDetector()  # noqa: PLW0108
    )
    app.dependency_overrides[app_module.get_period_detector] = (
        lambda: StubPeriodDetector()  # noqa: PLW0108
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_healthz_reports_ok():
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ask_rejects_an_empty_question():
    with TestClient(app) as client:
        response = client.post("/ask", json={"question": "   "})
    assert response.status_code == 422


@pytest.mark.db
def test_ask_streams_sse_events_in_order(stubbed_client):
    response = stubbed_client.post("/ask", json={"question": "What were net sales?"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    names = [name for name, _ in parse_sse(response.text)]
    assert names[0] == "token"
    assert "citation" in names
    assert names[-1] == "done"


@pytest.mark.db
def test_ask_citation_event_matches_the_design_shape(stubbed_client):
    response = stubbed_client.post("/ask", json={"question": "What were net sales?"})
    citation = next(
        data for name, data in parse_sse(response.text) if name == "citation"
    )
    assert set(citation) == {
        "marker",
        "verified",
        "accession",
        "ticker",
        "form_type",
        "filing_date",
        "sids",
        "quote",
    }
    assert citation["verified"] is True
    assert citation["accession"] == ACCESSION
    assert citation["ticker"] == "TSTE"
    assert citation["form_type"] == "10-K"
    assert citation["filing_date"] == "2024-11-01"


@pytest.mark.db
def test_ask_passes_filters_through(stubbed_client):
    response = stubbed_client.post(
        "/ask",
        json={"question": "What were net sales?", "filters": {"ticker": "NOPE"}},
    )
    done = next(data for name, data in parse_sse(response.text) if name == "done")
    assert done["chunks_retrieved"] == 0


@pytest.mark.db
def test_ask_passes_the_new_tickers_filter_through(stubbed_client):
    response = stubbed_client.post(
        "/ask",
        json={"question": "What were net sales?", "filters": {"tickers": ["NOPE"]}},
    )
    done = next(data for name, data in parse_sse(response.text) if name == "done")
    assert done["chunks_retrieved"] == 0


@pytest.mark.db
def test_filings_endpoint_returns_viewer_html_and_metadata(stubbed_client):
    response = stubbed_client.get(f"/filings/{ACCESSION}")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "accession",
        "viewer_html",
        "ticker",
        "form_type",
        "filing_date",
        "period_end",
    }
    assert 'data-sid="0"' in body["viewer_html"]
    assert body["ticker"] == "TSTE"


@pytest.mark.db
def test_filings_endpoint_404s_for_an_unknown_accession(stubbed_client):
    assert stubbed_client.get("/filings/0000000000-00-000000").status_code == 404


@pytest.mark.db
def test_companies_endpoint_lists_companies_with_filings(stubbed_client):
    body = stubbed_client.get("/companies").json()
    assert {
        "cik": 999999005,
        "ticker": "TSTE",
        "name": "Test Co E",
        "filings": 1,
    } in body


def test_ask_preflight_allows_the_frontend_origin():
    """The browser sends OPTIONS before a JSON POST cross-origin. Without CORS
    the frontend cannot call /ask at all, and the failure surfaces only in a
    browser -- never in pytest -- so it is pinned here."""
    with TestClient(app) as client:
        response = client.options(
            "/ask",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
