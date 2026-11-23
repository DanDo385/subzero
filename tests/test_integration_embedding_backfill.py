"""Memories and session summaries saved while embeddings were off get vectors later.

The refresh loop filled in messages and notes only. A memory or a summary
written before an embedding provider was configured, or while it was out of
credit, stayed without a vector for good, so memory search and the
near-duplicate check could never find it by meaning.
"""

from __future__ import annotations

from conftest import requires_db
from subzero.embeddings.refresh import refresh_backlog
from test_integration_semantic import fake  # noqa: F401  (fixture)

pytestmark = requires_db


async def test_refresh_fills_memories_and_summaries(services, fake) -> None:  # noqa: F811
    provider, embeddings = fake
    # Saved by the services fixture's embedding service, which has no provider.
    kept = await services.memories.save(content="Dan deploys the server from the mbp")
    secret = await services.memories.save(
        content="The safe combination is in the blue folder", sensitivity="highly_sensitive"
    )
    conv_id, _ = await services.sessions.ensure_conversation(
        provider="claude", provider_conversation_id="sum-1", title="Deploy notes"
    )
    await services.db.execute(
        """
        INSERT INTO session_summaries (conversation_id, provider, summary, summary_sha256)
        VALUES ($1, 'claude', 'Moved the release to the mbp server.', 'x')
        """,
        conv_id,
    )

    await refresh_backlog(services.db, embeddings, batch_size=50, max_batches=5)

    memories = {
        r["id"]: r
        for r in await services.db.fetch(
            "SELECT id, embedding IS NOT NULL AS has, embedding_skipped FROM memories"
        )
    }
    assert memories[kept.id]["has"] and not memories[kept.id]["embedding_skipped"]
    assert not memories[secret.id]["has"] and memories[secret.id]["embedding_skipped"]
    assert await services.db.fetchval(
        "SELECT embedding IS NOT NULL FROM session_summaries WHERE conversation_id = $1", conv_id
    )
    sent = [text for call in provider.calls for text in call]
    assert not any("safe combination" in text for text in sent)


async def test_a_batch_larger_than_a_write_chunk_is_written_in_full(
    services,
    fake,
    monkeypatch,  # noqa: F811
) -> None:
    from subzero.embeddings import refresh

    _, embeddings = fake
    monkeypatch.setattr(refresh, "WRITE_CHUNK", 2)
    for n in range(7):
        await services.memories.save(content=f"Distinct fact number {n} about the deploy")

    result = await refresh_backlog(services.db, embeddings, batch_size=50, max_batches=1)

    assert result.embedded == 7
    assert await services.db.fetchval("SELECT count(*) FROM memories WHERE embedding IS NULL") == 0
