"""One fusion rule for every search path.

Word search wins on exact names, titles and phrases; meaning search wins when
the wording differs. Their scores are not comparable, so averaging them is
meaningless. Reciprocal rank fusion combines positions instead: an item near
the top of either list scores well, and an item near the top of both scores
best. Every fused ranking in this codebase comes through here.
"""

from __future__ import annotations

#: Reciprocal rank fusion constant (the usual 60).
RRF_K = 60


def rrf[T](ranks: list[list[T]]) -> dict[T, float]:
    """Fuse ranked lists by position. Each list contributes 1/(K+rank)."""
    fused: dict[T, float] = {}
    for arm in ranks:
        for rank, key in enumerate(arm):
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
    return fused


def keyword_page_confidence(
    *,
    wanted: int,
    best_coverage: int,
    exact_hit: bool,
    phrase_hit: bool,
) -> str:
    """Confidence of a keyword-only result page.

    ``"low"`` means nothing matched every query term: no exact id or title
    hit, no quoted-phrase hit, and the best row covers fewer terms than the
    query has. The caller should try different terms; nothing is filtered.
    """
    if wanted <= 0:
        return "ok"
    if exact_hit or phrase_hit or best_coverage >= wanted:
        return "ok"
    return "low"
