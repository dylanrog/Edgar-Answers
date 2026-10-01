from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import store
from .canonicalize import canonicalize
from .chunk import chunk_sentences, embed_input
from .companies import Company


@dataclass
class IngestStats:
    ingested: int = 0
    skipped: int = 0


def ingest_company(
    company: Company,
    *,
    edgar,
    conn,
    force: bool = False,
    accession: str | None = None,
) -> IngestStats:
    stats = IngestStats()
    for ref in edgar.list_filings(company.cik):
        if accession is not None and ref.accession != accession:
            continue
        if not force and store.filing_exists(conn, ref.accession):
            stats.skipped += 1
            continue
        path = edgar.download_filing(ref, force=force)
        canonical = canonicalize(path.read_text(encoding="utf-8"), ref.form_type)
        store.store_filing(conn, company, ref, canonical, replace=force)
        stats.ingested += 1
    return stats


def embed_filings(
    conn,
    embedder,
    *,
    ticker: str | None = None,
) -> tuple[int, int]:
    """Chunk + embed every filing that has no chunks yet (design §4.3-4.4)."""
    filings_done = 0
    chunks_stored = 0
    for filing_id in store.filing_ids_without_chunks(conn, ticker=ticker):
        sentences = store.load_sentences(conn, filing_id)
        chunks = chunk_sentences(
            sentences,
            tables=store.load_tables(conn, filing_id),
            cells=store.load_cells(conn, filing_id),
        )
        vectors = embedder.embed_texts([embed_input(c) for c in chunks])
        chunks_stored += store.store_chunks(conn, filing_id, chunks, vectors)
        filings_done += 1
    return filings_done, chunks_stored


def rechunk_filings(conn, embedder, *, ticker: str | None = None) -> tuple[int, int]:
    """Rebuild every filing's chunks from stored sentences and cells, re-embedding.

    The explicit rebuild for a chunking change (spec 2026-09-29 §9): embed
    only touches filings with no chunks. Sentences are read, never written, so
    sids and the golden set's pins are untouched. Embedding happens before each
    filing's write, but the connection is not autocommit: the caller owns the
    transaction, `conn.transaction()` here is only a savepoint, and the CLI
    commits once at the end. Locks taken by earlier filings are therefore held
    until then. An interrupted run rolls back and is safe to re-run.
    """
    filings_done = 0
    chunks_stored = 0
    for filing_id, _cik, _accession, _form in store.filings_to_recanonicalize(
        conn, ticker=ticker
    ):
        chunks = chunk_sentences(
            store.load_sentences(conn, filing_id),
            tables=store.load_tables(conn, filing_id),
            cells=store.load_cells(conn, filing_id),
        )
        vectors = embedder.embed_texts([embed_input(c) for c in chunks])
        with conn.transaction():
            store.delete_chunks(conn, filing_id)
            chunks_stored += store.store_chunks(conn, filing_id, chunks, vectors)
        filings_done += 1
    return filings_done, chunks_stored


@dataclass
class RecanonicalizeStats:
    updated: int = 0
    missing: int = 0
    mismatched: list[str] = field(default_factory=list)


def recanonicalize_filings(
    conn,
    *,
    cache_dir: Path,
    ticker: str | None = None,
) -> RecanonicalizeStats:
    """Rebuild viewer_html from cached raw HTML. Writes nothing else.

    Sentence extraction reads block text, which an inline style attribute
    cannot affect, so sids are invariant across this change -- meaning no
    re-chunk and no re-embed. That invariance is *verified per filing* rather
    than assumed: if the freshly computed sentences disagree with the stored
    rows in any field, the filing is left untouched and reported. A silent
    rewrite here would break every stored citation and the golden set's
    pinned sids at once.
    """
    stats = RecanonicalizeStats()
    for filing_id, cik, accession, form_type in store.filings_to_recanonicalize(
        conn, ticker=ticker
    ):
        path = Path(cache_dir) / str(cik) / f"{accession}.html"
        if not path.exists():
            stats.missing += 1
            continue
        canonical = canonicalize(path.read_text(encoding="utf-8"), form_type)
        if canonical.sentences != store.load_sentences(conn, filing_id):
            stats.mismatched.append(accession)
            continue
        store.update_viewer_html(conn, filing_id, canonical.viewer_html)
        stats.updated += 1
    return stats


