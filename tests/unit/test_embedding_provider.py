"""Choosing an embedding provider from settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from subzero.config import Settings
from subzero.embeddings.base import NullEmbeddingProvider
from subzero.embeddings.remote import GeminiEmbeddingProvider, OpenAIEmbeddingProvider
from subzero.embeddings.service import build_embedding_provider


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
