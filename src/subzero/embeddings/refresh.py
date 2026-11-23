"""Keep message embeddings current, a little at a time.

A small loop in the server process: every ``embedding_refresh_seconds`` it
embeds up to ``embedding_batch_size * embedding_max_batches`` messages that
have no vector yet, newest first, then sleeps. New conversations from the
browser extension become searchable within a couple of minutes, a large
backlog drains over time without spikes, and an idle server makes one cheap
query per interval.

A message is marked ``embedding_skipped`` (never retried) when its
conversation's sensitivity forbids sending it out, or when it has too little
text to be worth a vector. A failed embedding call leaves rows untouched; the
next tick tries again.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from subzero.config import Settings
from subzero.db.pool import Database, vector_literal
from subzero.embeddings.service import EmbeddingService
from subzero.logging_setup import get_logger

logger = get_logger(__name__)

#: Shorter messages ("ok", "thanks") are not worth a vector.
MIN_CHARS = 20
#: What is embedded per message: the conversation title gives a short reply
#: its topic. Long messages are cut; the provider caps input anyway.
MAX_CHARS = 8000

_PENDING_SQL = """
SELECT m.id, m.content, m.role, c.title, c.sensitivity::text AS sensitivity
FROM messages m
JOIN conversations c ON c.id = m.conversation_id
WHERE m.embedding IS NULL AND NOT m.embedding_skipped
ORDER BY m.created_at DESC
LIMIT $1
"""


@dataclass
class RefreshResult:
    embedded: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def touched(self) -> int:
        return self.embedded + self.skipped


def _text(row: dict) -> str:
    title = (row.get("title") or "").strip()
    body = (row.get("content") or "").strip()[:MAX_CHARS]
    return f"{title}\n{row['role']}: {body}" if title else f"{row['role']}: {body}"


async def refresh_once(
    db: Database, embeddings: EmbeddingService, *, batch_size: int
) -> RefreshResult:
    """Embed one batch of pending messages."""
    result = RefreshResult()
    if not embeddings.enabled:
        return result
    rows = [dict(r) for r in await db.fetch(_PENDING_SQL, batch_size)]
    if not rows:
        return result

    skip = [
        r["id"]
        for r in rows
        if not embeddings.allowed(r["sensitivity"]) or len((r["content"] or "").strip()) < MIN_CHARS
    ]
    todo = [r for r in rows if r["id"] not in set(skip)]
    if skip:
        await db.execute(
            "UPDATE messages SET embedding_skipped = true WHERE id = ANY($1::uuid[])", skip
        )
        result.skipped = len(skip)
    if not todo:
        return result

    vectors = await embeddings.embed([_text(r) for r in todo])
    pairs = [(r["id"], vector_literal(v)) for r, v in zip(todo, vectors, strict=True) if v]
    result.failed = len(todo) - len(pairs)
    if pairs:
        async with db.acquire() as conn:
            await conn.executemany(
                "UPDATE messages SET embedding = $2::text::vector WHERE id = $1", pairs
            )
        result.embedded = len(pairs)
    return result


async def refresh_backlog(
    db: Database, embeddings: EmbeddingService, *, batch_size: int, max_batches: int
) -> RefreshResult:
    """Up to max_batches batches; stops early when nothing is pending or a call fails."""
    total = RefreshResult()
    for _ in range(max_batches):
        step = await refresh_once(db, embeddings, batch_size=batch_size)
        total.embedded += step.embedded
        total.skipped += step.skipped
        total.failed += step.failed
        if step.touched == 0 or step.failed:
            break
    return total


async def run_forever(db: Database, embeddings: EmbeddingService, settings: Settings) -> None:
    """The server's background loop. Never raises; cancelled at shutdown."""
    interval = settings.embedding_refresh_seconds
    while True:
        try:
            done = await refresh_backlog(
                db,
                embeddings,
                batch_size=settings.embedding_batch_size,
                max_batches=settings.embedding_max_batches,
            )
            if done.touched or done.failed:
                logger.info(
                    "embeddings refreshed",
                    extra={
                        "embedded": done.embedded,
                        "skipped": done.skipped,
                        "failed": done.failed,
                    },
                )
        except Exception:
            logger.exception("embedding refresh failed; retrying next interval")
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def background_refresh(db: Database, embeddings: EmbeddingService, settings: Settings):  # type: ignore[no-untyped-def]
    """Run the loop for the server's lifetime, when embeddings are on."""
    if not embeddings.enabled or settings.embedding_refresh_seconds <= 0:
        yield
        return
    task = asyncio.create_task(run_forever(db, embeddings, settings), name="embedding-refresh")
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
