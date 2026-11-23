"""Background embedding and semantic retrieval, with a deterministic fake provider."""

from __future__ import annotations

import math

import pytest

from conftest import requires_db
from subzero.config import EMBEDDING_DIM
from subzero.embeddings.base import NullEmbeddingProvider
from subzero.embeddings.refresh import refresh_backlog
from subzero.embeddings.service import EmbeddingService
from subzero.retrieval.semantic import deep_search

pytestmark = requires_db

#: Each topic is one axis: texts sharing a topic word are near each other even
#: when they share no other word, which is what semantic search is for.
TOPICS = {
    "pricing": 0,
    "price": 0,
    "cost": 0,
    "billing": 0,
    "garden": 1,
    "tomato": 1,
    "plants": 1,
    "deploy": 2,
    "server": 2,
    "release": 2,
}


class FakeProvider:
    dimension = EMBEDDING_DIM

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        vectors = []
        for text in texts:
            v = [0.0] * EMBEDDING_DIM
            for word in text.lower().replace(",", " ").split():
                if word in TOPICS:
                    v[TOPICS[word]] += 1.0
            v[100] += 0.05  # never a zero vector
            norm = math.sqrt(sum(x * x for x in v))
            vectors.append([x / norm for x in v])
        return vectors

    async def aclose(self) -> None:
        return None


@pytest.fixture
def fake(services, settings):
    provider = FakeProvider()
    embeddings = EmbeddingService(provider, settings)
    # Every reader of embeddings sees the fake (bootstrap reads it via retrieval).
    services.retrieval._embeddings = embeddings
    return provider, embeddings


async def _conversation(services, cid, title, messages, sensitivity="normal"):
    conv_id, _ = await services.sessions.ensure_conversation(
        provider="claude", provider_conversation_id=cid, title=title, imported=True
    )
    await services.db.execute(
        "UPDATE conversations SET sensitivity = $2::pc_sensitivity WHERE id = $1",
        conv_id,
        sensitivity,
    )
    await services.sessions.add_messages(
        conv_id,
        [
            {"provider_message_id": f"{cid}-{n}", "role": role, "content": text}
            for n, (role, text) in enumerate(messages)
        ],
        provider="claude",
    )
    return conv_id


async def test_refresh_embeds_pending_messages_and_skips_what_it_must(services, fake):
    provider, embeddings = fake
    await _conversation(
        services,
        "c-pricing",
        "API plans",
        [("user", "What pricing tiers should the API have for teams?"), ("assistant", "ok")],
    )
    await _conversation(
        services,
        "c-private",
        "Medical",
        [("user", "Something private about billing that must never leave this machine")],
        sensitivity="highly_sensitive",
    )

    done = await refresh_backlog(services.db, embeddings, batch_size=10, max_batches=3)
    assert (done.embedded, done.skipped, done.failed) == (1, 2, 0)
    # The private message was never sent to the provider.
    sent = " ".join(t for call in provider.calls for t in call)
    assert "private" not in sent

    rows = await services.db.fetch(
        "SELECT provider_message_id, embedding IS NOT NULL AS has_vector, embedding_skipped "
        "FROM messages ORDER BY provider_message_id"
    )
    state = {r["provider_message_id"]: (r["has_vector"], r["embedding_skipped"]) for r in rows}
    assert state == {
        "c-pricing-0": (True, False),
        "c-pricing-1": (False, True),  # "ok": too short
        "c-private-0": (False, True),  # highly_sensitive
    }

    again = await refresh_backlog(services.db, embeddings, batch_size=10, max_batches=3)
    assert again.touched == 0


async def test_deep_search_finds_by_meaning_and_falls_back_to_words(services, fake, settings):
    _, embeddings = fake
    await _conversation(
        services, "c-pricing", "API plans", [("user", "Which pricing tiers suit small teams best?")]
    )
    await _conversation(
        services, "c-garden", "Weekend", [("user", "My tomato plants need a bigger garden bed")]
    )
    await refresh_backlog(services.db, embeddings, batch_size=10, max_batches=2)

    # No shared words with the pricing chat: only meaning connects them.
    result = await deep_search(services.db, embeddings, "how much should it cost")
    assert result.semantic is True
    assert result.conversations[0]["title"] == "API plans"
    assert result.conversations[0]["messages"][0]["excerpt"].startswith("Which pricing tiers")

    # Embeddings off: exact words still work, and the result says so.
    off = EmbeddingService(NullEmbeddingProvider(), settings)
    words = await deep_search(services.db, off, "tomato")
    assert words.semantic is False
    assert words.note and "Full-text only" in words.note
    assert [c["title"] for c in words.conversations] == ["Weekend"]


async def test_bootstrap_adds_a_semantic_layer(services, fake):
    _, embeddings = fake
    await _conversation(
        services, "c-deploy", "Ops", [("user", "The server deploy failed again on the new box")]
    )
    await refresh_backlog(services.db, embeddings, batch_size=10, max_batches=2)

    # "release" is not in the message: only meaning connects them.
    package = await services.retrieval.bootstrap("release")
    layers = {layer.name: layer for layer in package.layers}
    assert layers["semantic"].status == "ran"
    semantic = [i for i in package.items if i.layer == "semantic"]
    assert semantic and semantic[0].title == "Ops"
    assert semantic[0].type == "message"


async def test_bootstrap_without_embeddings_is_unchanged(services):
    package = await services.retrieval.bootstrap("deploy")
    assert "semantic" not in {layer.name for layer in package.layers}
    assert not [i for i in package.items if i.layer == "semantic"]
