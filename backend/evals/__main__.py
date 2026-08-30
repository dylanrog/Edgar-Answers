from __future__ import annotations

import argparse

from pipeline import db
from pipeline.embed import Embedder
from pipeline.env import load_env

from . import harness


def cmd_run(args) -> None:
    questions = harness.load_golden()
    with db.connect() as conn:
        if args.debug:
            from api.retrieval import lexical_search, vector_search

            embedder = Embedder()
            for question in questions:
                vec = vector_search(conn, embedder.embed_query(question.question), k=5)
                lex = lexical_search(conn, question.question, k=5)
                print(f"\n{question.id}: {question.question}")
                print("  vector:", [(r[1], r[5], r[6]) for r in vec])
                print("  lexical:", [(r[1], r[5], r[6]) for r in lex])
            return
        embedder = Embedder()
        from api.detect import AnthropicCompanyDetector, AnthropicPeriodDetector

        company_detector = AnthropicCompanyDetector()
        period_detector = AnthropicPeriodDetector()
        metrics = harness.run_retrieval_eval(
            conn,
            embedder,
            questions,
            company_detector=company_detector,
            period_detector=period_detector,
        )
        if not args.retrieval_only:
            from api.generate import AnthropicGenerator

            from . import faithfulness

            metrics |= faithfulness.run_faithfulness_eval(
                conn, embedder, AnthropicGenerator(), company_detector, questions,
                period_detector=period_detector,
            )
    for key, value in metrics.items():
        print(f"{key}: {value}")
    harness.append_results(harness.RESULTS_PATH, metrics)
    print(f"appended to {harness.RESULTS_PATH}")


def cmd_verify(args) -> None:
    """Check every golden entry against the DB: accession exists, sids exist."""
    questions = harness.load_golden()
    failures = 0
    with db.connect() as conn, conn.cursor() as cur:
        for question in questions:
            cur.execute(
                "SELECT id FROM filings WHERE accession = %s", (question.accession,)
            )
            row = cur.fetchone()
            if row is None:
                print(f"FAIL {question.id}: accession {question.accession} not in DB")
                failures += 1
                continue
            filing_id = row[0]
            for sid in question.gold_sids:
                cur.execute(
                    "SELECT text FROM sentences WHERE filing_id = %s AND sid = %s",
                    (filing_id, sid),
                )
                sentence = cur.fetchone()
                if sentence is None:
                    print(f"FAIL {question.id}: sid {sid} not in {question.accession}")
                    failures += 1
                else:
                    print(f"ok  {question.id} sid {sid}: {sentence[0][:90]}")
    if failures:
        raise SystemExit(f"{failures} golden-set problems")
    print(f"all {len(questions)} entries verified")


def cmd_repin(args) -> None:
    from . import repin

    questions = harness.load_golden()
    with db.connect() as conn:
        if args.snapshot:
            repin.write_snapshot(repin.snapshot(conn, questions))
            print(f"snapshot written to {repin.SNAPSHOT_PATH}")
            return
        proposals = repin.propose(conn, questions, repin.read_snapshot())

    for proposal in proposals:
        flag = "ok " if proposal.resolved else "MANUAL"
        print(f"{flag} {proposal.question_id}: {proposal.old_sids} -> {proposal.new_sids}"
              f"  [{proposal.note}]")
    unresolved = [p.question_id for p in proposals if not p.resolved]

    if not args.apply:
        print("\nproposal only. Re-run with --apply to write golden.yaml.")
        return
    changed = repin.apply_to_golden(harness.GOLDEN_PATH, proposals)
    print(f"\nrewrote {changed} entries")
    if unresolved:
        print(f"LEFT UNCHANGED, fix by hand: {', '.join(unresolved)}")


def cmd_entities(args) -> None:
    from api import queries
    from api.detect import AnthropicCompanyDetector

    from . import entity_resolution

    cases = entity_resolution.load_cases()
    with db.connect() as conn:
        companies = queries.load_companies(conn)
    metrics = entity_resolution.run_entity_resolution_eval(
        AnthropicCompanyDetector(), companies, cases
    )
    for mismatch in metrics["mismatches"]:
        print(
            f"FAIL {mismatch['id']}: expected {mismatch['expected']},"
            f" got {mismatch['actual']}"
        )
    print(
        f"\n{metrics['correct']}/{metrics['cases']} correct"
        f" ({metrics['accuracy']:.2%})"
    )


def cmd_periods(args) -> None:
    from api import queries
    from api.detect import AnthropicPeriodDetector

    from . import fiscal_period_handling

    cases = fiscal_period_handling.load_cases()
    tickers = {case.ticker for case in cases}
    with db.connect() as conn:
        filings_by_ticker = {
            ticker: queries.load_filings_for_ticker(conn, ticker) for ticker in tickers
        }
    metrics = fiscal_period_handling.run_period_resolution_eval(
        AnthropicPeriodDetector(), filings_by_ticker, cases
    )
    for mismatch in metrics["mismatches"]:
        print(
            f"FAIL {mismatch['id']}: expected {mismatch['expected']},"
            f" got {mismatch['actual']}"
        )
    print(
        f"\n{metrics['correct']}/{metrics['cases']} correct"
        f" ({metrics['accuracy']:.2%})"
    )


def main(argv: list[str] | None = None) -> None:
    load_env()
    parser = argparse.ArgumentParser(prog="evals")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="run the eval harness against DATABASE_URL")
    p_run.add_argument("--retrieval-only", action="store_true")
    p_run.add_argument("--debug", action="store_true", help="print per-arm top results")
    sub.add_parser("verify", help="validate golden entries against the DB")
    p_repin = sub.add_parser(
        "repin", help="re-anchor golden gold_sids after a reprocess"
    )
    p_repin.add_argument(
        "--snapshot", action="store_true",
        help="capture current gold sentence text; run this BEFORE reprocess",
    )
    p_repin.add_argument(
        "--apply", action="store_true", help="write the proposals into golden.yaml"
    )
    sub.add_parser(
        "entities", help="score entity-resolution detection against hand-labeled cases"
    )
    sub.add_parser(
        "periods", help="score fiscal-period-resolution detection against hand-labeled cases"
    )
    args = parser.parse_args(argv)
    if args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "repin":
        cmd_repin(args)
    elif args.cmd == "entities":
        cmd_entities(args)
    elif args.cmd == "periods":
        cmd_periods(args)
    else:
        cmd_verify(args)


if __name__ == "__main__":
    main()
