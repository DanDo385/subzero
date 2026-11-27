"""Choosing an embedding provider from settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from subzero.config import Settings
from subzero.embeddings.base import NullEmbeddingProvider
from subzero.embeddings.remote import GeminiEmbeddingProvider, OpenAIEmbeddingProvider
from subzero.embeddings.service import build_embedding_provider
from subzero.errors import EmbeddingInputError, SubzeroError


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("SUBZERO_EMBEDDING_MODEL", "PC_EMBEDDING_MODEL"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("provider", "key_field", "cls", "model"),
    [
        ("openai", "openai_api_key", OpenAIEmbeddingProvider, "text-embedding-3-small"),
        ("gemini", "gemini_api_key", GeminiEmbeddingProvider, "gemini-embedding-001"),
    ],
)
async def test_each_provider_gets_its_own_default_model(provider, key_field, cls, model) -> None:
    settings = Settings(embedding_provider=provider, **{key_field: SecretStr("k")})
    built = build_embedding_provider(settings)
    try:
        assert isinstance(built, cls)
        assert built._model == model
    finally:
        await built.aclose()


async def test_an_explicit_model_wins() -> None:
    settings = Settings(
        embedding_provider="gemini",
        gemini_api_key=SecretStr("k"),
        embedding_model="gemini-embedding-2",
    )
    built = build_embedding_provider(settings)
    try:
        assert built._model == "gemini-embedding-2"  # type: ignore[attr-defined]
    finally:
        await built.aclose()


def test_a_provider_without_a_key_falls_back_to_text_search() -> None:
    assert isinstance(
        build_embedding_provider(Settings(embedding_provider="gemini")), NullEmbeddingProvider
    )


class _RefusingProvider:
    """Refuses any batch, and any single input longer than 40 characters."""

    dimension = 1536

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if len(texts) > 1 or len(texts[0]) > 40:
            raise EmbeddingInputError("Embedding request refused: HTTP 400: too long")
        return [[0.1] * 1536]

    async def aclose(self) -> None:
        return None


async def test_a_refused_batch_is_reasked_row_by_row() -> None:
    """One input over the token limit must not cost the batch its vectors."""
    from subzero.embeddings.service import EmbeddingService

    provider = _RefusingProvider()
    service = EmbeddingService(
        provider, Settings(embedding_provider="openai", openai_api_key=SecretStr("k"))
    )
    try:
        out = await service.embed(["short one", "x" * 200, "short two"])
    finally:
        await service.aclose()
    assert out[0] is not None
    assert out[2] is not None
    # Refused at 200, 100 and 50 chars: no vector, and the caller marks it
    # skipped instead of retrying it forever.
    assert out[1] is None
    assert provider.calls == 4  # one batch plus three shrinking single asks


async def test_a_dead_provider_still_returns_none_for_everything() -> None:
    from subzero.embeddings.service import EmbeddingService

    class _Dead:
        dimension = 1536

        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise SubzeroError("Embedding request failed: HTTP 500")

        async def aclose(self) -> None:
            return None

    service = EmbeddingService(
        _Dead(), Settings(embedding_provider="openai", openai_api_key=SecretStr("k"))
    )
    try:
        assert await service.embed(["a", "b"]) == [None, None]
    finally:
        await service.aclose()
