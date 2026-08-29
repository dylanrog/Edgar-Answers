from __future__ import annotations

from dataclasses import dataclass

from .retrieval import RetrievedChunk, retrieve


@dataclass(frozen=True)
class Target:
    ticker: str
    accessions: list[str] | None  # None if fiscal period handling abstained or isn't wired in


def retrieve_for_targets(
    conn,
    embedder,
    question: str,
    targets: list[Target],
    *,
    k_final: int = 8,
    form_type: str | None = None,
) -> list[RetrievedChunk]:
    """One retrieve() call per target, concatenated -- never re-fused.

    Re-fusing all targets' results back into one shared ranking would
    reproduce the exact bug this module exists to fix: whichever company's
    chunks score higher would still crowd out the other. Concatenating
    guarantees every named target is represented, independent of how the
    arms compare to each other. 0 targets is exactly today's single
    unscoped retrieve() call -- zero regression risk when nothing was
    detected.
    """
    if not targets:
        return retrieve(conn, embedder, question, k_final=k_final, form_type=form_type)
    chunks: list[RetrievedChunk] = []
    for target in targets:
        chunks.extend(
            retrieve(
                conn,
                embedder,
                question,
                k_final=k_final,
                ticker=target.ticker,
                accessions=target.accessions,
                form_type=form_type,
            )
        )
    return chunks
