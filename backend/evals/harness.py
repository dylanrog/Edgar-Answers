from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from api.retrieval import RetrievedChunk, retrieve
from api.targets import resolve_targets, retrieve_for_targets

GOLDEN_PATH = Path(__file__).parent / "golden.yaml"
RESULTS_PATH = Path(__file__).parent / "results.jsonl"
_REQUIRED = ("id", "question", "ticker", "accession", "section", "gold_sids")


_CATEGORIES = ("table_tail", "column")
_ALTERNATIVE_FIELDS = {"accession", "gold_sids", "expected_values", "gold_cells", "requires_cell"}


@dataclass(frozen=True)
class Alternative:
    """Examples only: another filing that also answers an entry correctly --
    a later filing reporting the same period's figure as restated. Judged by
    the same rules as the entry's own pin."""

    accession: str
    gold_sids: list[int]
    expected_values: tuple[tuple[str, ...], ...] = ()
    gold_cells: tuple[int, ...] = ()
    requires_cell: bool = False


@dataclass(frozen=True)
class GoldenQuestion:
    id: str
    question: str
    ticker: str
    accession: str
    section: str
    gold_sids: list[int]
    group: str = ""  # set by load_golden to entry.get("group", entry["id"])
    # Spec 2026-09-29 §7: which gap an entry measures ("" for the original set).
    category: str = ""
    # One inner tuple per required figure, holding its accepted spellings.
    expected_values: tuple[tuple[str, ...], ...] = ()
    # Examples only: the group this one follows in the same conversation.
    after: str = ""
    # Examples only: a verified citation on this accession must carry cells.
    requires_cell: bool = False
    # Examples only: the cell indexes (tr.cells[i]) on the gold sids that may
    # be cited -- the year-of-interest column of a multi-year row. Empty means
    # any cell on a gold sid.
    gold_cells: tuple[int, ...] = ()
    # Examples only: other pins that satisfy this entry instead of its own.
    alternatives: tuple[Alternative, ...] = ()

    def pins(self) -> list[Alternative]:
        """The entry's own pin first, then its alternatives."""
        own = Alternative(
            self.accession, self.gold_sids, self.expected_values,
            self.gold_cells, self.requires_cell,
        )
        return [own, *self.alternatives]


def _gold_sids(value, where: str) -> list[int]:
    if not isinstance(value, list) or not all(isinstance(s, int) for s in value):
        raise ValueError(f"golden entry {where}: gold_sids must be a list of ints")
    return value


def _expected_values(value, where: str) -> tuple[tuple[str, ...], ...]:
    # Quoted strings only: an unquoted 115,186 inside a YAML flow list
    # parses as two integers, which is exactly the mistake to catch here.
    if not isinstance(value, list) or not all(
        isinstance(spellings, list)
        and spellings
        and all(isinstance(s, str) and s for s in spellings)
        for spellings in value
    ):
        raise ValueError(
            f"golden entry {where}: expected_values must be a list of"
            " non-empty lists of quoted strings"
        )
    return tuple(tuple(spellings) for spellings in value)


