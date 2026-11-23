"""Semantic search over stored messages (needs embeddings).

Two read paths, both over ``messages.embedding`` (filled in the background by
``embeddings/refresh.py``):

* :func:`semantic_messages`: nearest messages to a query, for the optional
  ``semantic`` layer of ``context_bootstrap``.
* :func:`deep_search`: hybrid search for ``memory_deep_search``. Vector and
  full-text results are merged by reciprocal rank fusion, then grouped so one
  conversation contributes its best few messages, not a wall of them.

Both make exactly one embedding call for the query. When embeddings are off,
the call fails or times out, they return nothing (bootstrap) or full-text
results only (deep search), and say so.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from subzero.db.pool import Database, vector_literal
from subzero.embeddings.service import EmbeddingService
from subzero.logging_setup import get_logger

logger = get_logger(__name__)

#: The read path waits at most this long for the query's embedding.
QUERY_EMBED_TIMEOUT_S = 2.0
#: Cosine similarity below which a "nearest" message is noise, for
#: text-embedding-3-small at 1536 dimensions.
MIN_SIMILARITY = 0.30
#: Reciprocal rank fusion constant (the usual 60).
_RRF_K = 60

_SEMANTIC_SQL = """
SELECT m.id, m.conversation_id, m.role, m.content, m.created_at, m.provider,
       m.provenance::text AS provenance, c.title, c.project_id, p.slug AS project_slug,
       1 - (m.embedding <=> $1::text::vector) AS similarity
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
LEFT JOIN projects p ON p.id = c.project_id
WHERE m.embedding IS NOT NULL
  AND ($3::text IS NULL OR m.provider = $3)
ORDER BY m.embedding <=> $1::text::vector
LIMIT $2
"""

_TEXT_SQL = """
SELECT m.id, m.conversation_id, m.role, m.content, m.created_at, m.provider,
       m.provenance::text AS provenance, c.title, c.project_id, p.slug AS project_slug,
       ts_rank_cd(m.search_tsv, q) AS text_rank
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
LEFT JOIN projects p ON p.id = c.project_id,
     websearch_to_tsquery('english', $1) q
WHERE m.search_tsv @@ q
  AND ($3::text IS NULL OR m.provider = $3)
ORDER BY text_rank DESC
LIMIT $2
"""


@dataclass
class MessageHit:
    id: UUID
    conversation_id: UUID
    title: str | None
    role: str
    content: str
    provider: str
    created_at: datetime
    project: str | None
    provenance: str
    similarity: float | None = None
    text_rank: float | None = None
    score: float = 0.0

    @classmethod
    def from_row(cls, row: Any) -> MessageHit:
        return cls(
            id=row["id"],
            conversation_id=row["conversation_id"],
            title=row["title"],
            role=row["role"],
            content=row["content"],
            provider=row["provider"],
            created_at=row["created_at"],
            project=row["project_slug"],
            provenance=row["provenance"],
        )


@dataclass
class DeepSearchResult:
    query: str
    semantic: bool
    note: str | None
    conversations: list[dict[str, Any]] = field(default_factory=list)


async def embed_query(embeddings: EmbeddingService, query: str) -> list[float] | None:
    """The query's vector, or None (embeddings off, failed or too slow)."""
    if not embeddings.enabled or not query.strip():
        return None
    try:
        return await asyncio.wait_for(embeddings.embed_one(query), QUERY_EMBED_TIMEOUT_S)
    except TimeoutError:
        logger.warning("query embedding timed out; semantic layer skipped")
        return None


async def semantic_messages(
    db: Database,
    vector: list[float],
    *,
    limit: int,
    provider: str | None = None,
    min_similarity: float = MIN_SIMILARITY,
) -> list[MessageHit]:
    rows = await db.fetch(_SEMANTIC_SQL, vector_literal(vector), limit, provider)
    hits = []
    for row in rows:
        if row["similarity"] < min_similarity:
            break  # ordered by distance: the rest are further away
        hit = MessageHit.from_row(row)
        hit.similarity = float(row["similarity"])
        hit.score = hit.similarity
        hits.append(hit)
    return hits


def _excerpt(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def deep_search(
    db: Database,
    embeddings: EmbeddingService,
    query: str,
    *,
    limit: int = 8,
    per_conversation: int = 3,
    provider: str | None = None,
    excerpt_chars: int = 600,
) -> DeepSearchResult:
    """Hybrid search over every stored message, grouped by conversation."""
    pool = max(30, limit * 6)
    vector = await embed_query(embeddings, query)
    text_rows = await db.fetch(_TEXT_SQL, query, pool, provider)
    vector_hits = (
        await semantic_messages(db, vector, limit=pool, provider=provider) if vector else []
    )

    fused: dict[UUID, MessageHit] = {}
    for rank, row in enumerate(text_rows):
        hit = fused.setdefault(row["id"], MessageHit.from_row(row))
        hit.text_rank = float(row["text_rank"])
        hit.score += 1.0 / (_RRF_K + rank + 1)
    for rank, vhit in enumerate(vector_hits):
        hit = fused.setdefault(vhit.id, vhit)
        hit.similarity = vhit.similarity
        hit.score += 1.0 / (_RRF_K + rank + 1)

    by_conversation: dict[UUID, list[MessageHit]] = {}
    for hit in sorted(fused.values(), key=lambda h: h.score, reverse=True):
        group = by_conversation.setdefault(hit.conversation_id, [])
        if len(group) < per_conversation:
            group.append(hit)

    conversations = []
    for conv_id, hits in list(by_conversation.items())[:limit]:
        first = hits[0]
        conversations.append(
            {
                "conversation_id": str(conv_id),
                "title": first.title,
                "provider": first.provider,
                "project": first.project,
                "score": round(sum(h.score for h in hits), 4),
                "messages": [
                    {
                        "id": str(h.id),
                        "role": h.role,
                        "at": h.created_at.replace(microsecond=0).isoformat(),
                        "similarity": round(h.similarity, 3) if h.similarity is not None else None,
                        "excerpt": _excerpt(h.content, excerpt_chars),
                    }
                    for h in sorted(hits, key=lambda h: h.created_at)
                ],
            }
        )

    note = None
    if vector is None:
        note = (
            "Full-text only: semantic search needs SUBZERO_EMBEDDING_PROVIDER and its API key."
            if not embeddings.enabled
            else "Full-text only: the query could not be embedded just now."
        )
    return DeepSearchResult(
        query=query, semantic=vector is not None, note=note, conversations=conversations
    )
