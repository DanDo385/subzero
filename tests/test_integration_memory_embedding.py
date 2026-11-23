"""A memory is embedded once, and never while a transaction is open.

``memories.save`` used to call the embedding provider from inside the
checkpoint transaction, holding row locks and a pooled connection across a
network call, and it embedded a new memory twice: once to look for a near
duplicate and again to store the vector.
"""

from __future__ import annotations

from conftest import requires_db
from subzero.auth.principal import Principal
from subzero.config import EMBEDDING_DIM
from subzero.embeddings.service import EmbeddingService
from subzero.models import CheckpointDecision, CheckpointFact

pytestmark = requires_db

ME = Principal(subject="tester@example.com", provider="claude")


class WatchingProvider:
    """Records each call, and whether a transaction was open when it came."""

    dimension = EMBEDDING_DIM

    def __init__(self, db) -> None:
        self._db = db
        self.texts: list[str] = []
        self.open_transactions: list[int] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.texts.extend(texts)
        self.open_transactions.append(
            await self._db.fetchval(
                """
                SELECT count(*) FROM pg_stat_activity
                WHERE datname = current_database() AND state = 'idle in transaction'
                """
            )
        )
        # Each distinct text gets its own axis, so no two are near duplicates.
        vectors = []
        for text in texts:
            vector = [0.0] * EMBEDDING_DIM
            vector[sum(text.encode()) % EMBEDDING_DIM] = 1.0
            vectors.append(vector)
        return vectors

    async def aclose(self) -> None:
        return None


def _watch(services, settings) -> WatchingProvider:
    provider = WatchingProvider(services.db)
    embeddings = EmbeddingService(provider, settings)
    services.memories._embeddings = embeddings
    services.sessions._embeddings = embeddings
    return provider


async def test_a_new_memory_is_embedded_once(services, settings) -> None:
    provider = _watch(services, settings)

    result = await services.memories.save(content="The vault helper owns its state file")

    assert result.outcome == "created"
    assert provider.texts == ["The vault helper owns its state file"]


async def test_a_checkpoint_embeds_its_memories_outside_the_transaction(services, settings) -> None:
    provider = _watch(services, settings)

    await services.sessions.checkpoint(
        provider="claude",
        principal=ME,
        provider_conversation_id="embed-1",
        title="Vault helper state",
        summary="Decided where the helper keeps its record of what it wrote.",
        decisions=[CheckpointDecision(statement="The helper keeps a state file")],
        facts=[CheckpointFact(content="Dan runs the helper from a timer on Linux")],
    )

    assert provider.texts, "the fake provider was never called"
    assert provider.open_transactions == [0] * len(provider.open_transactions)
    stored = await services.db.fetchval("SELECT count(*) FROM memories WHERE embedding IS NOT NULL")
    assert stored == 2
