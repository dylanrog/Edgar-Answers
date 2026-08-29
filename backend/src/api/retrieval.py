from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import psycopg

from pipeline.store import to_pgvector

RRF_K = 60

# The lexical arm ORs the question's terms instead of ANDing them. Postgres'
# tsquery builders AND every stemmed term, so a natural question ("What were
# Apple's total net sales in fiscal 2024?" -- 6+ significant terms) matches a
# chunk only if *every* term is present, and the arm silently returns nothing
# on realistic queries. Rewriting '&' to '|' gives "any term matches, ranked
# by how many and how well" via ts_rank_cd, which is what design §6.1 wants
# here: catch exact financial jargon even when the rest of the question's
# phrasing isn't in the chunk verbatim.
#
# plainto_tsquery, not websearch_to_tsquery: the rewrite is a string replace
# on the tsquery's text form, so it is only total if '&' is the sole operator
# that can appear. websearch_to_tsquery also emits '!' for a "-term" and '<->'
# for a "quoted phrase"; an OR'd negation ('zebra' | !'import') matches nearly
# every chunk in the corpus. Phase 3 feeds user-typed questions straight in,
# so that input is reachable. plainto_tsquery treats punctuation as noise and
# only ever emits '&'.
#
# NULLIF keeps an all-stopword question from reaching to_tsquery(''), which
# would raise a notice; to_tsquery(NULL) is NULL and `tsvector @@ NULL`
# selects no rows, which is the correct answer for a question with no lexemes.
_OR_TSQUERY = (
    "to_tsquery('english',"
    " NULLIF(replace(plainto_tsquery('english', %s)::text, ' & ', ' | '), ''))"
)

# Must stay spelled exactly like the chunks_text_fts expression index
# (migration 001) or the lexical arm falls back to a sequential scan.
_TSVECTOR = "to_tsvector('english', ch.text)"

_BASE = (
    "SELECT ch.id, f.accession, f.form_type, f.filing_date, c.ticker, ch.section,"
    " ch.sid_start, ch.sid_end, ch.text, ch.filing_id"
    " FROM chunks ch"
    " JOIN filings f ON f.id = ch.filing_id"
    " JOIN companies c ON c.cik = f.cik"
)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    accession: str
    form_type: str
    filing_date: date
    ticker: str
    section: str
    sid_start: int
    sid_end: int
    text: str
    filing_id: int
    score: float


def _filters(
    ticker: str | None, form_type: str | None, accessions: list[str] | None = None
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if ticker:
        clauses.append("c.ticker = %s")
        params.append(ticker.upper())
    if form_type:
        clauses.append("f.form_type = %s")
        params.append(form_type)
    if accessions:
        clauses.append("f.accession = ANY(%s)")
        params.append(accessions)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def vector_search(
    conn: psycopg.Connection,
    query_vector: list[float],
    *,
    k: int = 20,
    ticker: str | None = None,
    form_type: str | None = None,
    accessions: list[str] | None = None,
) -> list[tuple]:
    where, params = _filters(ticker, form_type, accessions)
    sql = _BASE + where + " ORDER BY ch.embedding <=> %s::vector LIMIT %s"
    with conn.cursor() as cur:
        cur.execute(sql, [*params, to_pgvector(query_vector), k])
        return cur.fetchall()


def lexical_search(
    conn: psycopg.Connection,
    question: str,
    *,
    k: int = 20,
    ticker: str | None = None,
    form_type: str | None = None,
    accessions: list[str] | None = None,
) -> list[tuple]:
    where, params = _filters(ticker, form_type, accessions)
    match = f"{_TSVECTOR} @@ {_OR_TSQUERY}"
    where = where + (" AND " if where else " WHERE ") + match
    sql = (
        _BASE
        + where
        + f" ORDER BY ts_rank_cd({_TSVECTOR}, {_OR_TSQUERY}) DESC LIMIT %s"
    )
    with conn.cursor() as cur:
        cur.execute(sql, [*params, question, question, k])
        return cur.fetchall()


def retrieve(
    conn: psycopg.Connection,
    embedder,
    question: str,
    *,
    k_each: int = 20,
    k_final: int = 8,
    ticker: str | None = None,
    form_type: str | None = None,
    accessions: list[str] | None = None,
) -> list[RetrievedChunk]:
    """Hybrid retrieval per design §6.1: vector + lexical arms fused with RRF."""
    query_vector = embedder.embed_query(question)
    vector_rows = vector_search(
        conn, query_vector, k=k_each, ticker=ticker, form_type=form_type,
        accessions=accessions,
    )
    lexical_rows = lexical_search(
        conn, question, k=k_each, ticker=ticker, form_type=form_type,
        accessions=accessions,
    )

    scores: dict[int, float] = {}
    rows_by_id: dict[int, tuple] = {}
    for rows in (vector_rows, lexical_rows):
        for rank, row in enumerate(rows):
            chunk_id = row[0]
            rows_by_id[chunk_id] = row
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k_final]
    return [RetrievedChunk(*rows_by_id[chunk_id], score) for chunk_id, score in ranked]
