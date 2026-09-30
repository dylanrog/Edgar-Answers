from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import companies, db, ingest
from .edgar import EdgarClient
from .env import load_env


def main(argv: list[str] | None = None) -> None:
    load_env()
    parser = argparse.ArgumentParser(prog="pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply pending database migrations")
    p_ingest = sub.add_parser("ingest", help="fetch, canonicalize, and store filings")
    p_ingest.add_argument("--ticker", help="one curated ticker, e.g. AAPL")
    p_ingest.add_argument("--all", action="store_true", help="ingest every curated company")
    p_ingest.add_argument("--accession", help="restrict to a single accession number")
    p_ingest.add_argument("--force", action="store_true", help="re-download and re-store")
    p_embed = sub.add_parser("embed", help="chunk + embed filings that have no chunks yet")
    p_embed.add_argument("--ticker", help="restrict to one curated ticker")
    p_recanon = sub.add_parser(
        "recanonicalize",
        help="rebuild viewer_html from cached raw HTML (no re-embed, no EDGAR traffic)",
    )
    p_recanon.add_argument("--ticker", help="restrict to one curated ticker")
    p_retable = sub.add_parser(
        "retable",
        help="write table cells from cached raw HTML (no re-embed; refuses if sentences moved)",
    )
    p_retable.add_argument("--ticker", help="restrict to one curated ticker")
    p_reprocess = sub.add_parser(
        "reprocess",
        help="rebuild sentences, chunks and embeddings from cached raw HTML"
        " (no EDGAR traffic; invalidates stored sids -- run `evals repin` after)",
    )
    p_reprocess.add_argument("--ticker", help="restrict to one curated ticker")
    p_reprocess.add_argument(
        "--dry-run", action="store_true", help="report sid movement, write nothing"
    )
    args = parser.parse_args(argv)

    if args.cmd == "migrate":
        with db.connect() as conn:
            applied = db.migrate(conn)
        print(f"applied: {applied if applied else 'nothing to do'}")
        return

    if args.cmd == "embed":
        from .embed import Embedder

        with db.connect() as conn:
            filings_done, chunks_stored = ingest.embed_filings(
                conn, Embedder(), ticker=args.ticker
            )
        print(f"embedded {chunks_stored} chunks across {filings_done} filings")
        return

    if args.cmd == "recanonicalize":
        with db.connect() as conn:
            stats = ingest.recanonicalize_filings(
                conn, cache_dir=Path("data/raw"), ticker=args.ticker
            )
        print(f"updated {stats.updated} filings, {stats.missing} missing from cache")
        if stats.mismatched:
            print(f"SENTENCE MISMATCH, left untouched: {', '.join(stats.mismatched)}")
        return

    if args.cmd == "retable":
        with db.connect() as conn:
            stats = ingest.retable_filings(conn, cache_dir=Path("data/raw"), ticker=args.ticker)
        print(f"updated {stats.updated} filings, {stats.missing} missing from cache")
        if stats.mismatched:
            print(f"SENTENCE MISMATCH, left untouched: {', '.join(stats.mismatched)}")
        return

    if args.cmd == "reprocess":
        from .embed import Embedder

        with db.connect() as conn:
            stats = ingest.reprocess_filings(
                conn,
                Embedder(),
                cache_dir=Path("data/raw"),
                ticker=args.ticker,
                dry_run=args.dry_run,
            )
        verb = "would reprocess" if args.dry_run else "reprocessed"
        print(f"{verb} {stats.reprocessed} filings, {stats.missing} missing from cache")
        for accession, before, after in stats.moved:
            print(f"  {accession}: {before} -> {after} sentences")
        return

    if not args.all and not args.ticker:
        sys.exit("ingest requires --ticker or --all")
    user_agent = os.environ.get("EDGAR_USER_AGENT")
    if not user_agent:
        sys.exit("EDGAR_USER_AGENT is not set (SEC requires an identifying User-Agent)")
    edgar = EdgarClient(user_agent=user_agent, cache_dir=Path("data/raw"))
    targets = companies.CURATED if args.all else [companies.by_ticker(args.ticker)]
    with db.connect() as conn:
        for company in targets:
            stats = ingest.ingest_company(
                company, edgar=edgar, conn=conn, force=args.force, accession=args.accession
            )
            print(f"{company.ticker}: ingested={stats.ingested} skipped={stats.skipped}")


if __name__ == "__main__":
    main()