@dataclass
class RetableStats:
    updated: int = 0
    missing: int = 0
    mismatched: list[str] = field(default_factory=list)


def retable_filings(
    conn,
    *,
    cache_dir: Path,
    ticker: str | None = None,
) -> RetableStats:
    """Write filing_tables and table_cells from cached raw HTML. Writes nothing else.

    Column binding reads tables without touching the DOM, so sentences are
    invariant across it (spec 2026-09-29 §4.1). As with recanonicalize, that is
    verified per filing rather than assumed: a filing whose freshly computed
    sentences differ from the stored rows is left untouched and reported,
    because cells keyed to moved sids would point at the wrong rows.

    Transaction: the caller owns it (the connection is not autocommit;
    `conn.transaction()` per filing is a savepoint) and the CLI commits once at
    the end. An interrupted run rolls back and is safe to re-run.
    """
    stats = RetableStats()
    for filing_id, cik, accession, form_type in store.filings_to_recanonicalize(
        conn, ticker=ticker
    ):
        path = Path(cache_dir) / str(cik) / f"{accession}.html"
        if not path.exists():
            stats.missing += 1
            continue
        canonical = canonicalize(path.read_text(encoding="utf-8"), form_type)
        if canonical.sentences != store.load_sentences(conn, filing_id):
            stats.mismatched.append(accession)
            continue
        with conn.transaction():
            store.replace_tables(conn, filing_id, canonical.tables, canonical.cells)
        stats.updated += 1
    return stats


@dataclass
class ReprocessStats:
    reprocessed: int = 0
    missing: int = 0
    # (accession, sentences before, sentences after)
    moved: list[tuple[str, int, int]] = field(default_factory=list)


def reprocess_filings(
    conn,
    embedder,
    *,
    cache_dir: Path,
    ticker: str | None = None,
    dry_run: bool = False,
) -> ReprocessStats:
    """Rebuild sentences, chunks and embeddings from cached raw HTML.

    The deliberate opposite of recanonicalize_filings, which refuses to write
    when sentences move. This one expects them to move -- it is how a change
    to extraction reaches an already-ingested corpus. Every stored citation
    and every pinned gold sid is invalidated by design, so run
    `python -m evals repin` afterwards.

    One transaction per filing: a crash between deleting the old sentences and
    writing the new chunks would leave chunks pointing at sid ranges that no
    longer exist.
    """
    stats = ReprocessStats()
    for filing_id, cik, accession, form_type in store.filings_to_recanonicalize(
        conn, ticker=ticker
    ):
        path = Path(cache_dir) / str(cik) / f"{accession}.html"
        if not path.exists():
            stats.missing += 1
            continue
        canonical = canonicalize(path.read_text(encoding="utf-8"), form_type)
        before = len(store.load_sentences(conn, filing_id))
        after = len(canonical.sentences)
        if before != after:
            stats.moved.append((accession, before, after))
        if dry_run:
            continue
        with conn.transaction():
            store.delete_derived(conn, filing_id)
            store.replace_sentences(conn, filing_id, canonical.sentences)
            store.replace_tables(conn, filing_id, canonical.tables, canonical.cells)
            store.update_viewer_html(conn, filing_id, canonical.viewer_html)
            chunks = chunk_sentences(
                canonical.sentences,
                tables={t.table_id: t for t in canonical.tables},
                cells=canonical.cells,
            )
            vectors = embedder.embed_texts([embed_input(c) for c in chunks])
            store.store_chunks(conn, filing_id, chunks, vectors)
        stats.reprocessed += 1
    return stats
