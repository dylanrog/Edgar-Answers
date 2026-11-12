import os
from datetime import date
from pathlib import Path

import psycopg
import pytest
from tests.fakes import (
    FakeEmbedder,
    StubCompanyDetector,
    StubGenerator,
    StubQuoteRepairer,
)

from api.answer import answer_stream
from pipeline import db, store
from pipeline.canonicalize import canonicalize
from pipeline.chunk import chunk_sentences
from pipeline.companies import Company
from pipeline.edgar import FilingRef

FIXTURES = Path(__file__).parent / "fixtures"
COMPANY = Company(999999404, "TSTW", "Answer Cells Co")
REF = FilingRef(
    cik=999999404,
    accession="ANSWERCELLS-0001",
    form_type="10-K",
    filing_date=date(2025, 2, 26),
    period_end=date(2025, 1, 26),
    primary_document="x.html",
)


@pytest.fixture()
def seeded():
    conn = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    db.migrate(conn)
    canonical = canonicalize(
        (FIXTURES / "edgar_nvda_revenue.html").read_text(encoding="utf-8"), "10-K"
    )
    filing_id = store.store_filing(conn, COMPANY, REF, canonical, replace=True)
    chunks = chunk_sentences(canonical.sentences)
    store.store_chunks(
        conn, filing_id, chunks, FakeEmbedder().embed_texts([c.text for c in chunks])
    )
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM chunks WHERE filing_id = %s ORDER BY sid_start", (filing_id,))
        chunk_id = cur.fetchone()[0]
    data_center = next(s.sid for s in canonical.sentences if s.text.startswith("Data Center"))
    yield conn, chunk_id, data_center
    with conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE filing_id = %s", (filing_id,))
        cur.execute("DELETE FROM sentences WHERE filing_id = %s", (filing_id,))
        cur.execute("DELETE FROM filings WHERE id = %s", (filing_id,))
    conn.commit()
    conn.close()


@pytest.mark.db
def test_a_table_citation_carries_its_cited_cells(seeded):
    conn, chunk_id, data_center = seeded
    response = (
        "Data Center revenue was $115,186 million [1].\n\n"
        '```json\n{"citations": [{"marker": 1, "chunk_id": CHUNK, '
        '"quote": "Data Center $ 115,186"}]}\n```'
    ).replace("CHUNK", str(chunk_id))
    events = list(
        answer_stream(
            conn, FakeEmbedder(), StubGenerator(response), StubCompanyDetector(),
            "What was Data Center revenue?", tickers=["TSTW"],
        )
    )
    citation = next(e for e in events if e.name == "citation")
    assert citation.data["verified"] is True
    assert citation.data["cells"] == [{"sid": data_center, "cell": 2}]


def _ask(conn, chunk_id, prose, quote):
    response = (
        f"{prose}\n\n"
        '```json\n{"citations": [{"marker": 1, "chunk_id": CHUNK, '
        f'"quote": "{quote}"}}]}}\n```'
    ).replace("CHUNK", str(chunk_id))
    events = list(
        answer_stream(
            conn, FakeEmbedder(), StubGenerator(response), StubCompanyDetector(),
            "What was Data Center revenue?", tickers=["TSTW"],
        )
    )
    return next(e for e in events if e.name == "citation").data


WHOLE_ROW = "Data Center $ 115,186 $ 47,525 $ 15,005"


@pytest.mark.db
def test_a_whole_row_quote_marks_only_the_figure_the_answer_states(seeded):
    conn, chunk_id, data_center = seeded
    data = _ask(conn, chunk_id, "Data Center revenue was $115.2 billion [1].", WHOLE_ROW)
    assert data["verified"] is True
    assert data["sids"] == [data_center]
    assert data["cells"] == [{"sid": data_center, "cell": 2}]


@pytest.mark.db
def test_a_whole_row_quote_beside_a_claim_with_no_figure_marks_no_cells(seeded):
    conn, chunk_id, data_center = seeded
    data = _ask(conn, chunk_id, "Data Center revenue grew [1].", WHOLE_ROW)
    assert data["verified"] is True
    assert data["sids"] == [data_center]
    assert data["cells"] == []


@pytest.mark.db
def test_a_whole_row_quote_beside_an_unmatched_figure_keeps_every_cell(seeded):
    conn, chunk_id, data_center = seeded
    data = _ask(conn, chunk_id, "Data Center revenue grew 142% [1].", WHOLE_ROW)
    assert data["verified"] is True
    assert [c["cell"] for c in data["cells"]] == [2, 6, 10]


@pytest.mark.db
def test_a_marker_placed_before_its_claim_marks_the_figure_after_it(seeded):
    conn, chunk_id, data_center = seeded
    prose = "Here is the comparison: [1] Data Center revenue was $115,186 million."
    data = _ask(conn, chunk_id, prose, WHOLE_ROW)
    assert data["verified"] is True
    assert data["cells"] == [{"sid": data_center, "cell": 2}]


@pytest.mark.db
def test_citations_from_the_retry_are_not_narrowed(seeded):
    # Attempt 1's prose streams to the client but its citation block does not
    # parse; the retry's citations belong to prose nobody saw, so the cells
    # stay as verified.
    conn, chunk_id, data_center = seeded
    first = "Data Center revenue was $115.2 billion [1].\n\n```json\nnot json\n```"
    retry = (
        "Data Center revenue was $47.5 billion [1].\n\n"
        '```json\n{"citations": [{"marker": 1, "chunk_id": CHUNK, '
        f'"quote": "{WHOLE_ROW}"}}]}}\n```'
    ).replace("CHUNK", str(chunk_id))
    events = list(
        answer_stream(
            conn, FakeEmbedder(), StubGenerator(first, retry), StubCompanyDetector(),
            "What was Data Center revenue?", tickers=["TSTW"],
        )
    )
    data = next(e for e in events if e.name == "citation").data
    assert data["verified"] is True
    assert [c["cell"] for c in data["cells"]] == [2, 6, 10]


@pytest.mark.db
def test_a_repaired_table_quote_gets_its_cells_from_the_new_quote_then_narrowed(seeded):
    # The model elided text inside the row; the repair copies the whole row,
    # which verifies, resolves all three years, and is narrowed to the one
    # the answer states -- exactly as a first-pass whole-row quote would be.
    conn, chunk_id, data_center = seeded
    response = (
        "Data Center revenue was $115.2 billion [1].\n\n"
        '```json\n{"citations": [{"marker": 1, "chunk_id": CHUNK, '
        '"quote": "Data Center revenue $ 115,186"}]}\n```'
    ).replace("CHUNK", str(chunk_id))
    repairer = StubQuoteRepairer(WHOLE_ROW)
    events = list(
        answer_stream(
            conn, FakeEmbedder(), StubGenerator(response), StubCompanyDetector(),
            "What was Data Center revenue?", tickers=["TSTW"], quote_repairer=repairer,
        )
    )
    data = next(e for e in events if e.name == "citation").data
    assert len(repairer.calls) == 1
    assert data["verified"] is True
    assert data["repaired"] is True
    assert data["quote"] == WHOLE_ROW
    assert data["sids"] == [data_center]
    assert data["cells"] == [{"sid": data_center, "cell": 2}]