def _gold_cells(value, where: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not all(
        isinstance(c, int) and not isinstance(c, bool) for c in value
    ):
        raise ValueError(f"golden entry {where}: gold_cells must be a list of ints")
    return tuple(value)


def _alternatives(value, where: str) -> tuple[Alternative, ...]:
    if not isinstance(value, list) or not all(isinstance(a, dict) for a in value):
        raise ValueError(f"golden entry {where}: alternatives must be a list of mappings")
    alternatives = []
    for n, alt in enumerate(value):
        label = f"{where} alternative {n}"
        unknown = set(alt) - _ALTERNATIVE_FIELDS
        if unknown:
            raise ValueError(f"golden entry {label}: unknown field(s) {sorted(unknown)}")
        for field in ("accession", "gold_sids"):
            if field not in alt:
                raise ValueError(f"golden entry {label}: missing field {field!r}")
        if not isinstance(alt["accession"], str) or not alt["accession"]:
            raise ValueError(f"golden entry {label}: accession must be a quoted string")
        requires_cell = alt.get("requires_cell", False)
        if not isinstance(requires_cell, bool):
            raise ValueError(f"golden entry {label}: requires_cell must be true or false")
        alternatives.append(
            Alternative(
                alt["accession"],
                _gold_sids(alt["gold_sids"], label),
                _expected_values(alt.get("expected_values", []), label),
                _gold_cells(alt.get("gold_cells", []), label),
                requires_cell,
            )
        )
    return tuple(alternatives)


def load_golden(path: Path = GOLDEN_PATH) -> list[GoldenQuestion]:
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    questions = []
    for entry in entries:
        entry_id = entry.get("id", "<missing id>")
        for field in _REQUIRED:
            if field not in entry:
                raise ValueError(f"golden entry {entry_id}: missing field {field!r}")
        _gold_sids(entry["gold_sids"], entry_id)
        group = entry.get("group", entry["id"])
        category = entry.get("category", "")
        if category and category not in _CATEGORIES:
            raise ValueError(f"golden entry {entry_id}: unknown category {category!r}")
        questions.append(
            GoldenQuestion(
                *(entry[f] for f in _REQUIRED),
                group,
                category,
                _expected_values(entry.get("expected_values", []), entry_id),
                entry.get("after", ""),
                bool(entry.get("requires_cell", False)),
                _gold_cells(entry.get("gold_cells", []), entry_id),
                _alternatives(entry.get("alternatives", []), entry_id),
            )
        )
    by_group: dict[str, str] = {}
    seen_groups: set[str] = set()
    for question in questions:
        if question.after and (
            question.after == question.group or question.after not in seen_groups
        ):
            raise ValueError(
                f"golden entry {question.id}: after {question.after!r} must name a"
                " group defined earlier in the file"
            )
        seen_groups.add(question.group)
        prior = by_group.setdefault(question.group, question.question)
        if prior != question.question:
            raise ValueError(
                f"golden entries in group {question.group!r} have mismatched question text"
            )
    return questions


def hit(chunks: list[RetrievedChunk], accession: str, gold_sids: list[int]) -> bool:
    return any(
        chunk.accession == accession
        and any(chunk.sid_start <= sid <= chunk.sid_end for sid in gold_sids)
        for chunk in chunks
    )


def _score(conn, embedder, questions, *, ks, k_each: int, scoped: bool) -> dict:
    """Recall over one retrieval arm; `scoped` applies each question's ticker."""
    top_k = max(ks)
    hits_at = {k: 0 for k in ks}
    misses_at_top: list[str] = []
    for question in questions:
        chunks = retrieve(
            conn,
            embedder,
            question.question,
            k_each=k_each,
            k_final=top_k,
            **({"ticker": question.ticker} if scoped else {}),
        )
        for k in ks:
            if hit(chunks[:k], question.accession, question.gold_sids):
                hits_at[k] += 1
        if not hit(chunks, question.accession, question.gold_sids):
            misses_at_top.append(question.id)
    n = len(questions)
    scores: dict = {f"recall@{k}": round(hits_at[k] / n, 4) if n else 0.0 for k in ks}
    scores[f"misses@{top_k}"] = misses_at_top
    return scores


def _score_targeted(
    conn, embedder, questions, *, ks, k_each: int = 20,
    company_detector, period_detector=None, query_rewriter=None,
) -> dict:
    """Recall over the real resolve_targets + retrieve_for_targets pipeline.

    Grouped by `group` so a comparison question's sibling rows share one
    resolution + retrieval call (matching what /ask actually does for one
    incoming question) rather than re-resolving per row.
    """
    top_k = max(ks)
    hits_at = {k: 0 for k in ks}
    misses_at_top: list[str] = []
    chunks_by_group: dict[str, list[RetrievedChunk]] = {}
    for question in questions:
        if question.group not in chunks_by_group:
            targets = resolve_targets(
                conn,
                question.question,
                explicit_tickers=None,
                company_detector=company_detector,
                period_detector=period_detector,
                query_rewriter=query_rewriter,
            )
            chunks_by_group[question.group] = retrieve_for_targets(
                conn, embedder, question.question, targets, k_final=top_k, k_each=k_each,
            )
        chunks = chunks_by_group[question.group]
        for k in ks:
            if hit(chunks[:k], question.accession, question.gold_sids):
                hits_at[k] += 1
        if not hit(chunks, question.accession, question.gold_sids):
            misses_at_top.append(question.id)
    n = len(questions)
    scores: dict = {
        f"targeted_recall@{k}": round(hits_at[k] / n, 4) if n else 0.0 for k in ks
    }
    scores[f"targeted_misses@{top_k}"] = misses_at_top
    return scores


def run_retrieval_eval(
    conn,
    embedder,
    questions,
    *,
    ks=(5, 10),
    k_each: int = 20,
    company_detector=None,
    period_detector=None,
    query_rewriter=None,
) -> dict:
    """Score retrieval on up to three arms: scoped, unfiltered, and targeted.

    The scoped arm measures the retriever itself, and is what the unprefixed
    keys have always meant -- on the single-company corpus these numbers were
    first taken against, the two arms were identical by construction. The
    unfiltered arm measures what a user gets when they leave the ticker filter
    empty on /ask, where every other filer's boilerplate competes for the same
    ten slots. The targeted arm (only computed when `company_detector` is
    given, since it makes a live LLM call) measures the real query-decomposition
    pipeline: does resolve_targets + retrieve_for_targets actually fix what
    unfiltered gets wrong. `query_rewriter` is optional and only changes
    what happens inside that targeted arm.
    """
    metrics: dict = {"questions": len(questions), "k_each": k_each}
    metrics |= _score(conn, embedder, questions, ks=ks, k_each=k_each, scoped=True)
    # Spec §7.2: rows past the vector arm's reach today. Scoped, like recall@k.
    tail = [q for q in questions if q.category == "table_tail"]
    if tail:
        tail_scores = _score(conn, embedder, tail, ks=(10,), k_each=k_each, scoped=True)
        metrics["table_tail_recall@10"] = tail_scores["recall@10"]
        metrics["table_tail_misses@10"] = tail_scores["misses@10"]
    unfiltered = _score(conn, embedder, questions, ks=ks, k_each=k_each, scoped=False)
    metrics |= {f"unfiltered_{key}": value for key, value in unfiltered.items()}
    if company_detector is not None:
        metrics |= _score_targeted(
            conn,
            embedder,
            questions,
            ks=ks,
            k_each=k_each,
            company_detector=company_detector,
            period_detector=period_detector,
            query_rewriter=query_rewriter,
        )
    return metrics


def _git(*args: str) -> str:
    # check=False is the intent: a failed git call (no repo, detached state)
    # degrades to "" below rather than aborting an eval run.
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def append_results(path: Path, metrics: dict) -> None:
    # git_dirty matters as much as git_sha: an eval run against a working tree
    # with uncommitted changes is not reproducible from its recorded sha, and
    # a results log that can't be replayed is worse than no log. Recording the
    # flag makes that visible in the file instead of inferrable from commit
    # timestamps.
    record = {
        "git_sha": _git("rev-parse", "--short", "HEAD") or "unknown",
        "git_dirty": bool(_git("status", "--porcelain")),
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        **metrics,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
