"""Embedding service: policy in front of a provider."""

from __future__ import annotations

from subzero.config import Settings
from subzero.embeddings.base import (
    EmbeddingProvider,
    NullEmbeddingProvider,
    redact,
)
from subzero.errors import EmbeddingInputError
from subzero.logging_setup import get_logger

logger = get_logger(__name__)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "openai" and settings.openai_api_key.get_secret_value():
        from subzero.embeddings.remote import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(
            settings.openai_api_key.get_secret_value(),
            settings.embedding_model or OpenAIEmbeddingProvider.default_model,
        )
    if settings.embedding_provider == "gemini" and settings.gemini_api_key.get_secret_value():
        from subzero.embeddings.remote import GeminiEmbeddingProvider

        return GeminiEmbeddingProvider(
            settings.gemini_api_key.get_secret_value(),
            settings.embedding_model or GeminiEmbeddingProvider.default_model,
        )
    if settings.embedding_provider != "none":
        logger.warning(
            "embedding provider configured without an API key; falling back to text search",
            extra={"embedding_provider": settings.embedding_provider},
        )
    return NullEmbeddingProvider()


class EmbeddingService:
    """Embeds text, refusing to send anything the privacy policy forbids."""

    def __init__(self, provider: EmbeddingProvider, settings: Settings) -> None:
        self._provider = provider
        self._settings = settings
        self._blocked = set(settings.no_external_sensitivity)
        self._external = not isinstance(provider, NullEmbeddingProvider)

    @property
    def enabled(self) -> bool:
        return self._external

    def allowed(self, sensitivity: str) -> bool:
        return not (self._external and sensitivity in self._blocked)

    async def embed_one(self, text: str, *, sensitivity: str = "normal") -> list[float] | None:
        results = await self.embed([text], sensitivity=sensitivity)
        return results[0] if results else None

    async def embed(
        self, texts: list[str], *, sensitivity: str = "normal"
    ) -> list[list[float] | None]:
        """Returns one vector per input, or None where embedding was skipped."""
        if not texts or not self._external:
            return [None] * len(texts)
        if not self.allowed(sensitivity):
            logger.info("embedding skipped by privacy policy", extra={"sensitivity": sensitivity})
            return [None] * len(texts)

        cleaned = [redact(t) for t in texts]
        try:
            vectors = await self._provider.embed(cleaned)
        except EmbeddingInputError:
            # One input the provider will not embed refuses the whole batch.
            # Re-ask row by row, shrinking each refusal, so the good rows still
            # get vectors and only a row refused at every length comes back
            # None. Without this a single long message wedges the queue that
            # feeds it, because the row never leaves the pending set.
            vectors = [await self._embed_shrinking(text) for text in cleaned]
        except Exception:
            # A dead embedding vendor must not break memory_save. Full-text
            # search still works and a backfill can populate the column later.
            logger.exception("embedding failed; continuing without vectors")
            return [None] * len(texts)
        return [v or None for v in vectors]

    async def _embed_shrinking(self, text: str) -> list[float] | None:
        cut = len(text)
        while cut > 0:
            try:
                one = await self._provider.embed([text[:cut]])
            except EmbeddingInputError:
                cut //= 2
                continue
            except Exception:
                logger.exception("embedding failed; continuing without vectors")
                return None
            if one:
                return one[0]
            return None
        logger.warning(
            "embedding provider refused this input at every length; no vector",
            extra={"chars": len(text)},
        )
        return None

    async def aclose(self) -> None:
        await self._provider.aclose()
