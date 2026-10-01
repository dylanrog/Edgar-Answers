import os
from datetime import date

import psycopg
import pytest
from tests.fakes import FakeEmbedder

from api.retrieval import lexical_search, retrieve
from pipeline import db, store
from pipeline.canonicalize import CanonicalFiling, Sentence
from pipeline.chunk import Chunk
from pipeline.companies import Company
from pipeline.edgar import FilingRef

ALPHA = Company(999999003, "TSTC", "Test Co C")
BETA = Company(999999004, "TSTD", "Test Co D")

# One chunk per filing; distinctive vocabularies so vectors are far apart.
ALPHA_TEXT = "The escrow covenant restricts zebra imports during fiscal 2024."
BETA_TEXT = "Cloud subscription revenue accelerated in the fourth quarter."
LEXICAL_ONLY_TEXT = "A zebra logistics subsidiary was divested for cash proceeds."


def seed_filing(conn, company, accession, text):
    sentence = Sentence(0, "item1", text, 0, len(text))
    canonical = CanonicalFiling(
        text, [sentence], f'<p><span data-sid="0">{text}</span></p>'
    )
    ref = FilingRef(
        cik=company.cik,
        accession=accession,
        form_type="10-K",
        filing_date=date(2024, 11, 1),
        period_end=None,
        primary_document="t.htm",
    )
    filing_id = store.store_filing(conn, company, ref, canonical)
    chunk = Chunk("item1", 0, 0, text, 10)
    store.store_chunks(conn, filing_id, [chunk], FakeEmbedder().embed_texts([text]))
    return filing_id


@pytest.fixture()
def seeded_conn():
    conn = psycopg.connect(os.environ["TEST_DATABASE_URL"])
    db.migrate(conn)
    with conn.cursor() as cur:
        for accession in ("TESTC-24-000001", "TESTD-24-000001", "TESTC-24-000002"):
            cur.execute(
                "DELETE FROM chunks WHERE filing_id IN"
                " (SELECT id FROM filings WHERE accession = %s)",
                (accession,),
            )
            cur.execute(
                "DELETE FROM sentences WHERE filing_id IN"
                " (SELECT id FROM filings WHERE accession = %s)",
                (accession,),
            )
            cur.execute("DELETE FROM filings WHERE accession = %s", (accession,))
    conn.commit()
    seed_filing(conn, ALPHA, "TESTC-24-000001", ALPHA_TEXT)
    seed_filing(conn, BETA, "TESTD-24-000001", BETA_TEXT)
    seed_filing(conn, ALPHA, "TESTC-24-000002", LEXICAL_ONLY_TEXT)
    yield conn
    conn.close()


@pytest.mark.db
def test_exact_text_question_ranks_its_chunk_first(seeded_conn):
    results = retrieve(seeded_conn, FakeEmbedder(), ALPHA_TEXT)
    assert results[0].accession == "TESTC-24-000001"
    assert results[0].text == ALPHA_TEXT
    assert results[0].score > 0


@pytest.mark.db
def test_lexical_match_surfaces_vector_far_chunk(seeded_conn):
    # "zebra" appears in the lexical-only chunk; its fake vector shares little
    # with the full ALPHA_TEXT query, so fusion must bring it in via FTS.
    results = retrieve(seeded_conn, FakeEmbedder(), ALPHA_TEXT, k_final=3)
    accessions = {r.accession for r in results}
    assert "TESTC-24-000002" in accessions


@pytest.mark.db
def test_lexical_search_matches_on_partial_term_overlap(seeded_conn):
    # "reporting" appears nowhere in the seeded corpus. A naive AND-tsquery
    # requires every stemmed term to be present and would match zero rows
    # here, even though most of the question's terms hit the ALPHA chunk
    # directly. Full natural-language questions must degrade to "most terms
    # matched, ranked by overlap" or the lexical arm silently starves on
    # realistic queries.
    question = "What does the escrow covenant restrict about zebra imports for reporting purposes?"
    results = lexical_search(seeded_conn, question, k=10)
    assert results
    assert results[0][1] == "TESTC-24-000001"


