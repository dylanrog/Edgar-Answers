from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from pipeline import store
from pipeline.canonicalize import Sentence

from .harness import GoldenQuestion

SNAPSHOT_PATH = Path(__file__).parent / "repin_snapshot.json"


@dataclass(frozen=True)
class Proposal:
    question_id: str
    old_sids: list[int]
    new_sids: list[int]
    resolved: bool
    note: str


def snapshot(conn, questions: list[GoldenQuestion]) -> dict:
    """Capture each gold sid's *text* before reprocess destroys it.

    Sids are the thing being invalidated, so text is the only stable handle
    for re-anchoring afterwards.
    """
    snap: dict = {}
    for question in questions:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM filings WHERE accession = %s", (question.accession,)
            )
            row = cur.fetchone()
        if row is None:
            continue
        by_sid = {s.sid: s.text for s in store.load_sentences(conn, row[0])}
        snap[question.id] = {
            "accession": question.accession,
            "sids": list(question.gold_sids),
            "texts": [by_sid[sid] for sid in question.gold_sids if sid in by_sid],
        }
    return snap


def propose_from_sentences(
    questions: list[GoldenQuestion],
    snap: dict,
    sentences_by_accession: dict[str, list[Sentence]],
) -> list[Proposal]:
    """Map old gold sids to new ones by sentence text.

    Two cases matter. An unchanged prose sentence matches exactly. A gold sid
    that pointed at a flattened table now corresponds to several rows, so the
    old text *contains* each new row's text -- that is the containment branch.
    Anything else is reported unresolved rather than guessed: a wrong pin
    silently invalidates every measurement taken afterwards.
    """
    proposals: list[Proposal] = []
    for question in questions:
        entry = snap.get(question.id)
        if entry is None:
            proposals.append(
                Proposal(question.id, list(question.gold_sids), [], False, "no snapshot entry")
            )
            continue
        sentences = sentences_by_accession.get(entry["accession"], [])
        new_sids: list[int] = []
        notes: list[str] = []
        for text in entry["texts"]:
            exact = [s.sid for s in sentences if s.text == text]
            if exact:
                new_sids.extend(exact)
                notes.append("exact")
                continue
            contained = [s for s in sentences if s.text and s.text in text]
            if contained:
                # A short, generic row ('Products', 'Services') recurs across
                # many unrelated tables in a real filing, so naive containment
                # against every sentence in the accession pulls in rows from
                # tables that have nothing to do with this gold entry. The old
                # text came from exactly one table, so the correct group is
                # whichever table_id contributes the most matching rows here.
                by_table: dict[int | None, list[int]] = {}
                for s in contained:
                    by_table.setdefault(s.table_id, []).append(s.sid)
                table_groups = {tid: sids for tid, sids in by_table.items() if tid is not None}
                if table_groups:
                    best_tid = max(table_groups, key=lambda tid: len(table_groups[tid]))
                    rows = sorted(table_groups[best_tid])
                    notes.append(f"contained ({len(rows)} rows from table {best_tid})")
                else:
                    rows = [s.sid for s in contained]
                    notes.append(f"contained ({len(rows)} rows)")
                new_sids.extend(rows)
                continue
            notes.append("no match")
        resolved = bool(new_sids) and "no match" not in notes
        proposals.append(
            Proposal(
                question.id,
                list(question.gold_sids),
                sorted(set(new_sids)),
                resolved,
                "; ".join(notes),
            )
        )
    return proposals


def propose(conn, questions: list[GoldenQuestion], snap: dict) -> list[Proposal]:
    accessions = {entry["accession"] for entry in snap.values()}
    sentences_by_accession: dict[str, list[Sentence]] = {}
    for accession in accessions:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM filings WHERE accession = %s", (accession,))
            row = cur.fetchone()
        if row is not None:
            sentences_by_accession[accession] = store.load_sentences(conn, row[0])
    return propose_from_sentences(questions, snap, sentences_by_accession)


def apply_to_golden(path: Path, proposals: list[Proposal]) -> int:
    """Rewrite gold_sids for resolved proposals only. Returns how many changed.

    Round-trips the whole file through yaml.safe_dump, which preserves every
    entry's fields (including `group`) but drops all comments -- including
    any rationale notes written directly into golden.yaml. If this is ever
    run, re-add any deleted comments by hand afterward.
    """
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    by_id = {p.question_id: p for p in proposals if p.resolved}
    changed = 0
    for entry in entries:
        proposal = by_id.get(entry.get("id"))
        if proposal is None or entry.get("gold_sids") == proposal.new_sids:
            continue
        entry["gold_sids"] = proposal.new_sids
        changed += 1
    Path(path).write_text(
        yaml.safe_dump(
            entries, sort_keys=False, allow_unicode=True, default_flow_style=None
        ),
        encoding="utf-8",
    )
    return changed


def write_snapshot(snap: dict, path: Path = SNAPSHOT_PATH) -> None:
    Path(path).write_text(json.dumps(snap, indent=2), encoding="utf-8")


def read_snapshot(path: Path = SNAPSHOT_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
