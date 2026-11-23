"""Keep embeddings current, a little at a time.

A small loop in the server process: every ``embedding_refresh_seconds`` it
embeds up to ``embedding_batch_size * embedding_max_batches`` rows that have no
vector yet, newest first, then sleeps. New conversations from the browser
extension become searchable within a couple of minutes, a large backlog drains
over time without spikes, and an idle server makes one cheap query per interval.

Vectors are derived data, so no write path computes one inline: messages and
notes both store ``embedding`` as null and this loop fills it in. Doing it
inline meant a network call of up to 30 s inside the caller's database
transaction, holding row locks and a pooled connection while a provider was
slow, and it left rows permanently without a vector when the call failed,
because nothing ever came back to them.

A row is marked ``embedding_skipped`` (never retried) when its sensitivity
forbids sending it out, or when a message has too little text to be worth a
vector. A failed embedding call leaves rows untouched, and the loop backs off
exponentally: a provider that is down or out of credit is not worth asking at
full rate forever, and the failure is logged as a warning rather than buried in
the per-tick info line.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from subzero.config import Settings
from subzero.db.lease import TTL_TICKS, hold_lease, release_lease
from subzero.db.pool import Database, vector_literal
from subzero.embeddings.service import EmbeddingService
from subzero.logging_setup import get_logger

logger = get_logger(__name__)

#: Shorter messages ("ok", "thanks") are not worth a vector.
MIN_CHARS = 20
#: What is embedded per row. Long bodies are cut in SQL rather than after the
#: round trip; the provider caps input anyway.
MAX_CHARS = 8000

#: Consecutive all-failed ticks after which the interval starts doubling, the
#: ceiling it doubles to, and when the failure becomes a warning.
BACKOFF_AFTER = 1
MAX_BACKOFF_MULTIPLIER = 30
WARN_AFTER = 3

#: The lease this loop holds, so only one of the servers sharing the database
#: pays the embedding provider for a given row.
LEASE_NAME = "embedding-refresh"


@dataclass(frozen=True)
class _Source:
    """One table whose ``embedding`` column this loop maintains."""

    name: str
    pending_sql: str
    skip_sql: str
    store_sql: str
    text: Callable[[dict[str, Any]], str]
    min_chars: int


def _message_text(row: dict[str, Any]) -> str:
    title = (row.get("title") or "").strip()
    body = (row.get("content") or "").strip()
    return f"{title}\n{row['role']}: {body}" if title else f"{row['role']}: {body}"


def _note_text(row: dict[str, Any]) -> str:
    return f"{(row.get('title') or '').strip()}\n\n{(row.get('content') or '').strip()}"


_SOURCES: tuple[_Source, ...] = (
    _Source(
        name="messages",
        pending_sql="""
            SELECT m.id, left(m.content, $2) AS content, m.role, c.title,
                   c.sensitivity::text AS sensitivity
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.embedding IS NULL AND NOT m.embedding_skipped
            ORDER BY m.created_at DESC
            LIMIT $1
            """,
        skip_sql="UPDATE messages SET embedding_skipped = true WHERE id = ANY($1::uuid[])",
        store_sql="UPDATE messages SET embedding = $2::text::vector WHERE id = $1",
        text=_message_text,
        min_chars=MIN_CHARS,
    ),
    _Source(
        name="notes",
        pending_sql="""
            SELECT id, left(content, $2) AS content, title, sensitivity::text AS sensitivity
            FROM notes
            WHERE embedding IS NULL AND NOT embedding_skipped
            ORDER BY updated_at DESC
            LIMIT $1
            """,
        skip_sql="UPDATE notes SET embedding_skipped = true WHERE id = ANY($1::uuid[])",
        store_sql="UPDATE notes SET embedding = $2::text::vector WHERE id = $1",
        text=_note_text,
        min_chars=0,
    ),
)


@dataclass
class RefreshResult:
    embedded: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def touched(self) -> int:
        return self.embedded + self.skipped


async def _refresh_source(
    db: Database, embeddings: EmbeddingService, source: _Source, *, batch_size: int
) -> RefreshResult:
    result = RefreshResult()
    rows = [dict(r) for r in await db.fetch(source.pending_sql, batch_size, MAX_CHARS)]
    if not rows:
        return result

    skip = [
        r["id"]
        for r in rows
        if not embeddings.allowed(r["sensitivity"])
        or len((r["content"] or "").strip()) < source.min_chars
    ]
    todo = [r for r in rows if r["id"] not in set(skip)]
    if skip:
        await db.execute(source.skip_sql, skip)
        result.skipped = len(skip)
    if not todo:
        return result

    vectors = await embeddings.embed([source.text(r) for r in todo])
    pairs = [(r["id"], vector_literal(v)) for r, v in zip(todo, vectors, strict=True) if v]
    result.failed = len(todo) - len(pairs)
    if pairs:
        async with db.acquire() as conn:
            await conn.executemany(source.store_sql, pairs)
        result.embedded = len(pairs)
    return result


async def refresh_once(
    db: Database, embeddings: EmbeddingService, *, batch_size: int
) -> RefreshResult:
    """Embed one batch of pending rows from every source."""
    total = RefreshResult()
    if not embeddings.enabled:
        return total
    for source in _SOURCES:
        step = await _refresh_source(db, embeddings, source, batch_size=batch_size)
        total.embedded += step.embedded
        total.skipped += step.skipped
        total.failed += step.failed
    return total


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
    failures = 0
    while True:
        try:
            # Somebody else holds the loop this tick: sleep and ask again,
            # rather than embedding the same rows a second time.
            if not await hold_lease(db, LEASE_NAME, ttl_seconds=interval * TTL_TICKS):
                await asyncio.sleep(interval)
                continue
            done = await refresh_backlog(
                db,
                embeddings,
                batch_size=settings.embedding_batch_size,
                max_batches=settings.embedding_max_batches,
            )
            if done.failed and not done.embedded:
                failures += 1
            else:
                failures = 0
            if failures == WARN_AFTER:
                logger.warning(
                    "embedding refresh has failed %d ticks running; backing off. "
                    "Check the provider's key and credit.",
                    failures,
                    extra={"failed": done.failed, "consecutive_failures": failures},
                )
            elif done.touched or done.failed:
                logger.info(
                    "embeddings refreshed",
                    extra={
                        "embedded": done.embedded,
                        "skipped": done.skipped,
                        "failed": done.failed,
                    },
                )
        except Exception:
            failures += 1
            logger.exception("embedding refresh failed; retrying next interval")
        # A tick that produced nothing but failures waits longer each time, up
        # to MAX_BACKOFF_MULTIPLIER intervals, so an outage costs one cheap
        # query an hour rather than one failing batch every two minutes.
        multiplier = (
            min(2 ** (failures - BACKOFF_AFTER), MAX_BACKOFF_MULTIPLIER)
            if failures > BACKOFF_AFTER
            else 1
        )
        await asyncio.sleep(interval * multiplier)


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
        with contextlib.suppress(Exception):
            await release_lease(db, LEASE_NAME)