@pytest.mark.db
def test_lexical_search_treats_a_leading_dash_as_punctuation_not_negation(seeded_conn):
    # Phase 3 feeds user-typed text straight into this arm, and web-search
    # habits leak in. Under websearch_to_tsquery this parsed as 'zebra' |
    # !'import', and the OR'd negation matched every chunk lacking "imports"
    # -- BETA's cloud-revenue chunk scored as a lexical hit for a question
    # about zebras. Terms must be ORed as plain terms, never negated.
    results = lexical_search(seeded_conn, "zebra -imports", k=10)
    accessions = {row[1] for row in results}
    assert "TESTD-24-000001" not in accessions
    assert accessions == {"TESTC-24-000001", "TESTC-24-000002"}


@pytest.mark.db
def test_lexical_search_on_an_all_stopword_question_returns_nothing(seeded_conn):
    # No lexemes survive stemming, so there is nothing to match. The tsquery
    # must come out NULL rather than empty -- an empty tsquery raises a
    # Postgres notice on every call.
    assert lexical_search(seeded_conn, "the of and", k=10) == []


@pytest.mark.db
def test_ticker_filter_excludes_other_companies(seeded_conn):
    results = retrieve(seeded_conn, FakeEmbedder(), "revenue quarter", ticker="TSTD")
    assert results  # BETA's chunk matches
    assert {r.ticker for r in results} == {"TSTD"}


@pytest.mark.db
def test_k_final_caps_results(seeded_conn):
    results = retrieve(seeded_conn, FakeEmbedder(), "zebra revenue escrow", k_final=1)
    assert len(results) == 1


@pytest.mark.db
def test_accessions_filter_narrows_to_one_filing_within_a_ticker(seeded_conn):
    # Both ALPHA filings mention "zebra" (TESTC-24-000001's covenant text and
    # TESTC-24-000002's logistics text), so an unscoped or ticker-only query
    # would surface both. Pinning accessions must exclude the other one even
    # though it belongs to the same company.
    results = retrieve(
        seeded_conn, FakeEmbedder(), "zebra imports",
        ticker="TSTC", accessions=["TESTC-24-000001"],
    )
    accessions = {r.accession for r in results}
    assert accessions == {"TESTC-24-000001"}


@pytest.mark.db
def test_context_is_returned_but_not_lexically_indexed(seeded_conn):
    ctx_text = "Pieces of a table with no distinctive words."
    txt_text = "The okapi appendix lists every enclosure."
    ctx_acc, txt_acc = "TESTC-24-000003", "TESTC-24-000004"
    filing_ids = []
    for acc, text, context, table_id in (
        (ctx_acc, ctx_text, "Table: okapi appendix", 4),
        (txt_acc, txt_text, "", None),
    ):
        sentence = Sentence(0, "item7", text, 0, len(text))
        canonical = CanonicalFiling(text, [sentence], f'<p><span data-sid="0">{text}</span></p>')
        ref = FilingRef(
            cik=ALPHA.cik, accession=acc, form_type="10-K",
            filing_date=date(2024, 11, 1), period_end=None, primary_document="t.htm",
        )
        filing_id = store.store_filing(seeded_conn, ALPHA, ref, canonical)
        filing_ids.append(filing_id)
        chunk = Chunk("item7", 0, 0, text, 10, context=context, table_id=table_id)
        store.store_chunks(seeded_conn, filing_id, [chunk], FakeEmbedder().embed_texts([text]))
    seeded_conn.commit()
    try:
        lexical = {r[1] for r in lexical_search(seeded_conn, "okapi appendix", ticker=ALPHA.ticker)}
        assert txt_acc in lexical
        assert ctx_acc not in lexical
        # Context is still embedded/returned: the vector arm can surface the chunk
        # and the RetrievedChunk carries its context and table_id.
        results = retrieve(
            seeded_conn, FakeEmbedder(), "okapi appendix", k_final=8, ticker=ALPHA.ticker
        )
        hit = next(r for r in results if r.accession == ctx_acc)
        assert hit.context == "Table: okapi appendix"
        assert hit.table_id == 4
        assert hit.text == ctx_text
    finally:
        with seeded_conn.cursor() as cur:
            for filing_id in filing_ids:
                cur.execute("DELETE FROM chunks WHERE filing_id = %s", (filing_id,))
                cur.execute("DELETE FROM sentences WHERE filing_id = %s", (filing_id,))
                cur.execute("DELETE FROM filings WHERE id = %s", (filing_id,))
        seeded_conn.commit()
